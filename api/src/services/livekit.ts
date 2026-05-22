import { AccessToken } from 'livekit-server-sdk';
import { supabaseAdmin } from '../db/supabase';
import { spawn } from 'child_process';
import path from 'path';
import fs from 'fs';
import { redis } from './redis';
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
  const token = new AccessToken(apiKey!, apiSecret!, {
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

/**
 * Removes the tutor agent participant from a LiveKit room, if present.
 * Used to ensure pause/resume spawns a fresh agent without duplication.
 */
export async function removeAgentParticipant(session_id: string): Promise<boolean> {
  try {
    const { RoomServiceClient } = require('livekit-server-sdk');
    const svc = new RoomServiceClient(
      process.env.LIVEKIT_URL!,
      apiKey!,
      apiSecret!
    );
    const participants = await svc.listParticipants(session_id);
    const hasAgent = participants.some((p: any) => p.identity === 'agent');
    if (!hasAgent) {
      return false;
    }
    await svc.removeParticipant(session_id, 'agent');
    console.log(`[Agent Remove] Removed agent participant from room ${session_id}`);
    return true;
  } catch (err) {
    console.warn(`[Agent Remove] Failed to remove agent from room ${session_id}:`, err);
    return false;
  }
}

/**
 * Spawns the background Pipecat Python voice agent process for a session.
 * Features an atomic Redis-backed SETNX lock with 60s TTL to prevent concurrent spawns.
 * Lock is allowed to naturally expire after 60s on success (acts as debounce),
 * and is deleted immediately on pre-spawn failures to allow immediate retries.
 */
export async function spawnAgent(session_id: string): Promise<boolean> {
  const lockKey = `agent_spawn:${session_id}`;

  // 1. Acquire atomic SETNX lock to prevent simultaneous spawning races (webhook / resume / cluster nodes)
  const acquired = await redis.set(lockKey, 'locked', { nx: true, ex: 60 });
  if (!acquired) {
    console.log(`[Agent Spawn] Concurrency lock active for room: ${session_id}. Skipping spawn.`);
    return false;
  }

  try {
    // 2. Query LiveKit room to verify if an agent is already in the room (prevent double joins)
    let hasAgent = false;
    try {
      const { RoomServiceClient } = require('livekit-server-sdk');
      const svc = new RoomServiceClient(
        process.env.LIVEKIT_URL!,
        apiKey!,
        apiSecret!
      );
      const participants = await svc.listParticipants(session_id);
      hasAgent = participants.some((p: any) => p.identity === 'agent');
    } catch (err) {
      console.warn(`[Agent Spawn] Warning checking participants in room ${session_id}:`, err);
    }

    if (hasAgent) {
      console.log(`[Agent Spawn] Room ${session_id} already has an active agent participant. Skipping spawn.`);
      return false;
    }

    // 3. Fetch session to identify scenario_id and user_id
    const { data: session, error: sessionError } = await supabaseAdmin
      .from('sessions')
      .select('user_id, scenario_id, status')
      .eq('id', session_id)
      .single();

    if (sessionError || !session) {
      console.error(`[Agent Spawn] Session ${session_id} not found:`, sessionError);
      throw new Error(`Session ${session_id} not found in database.`);
    }

    // 4. Fetch the scenario system prompt
    const { data: scenario, error: scenarioError } = await supabaseAdmin
      .from('scenarios')
      .select('system_prompt')
      .eq('id', session.scenario_id)
      .single();

    if (scenarioError || !scenario) {
      console.error(`[Agent Spawn] Scenario ${session.scenario_id} not found:`, scenarioError);
      throw new Error(`Scenario ${session.scenario_id} prompt not found in database.`);
    }

    // 5. Fetch lesson state checkpoint (lightweight resume continuity)
    let lessonStateInstruction = "";
    try {
      const { data: lessonState } = await supabaseAdmin
        .from('lesson_states')
        .select('introduced_items, last_item, pause_at, interrupted_turn_text')
        .eq('session_id', session_id)
        .single();

      if (lessonState) {
        const serialized = JSON.stringify(lessonState);
        lessonStateInstruction = `\n\n[LESSON_STATE]\n${serialized}`;
      }
    } catch (stateErr) {
      console.warn(`[Agent Spawn] Failed to load lesson state for session ${session_id}:`, stateErr);
    }

    // 6. Fetch the last 3 memories for this user to compile the Personalized Learner Profile
    const { data: pastMemories } = await supabaseAdmin
      .from('memories')
      .select('scenario_title, summary, grammar_insights, vocabulary_learned, key_takeaways')
      .eq('user_id', session.user_id)
      .order('created_at', { ascending: false })
      .limit(3);

    let memoryInstruction = "";
    if (pastMemories && pastMemories.length > 0) {
      const weakGrammar = new Set<string>();
      const wordsPracticed = new Set<string>();
      const topTips: string[] = [];

      pastMemories.forEach(mem => {
        if (Array.isArray(mem.grammar_insights)) {
          mem.grammar_insights
            .filter((g: any) => g.status === "Needs Work")
            .forEach((g: any) => weakGrammar.add(g.topic));
        }
        if (Array.isArray(mem.vocabulary_learned)) {
          mem.vocabulary_learned.forEach((v: any) => wordsPracticed.add(v.spanish));
        }
        if (Array.isArray(mem.key_takeaways)) {
          mem.key_takeaways.forEach((t: string) => topTips.push(t));
        }
      });

      memoryInstruction = `\n\n[LEARNER PERSONALIZATION PROFILE]
- Weak Grammar Areas to Target: ${Array.from(weakGrammar).join(', ') || 'None flagged'}
- Vocabulary Previously Practiced: ${Array.from(wordsPracticed).slice(0, 10).join(', ')}
- Pedagogical Recommendations: ${topTips.slice(0, 3).join('; ')}
- Personalization Guideline: Gently weave in previously practiced vocabulary and test them on their weak grammar topics. Maintain the scenario setting.`;
    }

    const finalSystemPrompt = `${scenario.system_prompt}${lessonStateInstruction}${memoryInstruction}`;

    // 7. Build absolute paths for spawning python agent conversational pipeline process
    const agentPath = process.env.AGENT_PATH
      ? path.resolve(process.env.AGENT_PATH, 'pipeline.py')
      : path.resolve(__dirname, '../../../agent/pipeline.py');

    const pythonBinary = process.env.PYTHON_BINARY || 'python3';

    console.log(`[Agent Spawn] Spawning pipeline agent process...`);
    console.log(`  Binary: ${pythonBinary}`);
    console.log(`  Script: ${agentPath}`);
    console.log(`  Session ID: ${session_id}`);

    const logDir = process.env.AGENT_PATH
      ? path.resolve(process.env.AGENT_PATH)
      : path.resolve(__dirname, '../../../agent');
    const logFilePath = path.join(logDir, 'agent_runtime.log');
    const logFd = fs.openSync(logFilePath, 'a');

    // Use environment variable AGENT_SYSTEM_PROMPT to transfer prompt cleanly (shell injection proof)
    const child = spawn(pythonBinary, [agentPath, session_id], {
      detached: true,
      env: {
        ...process.env,
        AGENT_SYSTEM_PROMPT: finalSystemPrompt
      },
      stdio: ['ignore', logFd, logFd]
    });
    child.unref();

    console.log(`[Agent Spawn] Detached pipeline agent process successfully spawned for session: ${session_id}`);
    console.log(`  Agent runtime logs appended to: ${logFilePath}`);
    return true;
  } catch (err: any) {
    console.error(`[Agent Spawn] Hard pre-spawn failure for session ${session_id}:`, err);
    // Hard pre-spawn failure: release the lock immediately to allow developer/webhook retry
    try {
      await redis.del(lockKey);
      console.log(`[Agent Spawn] Released lock for room: ${session_id} immediately on failure.`);
    } catch (redisErr) {
      console.error(`[Agent Spawn] Failed to release lock on error:`, redisErr);
    }
    return false;
  }
}
