import { Router, Request, Response } from 'express';
import { WebhookReceiver, RoomServiceClient } from 'livekit-server-sdk';
import { supabaseAdmin } from '../db/supabase';
import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';

const router = Router();

// In-memory cache to prevent race-condition concurrent agent spawns within 60s
const spawnedRooms = new Set<string>();

// Create WebhookReceiver instance using credentials from environment
const receiver = new WebhookReceiver(
  process.env.LIVEKIT_API_KEY!,
  process.env.LIVEKIT_API_SECRET!
);

/**
 * POST /internal/livekit/webhook
 * Handshake webhook triggered when LiveKit rooms are created.
 */
router.post('/webhook', async (req: Request, res: Response): Promise<any> => {
  const authHeader = req.headers.authorization;
  
  if (!authHeader) {
    console.error('[LiveKit Webhook] Missing Authorization header');
    return res.status(401).json({
      error: {
        code: 'UNAUTHORIZED',
        message: 'Missing Authorization header.'
      }
    });
  }

  // Retrieve raw body buffer captured by index.ts middleware
  const rawBody = (req as any).rawBody;
  if (!rawBody) {
    console.error('[LiveKit Webhook] Missing raw body for signature verification');
    return res.status(400).json({
      error: {
        code: 'BAD_REQUEST',
        message: 'Missing raw body (required for cryptographic verification).'
      }
    });
  }

  try {
    // 1. Verify that the event is authentic and from LiveKit
    const event = await receiver.receive(rawBody.toString('utf8'), authHeader);
    const room = event.room;
    if (!room || !room.name) {
      console.error(`[LiveKit Webhook] Webhook event ${event.event} lacks room or name metadata`);
      return res.status(400).json({
        error: {
          code: 'BAD_REQUEST',
          message: 'Invalid room metadata.'
        }
      });
    }

    const session_id = room.name;

    // Process room_started or participant_joined to spawn agent if missing
    if (event.event === 'room_started' || event.event === 'participant_joined') {
      // 1. If participant_joined is from the agent itself, skip
      if (event.event === 'participant_joined' && event.participant?.identity === 'agent') {
        return res.status(200).json({ ok: true });
      }

      // 2. Check if we recently spawned an agent for this room to avoid race conditions
      if (spawnedRooms.has(session_id)) {
        console.log(`[LiveKit Webhook] Agent recently spawned for room: ${session_id}. Skipping.`);
        return res.status(200).json({ ok: true });
      }

      // 3. Query LiveKit server to check if an agent is already in the room
      let hasAgent = false;
      try {
        const svc = new RoomServiceClient(
          process.env.LIVEKIT_URL!,
          process.env.LIVEKIT_API_KEY!,
          process.env.LIVEKIT_API_SECRET!
        );
        const participants = await svc.listParticipants(session_id);
        hasAgent = participants.some(p => p.identity === 'agent');
      } catch (err) {
        console.warn(`[LiveKit Webhook] Error checking participants in room ${session_id}:`, err);
      }

      if (hasAgent) {
        console.log(`[LiveKit Webhook] Room ${session_id} already has an active agent participant. Skipping spawn.`);
        return res.status(200).json({ ok: true });
      }

      console.log(`[LiveKit Webhook] Spawning agent for room: ${session_id} triggered by event: ${event.event}`);

      // Add to spawning cache for 60 seconds
      spawnedRooms.add(session_id);
      setTimeout(() => {
        spawnedRooms.delete(session_id);
      }, 60000);

      // Fetch session to identify scenario_id
      const { data: session, error: sessionError } = await supabaseAdmin
        .from('sessions')
        .select('scenario_id, status')
        .eq('id', session_id)
        .single();

      if (sessionError || !session) {
        console.error(`[LiveKit Webhook] Session ${session_id} not found:`, sessionError);
        return res.status(404).json({
          error: {
            code: 'SESSION_NOT_FOUND',
            message: 'Specified session was not found.'
          }
        });
      }

      // Fetch the scenario system prompt
      const { data: scenario, error: scenarioError } = await supabaseAdmin
        .from('scenarios')
        .select('system_prompt')
        .eq('id', session.scenario_id)
        .single();

      if (scenarioError || !scenario) {
        console.error(`[LiveKit Webhook] Scenario ${session.scenario_id} not found:`, scenarioError);
        return res.status(404).json({
          error: {
            code: 'SCENARIO_NOT_FOUND',
            message: 'Scenario system prompt not found.'
          }
        });
      }

      // Build absolute path to agent conversational pipeline script
      const agentPath = process.env.AGENT_PATH
        ? path.resolve(process.env.AGENT_PATH, 'pipeline.py')
        : path.resolve(__dirname, '../../../agent/pipeline.py');

      const pythonBinary = process.env.PYTHON_BINARY || 'python3';

      console.log(`[LiveKit Webhook] Spawning pipeline agent process...`);
      console.log(`  Binary: ${pythonBinary}`);
      console.log(`  Script: ${agentPath}`);
      console.log(`  Session ID: ${session_id}`);

      // Spawn the detached pipeline agent process in background
      // Redirect stdout and stderr to a log file for transparent observability
      const logDir = process.env.AGENT_PATH
        ? path.resolve(process.env.AGENT_PATH)
        : path.resolve(__dirname, '../../../agent');
      const logFilePath = path.join(logDir, 'agent_runtime.log');
      const logFd = fs.openSync(logFilePath, 'a');

      const child = spawn(pythonBinary, [agentPath, session_id, scenario.system_prompt], {
        detached: true,
        stdio: ['ignore', logFd, logFd]
      });
      child.unref();

      console.log(`[LiveKit Webhook] Detached pipeline agent process spawned for session: ${session_id}`);
      console.log(`  Agent runtime logs appended to: ${logFilePath}`);
    }

    return res.status(200).json({ ok: true });
  } catch (error: any) {
    console.error('[LiveKit Webhook] Error processing event:', error);
    return res.status(400).json({
      error: {
        code: 'WEBHOOK_FAILED',
        message: `Webhook validation or execution failed: ${error.message}`
      }
    });
  }
});

export default router;
