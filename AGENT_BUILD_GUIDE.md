# AGENT BUILD GUIDE — Voice AI Spanish Language Tutor MVP

> **READ THIS FIRST.**
> This file is the single source of truth for what you must build. Follow the steps in order. Do not build anything not explicitly described here. When a step says "USER ACTION REQUIRED", stop and wait — you cannot proceed until those values exist.

---

## 0. What You Are Building

A voice-to-voice Spanish language tutor with:
- Real-time bilingual conversation (English ↔ Spanish) via WebRTC
- Sub-second response latency
- Session pause/resume with memory injection
- Post-session grammar correction feedback

**Two deployable services:**
1. `api/` — Node.js/Express REST API (auth, session management, LiveKit token minting)
2. `agent/` — Python Pipecat agent worker (VAD → ASR → LLM → TTS pipeline)

**Do not build:**
- Avatars, video, or any visual media streams
- Gamification (streaks, XP, leaderboards)
- Dynamic scenario creation UI
- OAuth providers beyond email/password (Supabase Auth handles this)
- Frontend/UI of any kind — this guide is backend/agent only

---

## 1. Repository Structure

Create exactly this layout. Do not add extra directories.

```
/
├── api/
│   ├── src/
│   │   ├── routes/
│   │   │   ├── sessions.ts      # POST/GET/PATCH session routes
│   │   │   └── livekit.ts       # POST /internal/livekit/webhook
│   │   ├── middleware/
│   │   │   └── auth.ts
│   │   ├── db/
│   │   │   ├── supabase.ts
│   │   │   └── mongo.ts
│   │   ├── services/
│   │   │   ├── livekit.ts
│   │   │   └── redis.ts
│   │   └── index.ts
│   ├── package.json
│   ├── tsconfig.json
│   └── .env.example
│
├── agent/
│   ├── pipeline.py          # Pipecat pipeline definition
│   ├── context_loader.py    # Fetches prior turns and builds LLM context
│   ├── grammar_worker.py    # Background correction processor
│   ├── requirements.txt
│   └── .env.example
│
├── supabase/
│   └── migrations/
│       └── 001_init.sql     # All schema DDL
│
└── AGENT_BUILD_GUIDE.md     # This file
```

---

## 2. Environment Variables

### 2.1 USER ACTION REQUIRED — Provision These Services

Before any code runs, the user must create accounts and retrieve keys for each service below. The agent cannot do this.

| Service | What to do | Where to get it |
|---|---|---|
| **Supabase** | Create a new project | supabase.com → Project Settings → API |
| **MongoDB Atlas** | Create a free cluster, create DB `language_tutor`, collection `turns` | atlas.mongodb.com → Connect → Drivers |
| **LiveKit Cloud** | Create a project | cloud.livekit.io → Settings → Keys |
| **Deepgram** | Create a project, enable Spanish (`es`) and English (`en-US`) | console.deepgram.com → API Keys |
| **Groq** | Create an account | console.groq.com → API Keys (models: llama-3.1-8b-instant for live conversation and model="qwen/qwen3-32b" as grammar worker) |
| **Upstash Redis** | Create a Redis database | console.upstash.com → REST URL + Token |

---

### 2.2 `api/.env.example`

Create this file exactly. The agent must reference all variables from `process.env.*` using these exact names.

```env
# Node
PORT=3000
NODE_ENV=development

# Supabase
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=

# MongoDB
MONGO_URI=

# LiveKit
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_URL=

# Upstash Redis
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=

# Agent worker path (leave blank for local dev; set to absolute path in production)
AGENT_PATH=
```

---

### 2.3 `agent/.env.example`

```env
# Groq
GROQ_API_KEY=

# Deepgram
DEEPGRAM_API_KEY=

# LiveKit (agent connects as a participant)
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# MongoDB (for writing turns + queueing corrections)
MONGO_URI=

# Upstash Redis
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
```

---

## 3. Database Setup

### Step 3.1 — Supabase Schema

File: `supabase/migrations/001_init.sql`

