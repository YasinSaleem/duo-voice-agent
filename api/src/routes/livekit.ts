import { Router, Request, Response } from 'express';
import { WebhookReceiver } from 'livekit-server-sdk';
import { supabaseAdmin } from '../db/supabase';
import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';


const router = Router();

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
    console.log(`[LiveKit Webhook] Successfully verified event signature: ${event.event}`);

    // 2. Act exclusively on room_started events
    if (event.event === 'room_started') {
      const room = event.room;
      if (!room || !room.name) {
        console.error('[LiveKit Webhook] room_started event lacks room or name metadata');
        return res.status(400).json({
          error: {
            code: 'BAD_REQUEST',
            message: 'Invalid room metadata.'
          }
        });
      }

      const session_id = room.name;
      console.log(`[LiveKit Webhook] Room started: ${session_id}. Retrieving session scenario...`);

      // 3. Fetch session to identify scenario_id
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

      // 4. Fetch the scenario system prompt
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

      // 5. Build absolute path to agent conversational pipeline script
      const agentPath = process.env.AGENT_PATH
        ? path.resolve(process.env.AGENT_PATH, 'pipeline.py')
        : path.resolve(__dirname, '../../../agent/pipeline.py');

      const pythonBinary = process.env.PYTHON_BINARY || 'python3';

      console.log(`[LiveKit Webhook] Spawning pipeline agent process...`);
      console.log(`  Binary: ${pythonBinary}`);
      console.log(`  Script: ${agentPath}`);
      console.log(`  Session ID: ${session_id}`);

      // 6. Spawn the detached pipeline agent process in background
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
