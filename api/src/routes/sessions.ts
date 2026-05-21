import { Router, Request, Response } from 'express';
import { authMiddleware } from '../middleware/auth';
import { AuthenticatedRequest } from '../types';
import { supabaseAdmin } from '../db/supabase';
import { getTurnsCollection } from '../db/mongo';
import { createLiveKitToken } from '../services/livekit';
import { cacheResumeTurns } from '../services/redis';

const router = Router();

// Generic UUID validation regex
const uuidRegex = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function isValidUuid(uuid: string): boolean {
  return uuidRegex.test(uuid);
}

// Standardized error sender helper
function sendError(res: Response, status: number, code: string, message: string) {
  return res.status(status).json({
    error: {
      code,
      message
    }
  });
}

// Apply authentication middleware to all protected session routes
router.use(authMiddleware);

/**
 * Route 0: GET /v1/sessions/scenarios
 * Purpose: Fetch all active scenarios for scenario selection
 */
router.get('/scenarios', async (req: Request, res: Response): Promise<any> => {
  try {
    const { data: scenarios, error } = await supabaseAdmin
      .from('scenarios')
      .select('*')
      .order('title', { ascending: true });

    if (error) {
      console.error('[Sessions] Error fetching scenarios:', error);
      return sendError(res, 500, 'DATABASE_ERROR', 'Failed to retrieve scenarios.');
    }

    return res.status(200).json(scenarios);
  } catch (err: any) {
    console.error('[Sessions] Unexpected error in GET /scenarios:', err);
    return sendError(res, 500, 'INTERNAL_SERVER_ERROR', err.message || 'An unexpected error occurred.');
  }
});

/**
 * Route 1: POST /v1/sessions
 * Purpose: Start a new session
 */
router.post('/', async (req: Request, res: Response): Promise<any> => {
  const authReq = req as AuthenticatedRequest;
  const { scenario_id } = authReq.body;

  if (!scenario_id) {
    return sendError(res, 400, 'INVALID_INPUT', 'Missing required parameter: scenario_id.');
  }

  if (!isValidUuid(scenario_id)) {
    return sendError(res, 400, 'INVALID_INPUT', 'Provided scenario_id is not a valid UUID.');
  }

  try {
    // 1. Verify scenario exists
    const { data: scenario, error: scenarioError } = await supabaseAdmin
      .from('scenarios')
      .select('id')
      .eq('id', scenario_id)
      .single();

    if (scenarioError || !scenario) {
      return sendError(res, 404, 'SCENARIO_NOT_FOUND', 'The specified scenario does not exist.');
    }

    // 2. Insert new active session
    const { data: session, error: sessionError } = await supabaseAdmin
      .from('sessions')
      .insert({
        user_id: authReq.user.id,
        scenario_id: scenario_id,
        status: 'active'
      })
      .select('id')
      .single();

    if (sessionError || !session) {
      console.error('[Sessions] Database insertion error:', sessionError);
      return sendError(res, 500, 'DATABASE_ERROR', 'Failed to create a new session.');
    }

    // 3. Mint WebRTC token
    const token = await createLiveKitToken(session.id, authReq.user.id);

    return res.status(201).json({
      session_id: session.id,
      livekit_url: process.env.LIVEKIT_URL,
      livekit_token: token
    });
  } catch (err: any) {
    console.error('[Sessions] Unexpected error in POST /:', err);
    return sendError(res, 500, 'INTERNAL_SERVER_ERROR', err.message || 'An unexpected error occurred.');
  }
});

/**
 * Route 2: POST /v1/sessions/:session_id/resume
 * Purpose: Resume a paused session (Sequential Resume Flow)
 */
router.post('/:session_id/resume', async (req: Request, res: Response): Promise<any> => {
  const authReq = req as AuthenticatedRequest;
  const session_id = authReq.params.session_id as string;

  if (!isValidUuid(session_id)) {
    return sendError(res, 400, 'INVALID_INPUT', 'Provided session_id parameter is not a valid UUID.');
  }

  try {
    // 1. Validate eligibility (existence, ownership, and that it is paused)
    const { data: session, error: fetchError } = await supabaseAdmin
      .from('sessions')
      .select('user_id, status')
      .eq('id', session_id)
      .single();

    if (fetchError || !session) {
      return sendError(res, 404, 'SESSION_NOT_FOUND', 'Session not found.');
    }

    if (session.user_id !== authReq.user.id) {
      return sendError(res, 403, 'FORBIDDEN', 'You do not have access to this session.');
    }

    if (session.status === 'active') {
      return sendError(res, 400, 'ALREADY_ACTIVE', 'Session is already active.');
    }

    if (session.status === 'completed') {
      return sendError(res, 400, 'SESSION_COMPLETED', 'Session is completed and cannot be resumed.');
    }

    // 2. Fetch the last 10 turns from MongoDB sorted ascending by timestamp
    const turnsCollection = await getTurnsCollection();
    const rawTurns = await turnsCollection
      .find({ session_id })
      .sort({ timestamp: 1 })
      .limit(10)
      .toArray();

    const turns = rawTurns.map(t => ({
      role: t.role,
      transcript: t.transcript,
      timestamp: t.timestamp
    }));

    // 3. Cache the serialized turns array in Redis with a 300s TTL
    await cacheResumeTurns(session_id, turns);

    // 4. Perform atomic status transition (paused -> active)
    const { data: updatedSessions, error: updateError } = await supabaseAdmin
      .from('sessions')
      .update({ status: 'active' })
      .eq('id', session_id)
      .eq('user_id', authReq.user.id)
      .eq('status', 'paused')
      .select();

    if (updateError || !updatedSessions || updatedSessions.length === 0) {
      return sendError(res, 400, 'ALREADY_ACTIVE', 'Session activation failed. Session may have been activated concurrently.');
    }

    // 5. Mint LiveKit WebRTC room token
    const token = await createLiveKitToken(session_id, authReq.user.id);

    return res.status(200).json({
      livekit_url: process.env.LIVEKIT_URL,
      livekit_token: token
    });
  } catch (err: any) {
    console.error('[Sessions] Unexpected error in POST /:session_id/resume:', err);
    return sendError(res, 500, 'INTERNAL_SERVER_ERROR', err.message || 'An unexpected error occurred.');
  }
});