Write and execute this SQL exactly. No extra tables, no extra columns.

```sql
-- Enable UUID extension
create extension if not exists "uuid-ossp";

-- Scenarios table
create table scenarios (
  id uuid primary key default uuid_generate_v4(),
  title text not null,
  target_language text not null,   -- e.g. 'es-ES'
  base_language text not null,     -- e.g. 'en-US'
  system_prompt text not null,
  difficulty_level int not null check (difficulty_level between 1 and 5)
);

-- Sessions table
create type session_status as enum ('active', 'paused', 'completed');

create table sessions (
  id uuid primary key default uuid_generate_v4(),
  user_id uuid not null references auth.users(id) on delete cascade,
  scenario_id uuid not null references scenarios(id),
  status session_status not null default 'active',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Auto-update updated_at
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger sessions_updated_at
  before update on sessions
  for each row execute procedure update_updated_at();

-- RLS: users can only see their own sessions
alter table sessions enable row level security;

create policy "user owns session"
  on sessions for all
  using (auth.uid() = user_id);
```

**USER ACTION REQUIRED:** Run this SQL in the Supabase SQL editor for your project.

---

### Step 3.2 — Seed One Scenario

After running the migration, insert the single MVP scenario via Supabase SQL editor:

```sql
insert into scenarios (title, target_language, base_language, system_prompt, difficulty_level)
values (
  'Tapas Bar in Barcelona',
  'es-ES',
  'en-US',
  'Lesson type: beginner guided lesson.
Lesson objective: ordering food and drinks at a restaurant.
Vocabulary targets (teach in this order): el menu (menu), el agua (water), el cafe (coffee), la cuenta (bill), el pan (bread).
Phrase targets (teach in this order): "Quiero ...", "Me trae ...?", "La cuenta, por favor."
Progression: start in English. For each target, explain in English, give the Spanish, ask the learner to repeat. Do not move on until mastery (2 correct repetitions OR 1 correct contextual use). After about 3 failed attempts, increase English guidance and simplify. Increase Spanish only after mastery.
Guided practice: combine phrases and vocab into short requests, then run a short ordering exchange after all items.
Completion: when all vocab and phrase targets are mastered, say the lesson is complete and ask whether to continue practicing or end the lesson/call.',
  1
);
```

**USER ACTION REQUIRED:** Execute this in the Supabase SQL editor. Note the returned `id` — it is the `scenario_id` for testing.

---

### Step 3.3 — MongoDB Index

The agent worker needs fast reads on `session_id`. In the `language_tutor` database, `turns` collection, create this index:

```js
db.turns.createIndex({ session_id: 1, timestamp: 1 })
```

**USER ACTION REQUIRED:** Run this in the Atlas UI (Collections → turns → Indexes) or via `mongosh`.

---

## 4. API Service (`api/`)

Build in this exact order.

### Step 4.1 — Bootstrap

```bash
cd api
npm init -y
npm install express @supabase/supabase-js mongodb @upstash/redis livekit-server-sdk dotenv
npm install -D typescript @types/express @types/node ts-node-dev
npx tsc --init
```

Set `tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true
  }
}
```

---

### Step 4.2 — Database Clients

**`src/db/supabase.ts`**

Initialize and export a single Supabase client using `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Use the service role key (not anon key) so the API can bypass RLS when needed for server-side operations.

```ts
import { createClient } from '@supabase/supabase-js'
import 'dotenv/config'

export const supabase = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
)
```

**`src/db/mongo.ts`**

Initialize and export a MongoDB client and a reference to the `turns` collection. Use a module-level singleton so the connection is not recreated per request.

```ts
import { MongoClient } from 'mongodb'
import 'dotenv/config'

const client = new MongoClient(process.env.MONGO_URI!)

export async function getDb() {
  await client.connect()
  return client.db('language_tutor')
}

