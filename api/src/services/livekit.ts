import { AccessToken } from 'livekit-server-sdk';
import 'dotenv/config';

const apiKey = process.env.LIVEKIT_API_KEY;
const apiSecret = process.env.LIVEKIT_API_SECRET;

if (!apiKey || !apiSecret) {
  throw new Error('Missing LiveKit environment variables: LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be defined.');
}

/**
 * Mints a secure WebRTC participant token for a given session room.
 * Participant identity = user_id
 * Room name = session_id
 * TTL = 60 minutes (1 hour)
 */
export async function createLiveKitToken(roomName: string, participantIdentity: string): Promise<string> {
  const token = new AccessToken(apiKey, apiSecret, {
    identity: participantIdentity,
    ttl: '1h' // 60 minutes
  });

  token.addGrant({
    roomJoin: true,
    room: roomName,
    canPublish: true,
    canSubscribe: true
  });

  return await token.toJwt();
}
