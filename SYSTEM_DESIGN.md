# Duo Voice Agent: System Design & Architecture

This document outlines the architecture, trade-offs, latency measurements, costs, roadmap, and current limitations based on the live codebase and the latest evaluation outputs.

## 🏗️ Architecture

The system is a distributed, event-driven stack that separates the real-time voice loop from slower, compute-heavy analysis.

- **API + Orchestration (Node/Express + LiveKit):**
  - `api/src/index.ts` serves the UI and exposes session/auth routes.
  - `api/src/routes/livekit.ts` receives LiveKit webhooks and triggers `spawnAgent(session_id)`.
  - `api/src/services/livekit.ts` spawns the agent as a detached Python process (`python -m agent.voice_agent`) and injects the assembled system prompt (scenario + lesson state + recent memories).

- **Real-Time Voice Pipeline (Pipecat + LiveKit):**
  - `agent/voice_agent.py` builds a Pipecat pipeline: LiveKit transport → Deepgram STT (Nova-3 general, multi-language) → user turn buffer → Groq LLM (llama-3.1-8b-instant) → clause chunker → tutor text streamer → Deepgram TTS (aura-2-sirio-es) → LiveKit output.
  - `ClauseBoundaryTextChunker` flushes phrase-sized text frames at punctuation to reduce perceived latency without token-by-token TTS.
  - `UserTurnBufferProcessor` merges nearby transcription chunks into a single user turn, persists to MongoDB, and enqueues a grammar job.

- **Async Workers (Python daemons + Upstash Redis):**
  - Grammar worker (`agent/workers/grammar_worker.py`) pulls from `grammar_jobs` and uses Groq Qwen3-32B to produce `{error, correction, explanation}` stored in MongoDB.
  - Memory worker (`agent/workers/memory_worker.py`) pulls from `memory_jobs`, summarizes full transcripts with Groq llama-3.1-8b-instant, and writes structured memories to Supabase.

- **Storage:**
  - MongoDB: high-throughput turn storage and grammar corrections.
  - Supabase (Postgres): user accounts, session metadata, lesson state checkpoints, and long-term memories.
  - Upstash Redis (REST): async job queues (`grammar_jobs`, `memory_jobs`) and resume cache.

## ⚖️ Trade-offs

1. **Detached agent subprocess vs. container/job orchestration**
   - The API spawns a local `python -m agent.voice_agent` subprocess. This is simple to deploy on a single VM but not horizontally scalable or isolated.

2. **Polling Redis REST vs. blocking queues**
   - Workers poll Upstash via `LPOP` with sleeps (1s grammar, 2s memory). This avoids persistent sockets but adds queue latency and wastes idle cycles.

3. **Sync SDK calls inside asyncio loops**
   - Workers use synchronous Groq and Supabase calls in async loops. This is fine for single-worker MVP but blocks concurrency under load.

4. **Streaming behavior depends on provider chunking**
   - Groq streaming can be fast but still appear “buffered or burst” at small timescales for short completions (see measurements below).

## ⚡ Latency Measurements

Measurements taken from the evaluation scripts on 2026-05-23:

- **Groq LLM streaming (`agent/experiments/verify_groq_streaming.py`)**
  - TTFT: **0.214s**
  - Total completion time: **0.295s**
  - Verdict: **buffered_or_burst** (chunks clustered near completion)

- **Pipecat full pipeline trace (`agent/experiments/trace_pipecat_streaming.py`)**
  - First LLM token: **+1.762s**
  - First TTS input: **+1.764s**
  - First audio frame: **+2.141s**
  - LLM-to-audio gap in this run: **~0.379s**

- **Deepgram TTS streaming (`agent/experiments/verify_deepgram_tts_streaming.py`)**
  - First audio chunk: **+1.352s**
  - Large, continuous stream of audio chunks observed after first chunk (hundreds of frames).

Notes:
- The isolated Deepgram TTS test includes websocket connection setup in its timing, which contributed ~1.0s of cold-start before audio streaming.

## 🧪 Testing & Evaluation

- **LLM regression harness:** `agent/evaluation/eval_tutor.py`
  - Validates tutor brevity, English fallback behavior, and grammar prompt robustness.

- **Pipeline/infra verification:**
  - `agent/experiments/verify_pipeline.py` exercises LiveKit token minting, MongoDB writes, and Redis enqueue semantics.
  - `agent/experiments/verify_grammar.py` validates grammar worker output schema and DB updates.

- **Streaming/latency checks:**
  - `agent/experiments/verify_groq_streaming.py`
  - `agent/experiments/trace_pipecat_streaming.py`
  - `agent/experiments/verify_deepgram_tts_streaming.py`

## 💰 Costs

- **Groq:** low-cost, high-throughput inference for llama-3.1-8b-instant and Qwen3-32B (grammar).
- **Deepgram:** STT (Nova-3) + TTS (Aura) billed per minute; websocket usage keeps latency low but has a cold-start cost.
- **Upstash Redis / Supabase / MongoDB Atlas:** serverless pricing tiers suitable for early-stage usage and low idle cost.
- **LiveKit Cloud:** pay-as-you-go bandwidth/egress; predictable scaling for WebRTC sessions.

## 🚀 What I’d Build Next

1. **Replace subprocess spawning with a job-based agent fleet**
   - Use a queue + worker pool (Kubernetes Jobs, ECS, or Temporal) to scale agents safely and collect structured telemetry.

2. **Switch Redis polling to blocking or streaming queues**
   - Move from REST polling to BLPOP or a streaming queue (Redis Streams / SQS) to reduce grammar/memory delays.

3. **Add dedicated observability**
   - Structured logs, OpenTelemetry traces, and per-stage latency metrics (STT → LLM → TTS).

4. **Improve personalization fidelity**
   - Feed more than 3 historical memories and add session-level retrieval to avoid prompt truncation.

## ⚠️ Known Limitations

- **Groq streaming can be “burst-like” for short outputs**
  - The current run showed buffered/burst chunking despite a fast TTFT, which can reduce perceived incremental streaming.

- **TTS cold-start latency**
  - The first Deepgram websocket connection adds ~1.0s before audio frames are observed in isolated tests.

- **Polling delays and rate limits**
  - Upstash REST polling introduces up to 1–2s of idle delay and can hit API limits under load.

- **Single-process worker throughput**
  - Grammar and memory workers are single-process, synchronous SDKs inside async loops; they will bottleneck without a worker pool.

- **Context truncation on resume**
  - Resume only caches the last 10 turns and last 3 memories, which can omit older context in long-running learners.