export async function getTurnsCollection() {
  const db = await getDb()
  return db.collection('turns')
}
```

---

### Step 4.3 — Auth Middleware

**`src/middleware/auth.ts`**

Validate the `Authorization: Bearer <token>` header on every protected route. Use the Supabase client's `auth.getUser(token)` to verify. If invalid, return `401`. If valid, attach `req.user = { id: user.id }` and call `next()`.

Do not implement any other auth logic. Supabase Auth manages user registration and login — the API only validates tokens it receives.

---

### Step 4.4 — LiveKit Service

**`src/services/livekit.ts`**

Use `livekit-server-sdk` to mint participant tokens. Export a single function:

```ts
export function createLiveKitToken(roomName: string, participantIdentity: string): string
```

- Room name = `session_id`
- Participant identity = `user_id`
- Grant: `roomJoin: true`, `canPublish: true`, `canSubscribe: true`
- Token TTL: 6 hours
- Use `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` from env

---

### Step 4.5 — Redis Service

**`src/services/redis.ts`**

Use `@upstash/redis`. Export one function:

```ts
export async function enqueueGrammarJob(sessionId: string, turnId: string): Promise<void>
```

Push a JSON payload `{ sessionId, turnId }` to a Redis list key named `grammar_jobs`. This decouples the live call from grammar processing.

---

### Step 4.6 — Session Routes

**`src/routes/sessions.ts`**

Implement exactly four public routes below. Apply auth middleware to all four. The internal webhook route lives in `src/routes/livekit.ts` and is counted separately.

---

#### Route 1: `POST /v1/sessions`

**Purpose:** Start a new session.

**Steps:**
1. Read `scenario_id` from request body. If missing, return `400`.
2. Verify the scenario exists in Supabase `scenarios` table. If not found, return `404`.
3. Insert a new row into Supabase `sessions` table with `user_id = req.user.id`, `scenario_id`, `status = 'active'`.
4. Call `createLiveKitToken(session.id, req.user.id)`.
5. Return:
```json
{
  "session_id": "<uuid>",
  "livekit_url": "<LIVEKIT_URL from env>",
  "livekit_token": "<token>"
}
```

Do not start any Pipecat worker here. Worker startup is triggered externally by LiveKit room events (see Step 5).

---

#### Route 2: `POST /v1/sessions/:session_id/resume`

**Purpose:** Resume a paused session.

**Steps:**
1. Fetch the session from Supabase by `id = session_id`. If not found, return `404`.
2. Verify `session.user_id === req.user.id`. If not, return `403`.
3. Verify `session.status === 'paused'`. If `completed`, return `400` with message `"Session is completed"`. If `active`, return `400` with message `"Session is already active"`.
4. Update `session.status` to `'active'` in Supabase.
5. Fetch the **last 10 turns** from MongoDB `turns` collection where `session_id = session_id`, sorted by `timestamp` ascending.
6. Publish the historic turns to a Redis key `resume:<session_id>` as a JSON string (list of turn objects). Set TTL of 300 seconds. The Pipecat worker will read this on startup to pre-load context.
7. Call `createLiveKitToken(session_id, req.user.id)`.
8. Return:
```json
{
  "livekit_url": "<LIVEKIT_URL from env>",
  "livekit_token": "<token>"
}
```

---

#### Route 3: `GET /v1/sessions/:session_id/feedback`

**Purpose:** Return post-session correction data.

**Steps:**
1. Fetch session from Supabase, verify ownership (same as Route 2 steps 1–2).
2. Query MongoDB `turns` collection for all documents where `session_id = session_id`, sorted by `timestamp` ascending.
3. Return array of turns. Each turn object must contain: `role`, `transcript`, `corrections` (null if not yet processed).

---

#### Route 4: `PATCH /v1/sessions/:session_id/status`

**Purpose:** Allow the client to pause or complete a session.

**Steps:**
1. Verify session ownership (same as Route 2).
2. Accept `{ "status": "paused" | "completed" }` in body. Reject any other value with `400`.
3. Update the session status in Supabase.
4. Return `{ "ok": true }`.

---

### Step 4.7 — Entry Point

**`src/index.ts`**

```ts
import express from 'express'
import 'dotenv/config'
import sessionRoutes from './routes/sessions'

