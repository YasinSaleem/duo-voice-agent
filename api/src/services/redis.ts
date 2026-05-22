import { Redis } from '@upstash/redis';
import 'dotenv/config';

const redisUrl = process.env.UPSTASH_REDIS_REST_URL;
const redisToken = process.env.UPSTASH_REDIS_REST_TOKEN;

if (!redisUrl || !redisToken) {
  throw new Error('Missing Upstash Redis environment variables: UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN must be defined.');
}

export const redis = new Redis({
  url: redisUrl,
  token: redisToken
});

/**
 * Enqueues a grammar check job to the tail of the 'grammar_jobs' list.
 * Using RPUSH ensures First-In, First-Out (FIFO) queue semantics
 * when consumed from the head via BLPOP/LPOP.
 */
export async function enqueueGrammarJob(sessionId: string, turnId: string): Promise<void> {
  const payload = JSON.stringify({ sessionId, turnId });
  await redis.rpush('grammar_jobs', payload);
}

/**
 * Caches up to the last 10 historical turns of a session in Redis
 * under key 'resume:<session_id>' with a 300-second TTL.
 */
export async function cacheResumeTurns(sessionId: string, turns: any[]): Promise<void> {
  const key = `resume:${sessionId}`;
  const payload = JSON.stringify(turns);
  // Set value with an explicit TTL of 300 seconds (5 minutes)
  await redis.set(key, payload, { ex: 300 });
}

/**
 * Enqueues a long-term memory compression job to the tail of the 'memory_jobs' list.
 */
export async function enqueueMemoryJob(sessionId: string, userId: string): Promise<void> {
  const payload = JSON.stringify({ sessionId, userId });
  await redis.rpush('memory_jobs', payload);
}

export async function getAgentPid(sessionId: string): Promise<number | null> {
  const raw = await redis.get(`agent_pid:${sessionId}`);
  if (!raw) return null;
  const parsed = parseInt(String(raw), 10);
  return Number.isNaN(parsed) ? null : parsed;
}

export async function clearAgentPid(sessionId: string): Promise<void> {
  await redis.del(`agent_pid:${sessionId}`);
}

export async function getAgentSpeakingText(sessionId: string): Promise<string | null> {
  const speaking = await redis.get(`agent_speaking:${sessionId}`);
  if (!speaking) return null;
  const text = await redis.get(`agent_speaking_text:${sessionId}`);
  return text ? String(text) : null;
}