/**
 * Route 3: GET /v1/sessions/:session_id/feedback
 * Purpose: Return post-session correction turns (Unpaginated for MVP)
 */
router.get('/:session_id/feedback', async (req: Request, res: Response): Promise<any> => {
  const authReq = req as AuthenticatedRequest;
  const session_id = authReq.params.session_id as string;

  if (!isValidUuid(session_id)) {
    return sendError(res, 400, 'INVALID_INPUT', 'Provided session_id parameter is not a valid UUID.');
  }

  try {
    // 1. Verify session ownership
    const { data: session, error: fetchError } = await supabaseAdmin
      .from('sessions')
      .select('user_id')
      .eq('id', session_id)
      .single();

    if (fetchError || !session) {
      return sendError(res, 404, 'SESSION_NOT_FOUND', 'Session not found.');
    }

    if (session.user_id !== authReq.user.id) {
      return sendError(res, 403, 'FORBIDDEN', 'You do not have access to this session.');
    }

    // 2. Fetch turns from MongoDB sorted ascending by timestamp
    const turnsCollection = await getTurnsCollection();
    const rawTurns = await turnsCollection
      .find({ session_id })
      .sort({ timestamp: 1 })
      .toArray();

    const turns = rawTurns.map(t => ({
      role: t.role,
      transcript: t.transcript,
      corrections: t.corrections || null
    }));

    return res.status(200).json(turns);
  } catch (err: any) {
    console.error('[Sessions] Unexpected error in GET /:session_id/feedback:', err);
    return sendError(res, 500, 'INTERNAL_SERVER_ERROR', err.message || 'An unexpected error occurred.');
  }
});

/**
 * Route 4: PATCH /v1/sessions/:session_id/status
 * Purpose: Pause or complete a session
 */
router.patch('/:session_id/status', async (req: Request, res: Response): Promise<any> => {
  const authReq = req as AuthenticatedRequest;
  const session_id = authReq.params.session_id as string;
  const { status } = authReq.body;

  if (!isValidUuid(session_id)) {
    return sendError(res, 400, 'INVALID_INPUT', 'Provided session_id parameter is not a valid UUID.');
  }

  if (status !== 'paused' && status !== 'completed') {
    return sendError(res, 400, 'INVALID_INPUT', 'Invalid status. Status must be either "paused" or "completed".');
  }

  try {
    // 1. Fetch current status to check transition constraints
    const { data: session, error: fetchError } = await supabaseAdmin
      .from('sessions')
      .select('user_id, status')
      .eq('id', session_id)
      .single();

    if (fetchError || !session) {
      return sendError(res, 404, 'SESSION_NOT_FOUND', 'Session not found.');
    }

    if (session.user_id !== authReq.user.id) {
      return sendError(res, 403, 'FORBIDDEN', 'You do not have access to this session.');
    }

    // Reject no-op transitions
    if (session.status === status) {
      return sendError(res, 400, 'INVALID_TRANSITION', `Session is already in "${status}" state.`);
    }

    // Reject any mutation out of completed
    if (session.status === 'completed') {
      return sendError(res, 400, 'INVALID_TRANSITION', 'Completed sessions are immutable and cannot be updated.');
    }

    // 2. Perform atomic update in Supabase
    const { data: updatedSessions, error: updateError } = await supabaseAdmin
      .from('sessions')
      .update({ status })
      .eq('id', session_id)
      .eq('user_id', authReq.user.id)
      .select();

    if (updateError || !updatedSessions || updatedSessions.length === 0) {
      return sendError(res, 500, 'DATABASE_ERROR', 'Failed to update session status.');
    }

    return res.status(200).json({ ok: true });
  } catch (err: any) {
    console.error('[Sessions] Unexpected error in PATCH /:session_id/status:', err);
    return sendError(res, 500, 'INTERNAL_SERVER_ERROR', err.message || 'An unexpected error occurred.');
  }
});

export default router;