const app = express()
app.use(express.json())
app.use('/v1/sessions', sessionRoutes)

app.get('/health', (_, res) => res.json({ ok: true }))

app.listen(process.env.PORT || 3000, () => {
  console.log(`API running on port ${process.env.PORT || 3000}`)
})
```

---

## 5. Agent Worker (`agent/`)

### Step 5.1 — Dependencies

```bash
cd agent
pip install pipecat-ai[livekit,deepgram,groq] \
  pymongo motor upstash-redis python-dotenv
```

**`requirements.txt`** — pin the versions output by `pip freeze` after install.

---

### Step 5.2 — Context Loader

**`agent/context_loader.py`**

This module loads prior conversation history from Redis (written by the API on resume) and converts it into a list of LLM message dicts.

```python
import json, os
from upstash_redis import Redis

redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

def load_prior_context(session_id: str) -> list[dict]:
    """
    Reads turns from Redis key 'resume:<session_id>'.
    Returns a list of {'role': 'user'|'assistant', 'content': str} dicts.
    Returns empty list if key does not exist (new session).
    """
    key = f"resume:{session_id}"
    raw = redis.get(key)
    if not raw:
        return []
    turns = json.loads(raw)
    messages = []
    for turn in turns:
        role = "user" if turn["role"] == "user" else "assistant"
        messages.append({"role": role, "content": turn["transcript"]})
    redis.delete(key)  # consume once
    return messages
```

---

### Step 5.3 — Main Pipeline

**`agent/pipeline.py`**

This is the core Pipecat worker. It:
1. Joins a LiveKit room as an agent participant
2. Uses Silero VAD for speech boundary detection / turn segmentation
3. Runs the ASR → LLM → TTS conversational pipeline
4. Writes each completed turn to MongoDB
5. Enqueues grammar jobs to Redis

IMPORTANT: Verify the exact LiveKitParams VAD configuration fields
against the installed pipecat-ai version before implementation,
as VAD parameter names may differ across releases.

```python
import asyncio, os, json
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.transports.services.livekit import LiveKitTransport, LiveKitParams
from pipecat.services.deepgram import DeepgramSTTService, DeepgramTTSService
from pipecat.services.groq import GroqLLMService
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext, OpenAILLMContextAggregator
from pipecat.vad.silero import SileroVADAnalyzer

from motor.motor_asyncio import AsyncIOMotorClient
from upstash_redis import Redis

from context_loader import load_prior_context

# ── MongoDB setup ──────────────────────────────────────────────────────────────
mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"])
turns_col = mongo_client["language_tutor"]["turns"]

# ── Redis setup ────────────────────────────────────────────────────────────────
redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

async def write_turn(session_id: str, role: str, transcript: str) -> str:
    """Persist a turn to MongoDB. Returns the inserted document id as string."""
    doc = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc),
        "role": role,
        "transcript": transcript,
        "corrections": None  # filled later by grammar_worker
    }
    result = await turns_col.insert_one(doc)
    return str(result.inserted_id)

def enqueue_grammar_job(session_id: str, turn_id: str):
    payload = json.dumps({"sessionId": session_id, "turnId": turn_id})
    redis.rpush("grammar_jobs", payload)

