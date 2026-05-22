import { Router, Request, Response } from 'express';
import { WebhookReceiver } from 'livekit-server-sdk';
import { spawnAgent } from '../services/livekit';

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

      console.log(`[LiveKit Webhook] Event ${event.event} triggered checking/spawning for session: ${session_id}`);
      await spawnAgent(session_id);
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