async def run_agent(session_id: str, scenario_system_prompt: str):
    # Load prior context if this is a resumed session
    prior_messages = load_prior_context(session_id)

    # ── Transport + VAD ────────────────────────────────────────────────────────
    vad = SileroVADAnalyzer()

    transport = LiveKitTransport(
        url=os.environ["LIVEKIT_URL"],
        token=_mint_agent_token(session_id),  # see helper below
        room_name=session_id,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=vad
        )
    )

    # ── STT ────────────────────────────────────────────────────────────────────
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        language="multi",   # enables auto-detect English + Spanish
    )

    # ── LLM ────────────────────────────────────────────────────────────────────
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        model="llama3-70b-8192"
    )

    # ── TTS ────────────────────────────────────────────────────────────────────
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        voice="aura-luna-es"  # Deepgram's Spanish (Spain) voice; swap to "aura-helios-es" for Latin American
    )

    # ── Context ────────────────────────────────────────────────────────────────
    # OpenAILLMContext uses the OpenAI message schema ({role, content}).
    # Groq's API is OpenAI-compatible, so this class works with Groq — no OpenAI dependency required.
    messages = [{"role": "system", "content": scenario_system_prompt}]
    messages.extend(prior_messages)

    context = OpenAILLMContext(messages)
    context_aggregator = OpenAILLMContextAggregator(context)

    # ── Pipeline ───────────────────────────────────────────────────────────────
    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    # ── Turn persistence hooks ─────────────────────────────────────────────────
    @transport.event_handler("on_first_participant_joined")
    async def on_participant_joined(transport, participant):
        # Greet user in Spanish — tú form matches difficulty_level 1 (casual, beginner-friendly)
        await transport.send_audio_text(
            "¡Hola! Bienvenido. ¿Qué te gustaría pedir hoy?"
        )

    @llm.event_handler("on_completion_finished")
    async def on_agent_turn(service, text):
        await write_turn(session_id, "agent", text)
        # Only queue grammar jobs for user turns; agent turns need no correction
        # (queuing happens in the STT handler below)

    @stt.event_handler("on_final_transcript")
    async def on_user_turn(service, text):
        turn_id = await write_turn(session_id, "user", text)
        enqueue_grammar_job(session_id, turn_id)

    # ── Run ────────────────────────────────────────────────────────────────────
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    runner = PipelineRunner()
    await runner.run(task)


def _mint_agent_token(session_id: str) -> str:
    """Mint a LiveKit token for the agent participant using livekit-api."""
    from livekit.api import AccessToken, VideoGrants
    token = AccessToken(
        os.environ["LIVEKIT_API_KEY"],
        os.environ["LIVEKIT_API_SECRET"]
    ).with_identity("agent") \
     .with_grants(VideoGrants(room_join=True, room=session_id)) \
     .to_jwt()
    return token


if __name__ == "__main__":
    import sys
    # Expects: python pipeline.py <session_id> "<system_prompt>"
    session_id = sys.argv[1]
    system_prompt = sys.argv[2]
    asyncio.run(run_agent(session_id, system_prompt))
```

**Note on Pipecat event handler API:** Pipecat's event handler method names (`on_completion_finished`, `on_final_transcript`) must be verified against the installed version of `pipecat-ai`. Check `pipecat`'s changelog if handlers do not fire — the API surface is still evolving. The handler registration pattern above (`@service.event_handler("event_name")`) is the current stable pattern.

---

### Step 5.4 — Grammar Worker

**`agent/grammar_worker.py`**

This is a separate long-running process. It polls Redis for `grammar_jobs` and calls Groq to evaluate grammar asynchronously, then writes corrections back to MongoDB.

```python
import asyncio, json, os
from dotenv import load_dotenv
load_dotenv()

from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from upstash_redis import Redis

groq = Groq(api_key=os.environ["GROQ_API_KEY"])
mongo = AsyncIOMotorClient(os.environ["MONGO_URI"])
turns_col = mongo["language_tutor"]["turns"]
redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

GRAMMAR_SYSTEM_PROMPT = """
You are a Spanish language grammar checker.
Given a Spanish or mixed Spanish-English utterance from a language learner, identify ONE grammar error if present.
Respond ONLY with valid JSON in this exact shape:
{ "error": "<what they said>", "correction": "<correct form>", "explanation": "<one sentence in English>" }
If there is no error, respond with: null
"""

async def process_job(payload: dict):
    turn_id = payload["turnId"]
    doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    if not doc or not doc.get("transcript"):
        return

    response = groq.chat.completions.create(
        model="llama3-70b-8192",
        messages=[
            {"role": "system", "content": GRAMMAR_SYSTEM_PROMPT},
            {"role": "user", "content": doc["transcript"]}
        ],
        response_format={"type": "json_object"},
        max_tokens=200
    )

    raw = response.choices[0].message.content
    try:
        corrections = json.loads(raw)
    except Exception:
        corrections = None

    await turns_col.update_one(
        {"_id": ObjectId(turn_id)},
        {"$set": {"corrections": corrections}}
    )
    print(f"[grammar_worker] Processed turn {turn_id}: {corrections}")

async def main():
    print("[grammar_worker] Listening for grammar_jobs...")
    # MVP NOTE: upstash_redis.Redis is synchronous. brpop blocks the thread for up to
    # `timeout` seconds per call. This is acceptable for a single-worker MVP process that
    # does nothing else. If this worker is ever extended to handle concurrent jobs or
    # other async tasks, replace with an async Redis client (e.g. redis.asyncio).
    while True:
        # Blocking pop with 5s timeout
        result = redis.blpop("grammar_jobs", timeout=5)
        if result:
            _, raw = result
            payload = json.loads(raw)
            try:
                await process_job(payload)
            except Exception as e:
                print(f"[grammar_worker] Error: {e}")
        await asyncio.sleep(0)  # yield to event loop

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 6. Agent Startup Integration

The Pipecat agent worker must be launched when a LiveKit room is created. This is triggered via a **LiveKit `room_started` webhook** — not by the API session routes directly.

### Step 6.1 — LiveKit Webhook Handler

Create `src/routes/livekit.ts` and add this route. Do **not** apply auth middleware — LiveKit signs requests with its own signature, verified in step 1 below.

```
POST /internal/livekit/webhook
```

**Steps:**
1. Verify the request comes from LiveKit by validating the `Authorization` header using LiveKit's webhook receiver from `livekit-server-sdk`.
2. Parse the event. Act only on `room_started` events.
3. Extract `room.name` — this is the `session_id`.
4. Fetch the session from Supabase using `session_id`. Fetch the related scenario to get `system_prompt`.
5. Spawn the agent worker process:
```ts
import { spawn } from 'child_process'
import path from 'path'

const agentPath = process.env.AGENT_PATH
  ? path.resolve(process.env.AGENT_PATH, 'pipeline.py')
  : path.resolve(__dirname, '../../agent/pipeline.py')

spawn('python', [agentPath, session_id, system_prompt], {
  detached: true,
  stdio: 'ignore'
}).unref()
```

Add `AGENT_PATH=` to `api/.env.example` (leave blank for local dev; set to absolute path in production).
6. Return `200 OK`.

**USER ACTION REQUIRED:** In the LiveKit Cloud dashboard, go to your project → Webhooks → add the URL `https://<your-api-host>/internal/livekit/webhook`. Select the `RoomStarted` event only.

---

## 7. Deployment Notes

This section describes what needs to run and where. Do not configure deployment infra — just ensure the code is structured to support it.

| Service | Runtime | Required env file |
|---|---|---|
| `api/` | Node.js 20+ | `api/.env` |
| `agent/pipeline.py` | Python 3.11+ | `agent/.env` |
| `agent/grammar_worker.py` | Python 3.11+ | `agent/.env` |

**USER ACTION REQUIRED:** Before running locally, copy `.env.example` to `.env` in both `api/` and `agent/` and fill in all values.

Local dev startup:
```bash
# Terminal 1 — API
cd api && npm run dev

# Terminal 2 — Grammar worker (persistent background process)
cd agent && python grammar_worker.py

# Agent pipeline is spawned automatically on room_started webhook
```

---

## 8. Correctness Checklist

Before considering any step complete, verify:

**API:**
- [ ] `POST /v1/sessions` returns `session_id`, `livekit_url`, `livekit_token`
- [ ] `POST /v1/sessions/:id/resume` returns `403` for wrong user, `400` for wrong status
- [ ] `POST /v1/sessions/:id/resume` writes turns to Redis key `resume:<session_id>`
- [ ] `GET /v1/sessions/:id/feedback` returns turns from MongoDB with `corrections` field (may be null)
- [ ] `PATCH /v1/sessions/:id/status` accepts only `paused` or `completed`
- [ ] Auth middleware rejects requests with missing or invalid JWT

**Agent:**
- [ ] `context_loader.py` returns empty list for new sessions, correct messages for resumed sessions
- [ ] `pipeline.py` connects to LiveKit room, sends greeting on first participant join
- [ ] Each user utterance is written to MongoDB with `corrections: null` and enqueued to Redis
- [ ] `grammar_worker.py` reads from Redis, calls Groq, writes corrections back to MongoDB
- [ ] Grammar worker does not block the live call (separate process)

**Data:**
- [ ] MongoDB `turns` documents have: `session_id`, `timestamp`, `role`, `transcript`, `corrections`
- [ ] Supabase `sessions.status` transitions: `active → paused → active → completed`
- [ ] Redis `resume:<session_id>` key is deleted after being consumed by `context_loader.py`

---

## 9. What Not To Do

The agent must not:
- Create any frontend pages, HTML, or React components
- Add any database tables or collections not defined in this document
- Implement email sending, SMS, or push notifications
- Add rate limiting, caching layers, or analytics beyond what is described
- Implement user registration or login endpoints — Supabase Auth client handles this on the frontend
- Create more than one LLM provider integration — use Groq only
- Add any logging infrastructure beyond `console.log` / `print`
- Create Docker files or CI/CD configuration unless explicitly asked in a follow-up instruction

---

## 10. Streaming Verification Scripts (Non-Production)

Before changing production latency behavior, run these standalone scripts to verify streaming behavior. These scripts do **not** modify the production runtime or pipeline.

**Location:** `agent/scratch/`

### 10.1 Groq LLM streaming verification

Script: `agent/scratch/verify_groq_streaming.py`

Goal: confirm incremental token streaming using the same model and a production-equivalent prompt shape.

Run:
```bash
cd agent
python3 scratch/verify_groq_streaming.py --system-prompt-file ../AGENT_SYSTEM_PROMPT.txt
```

Notes:
- If you do not have a prompt file, set `AGENT_SYSTEM_PROMPT` in your environment or run without `--system-prompt-file` (it will use a representative default prompt).
- You can override the user prompt with `--user-prompt "..."`.

Expected output interpretation:
- **Streaming** if the first token arrives significantly before completion and chunks arrive over time.
- **Buffered/Burst** if chunks cluster near completion or first token appears near the end.

### 10.2 Deepgram TTS streaming verification (Pipecat path)

Script: `agent/scratch/verify_deepgram_tts_streaming.py`

Goal: verify Deepgram TTS streaming using the same Pipecat integration path and voice config as production.

Run:
```bash
cd agent
python3 scratch/verify_deepgram_tts_streaming.py --text "Hola. Hoy vamos a practicar el menu y el agua."
```

Notes:
- Uses `DEEPGRAM_API_KEY` and `DEEPGRAM_VOICE` (defaults to `aura-2-sirio-es`).
- This uses Pipecat's `DeepgramTTSService`, not a generic REST request.

Expected output interpretation:
- **Streaming** if the first audio chunk arrives significantly before completion and chunks arrive over time.
- **Buffered/Burst** if audio chunks cluster near completion.

### 10.3 Pipecat streaming trace (mandatory)

Script: `agent/scratch/trace_pipecat_streaming.py`

Goal: trace internal buffering boundaries: first LLM token → first TextFrame → first TTS audio frame.

Run:
```bash
cd agent
python3 scratch/trace_pipecat_streaming.py --system-prompt-file ../AGENT_SYSTEM_PROMPT.txt
```

Notes:
- Uses the same Groq and Deepgram services as the production pipeline.
- Override user input with `--user-text "..."`.

Expected output interpretation:
- If the first LLM token is late, the LLM is likely buffering.
- If the first TextFrame is early but audio is late, TTS or Pipecat scheduling is buffering.

---