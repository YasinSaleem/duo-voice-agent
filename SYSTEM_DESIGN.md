# Duo Voice Agent: System Design & Architecture

This document outlines the architecture, trade-offs, latency measurements, costs, roadmap, and current limitations based on the live codebase, production session instrumentation (`agent/latency_runtime.log`), and isolated lab benchmarks.

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

### Production configuration (live sessions)

Instrumented via hardcoded pipeline logging to `agent/latency_runtime.log` (JSON lines, always on).

| Component | Value |
| --- | --- |
| LLM | Groq `llama-3.1-8b-instant` |
| STT | Deepgram `nova-3-general` (multi) |
| TTS | Deepgram `aura-2-sirio-es` |
| Max completion tokens | 120 |
| System prompt (typical) | ~370 words / ~2472 chars |

### End-to-end response latency (production)

**Definition:** user starts speaking → assistant starts speaking (first TTS audio frame).

Known pause-induced interruption outlier excluded from percentile calculations.

| Metric | Value |
| --- | --- |
| **p50** | ~**1.9–2.0 s** |
| **p95** | ~**2.05–2.1 s** |

**Interpretation:** latency is fairly stable. Most responses land near **2 seconds**; worst normal cases are just above **2 seconds**. No large jitter in the normal response path.

### Interruption / barge-in (production)

**Definition:** user interrupts bot while bot is speaking → bot stops speaking.

Pause artifact outlier ignored (one long false-positive during user silence).

| Sample latencies | ~0.53 s, ~0.57 s, ~0.67 s, ~1.70 s |
| --- | --- |
| **p50** | ~**0.7–0.9 s** |
| **p95** | ~**1.6–3.3 s** (varies by snapshot) |

**Interpretation:** barge-in works. Typical stop time is **~700–900 ms**. Occasional slower cases at **1.5–1.7 s** — usable, but target for premium UX is **under 500 ms**.

### TTS streaming behavior (production)

- Observed streaming chunk duration: **~80 ms (0.08 s)** per frame, repeatedly.
- TTS is **not** the dominant bottleneck; small chunks support early playback.

### Instrumentation caveats

- Logs can emit many `tts_audio`-driven pseudo-turns (e.g. turn 643, 644, 645…). This reflects **logging turn boundaries**, not conversational turns.
- Raw turn counts from the log are misleading; percentiles should be computed from `turn_latency_breakdown` / `end_to_end_voice_latency` events, not turn IDs.

Per-turn breakdown fields in `turn_latency_breakdown` include: `mic_to_stt_interim`, `stop_to_merged_user`, `merged_to_inference`, `groq_to_first_token`, `first_token_to_tts_request`, `tts_request_to_audio`, and `end_to_end_voice_latency`.

### Overall assessment (production)

| Area | Score | Notes |
| --- | --- | --- |
| Response speed | **7/10** | ~2 s median; stable but not “premium real-time” |
| Interruption responsiveness | **6.5–7/10** | Works; p50 ~0.7–0.9 s |
| Latency consistency | **8/10** | Tight p50–p95 band on normal responses |
| TTS responsiveness | **8.5/10** | Small streaming chunks, low perceived stall |

**Working well:** stable overall latency, streaming TTS, functional barge-in, no severe normal response spikes.

**Could improve:** shave end-to-end from ~2 s toward sub-1.5 s; tighten interruption p95 and target under 500 ms typical stop time; fix pseudo-turn noise in latency aggregation.

**Bottom line:** reasonably responsive for a tutoring MVP, but not yet premium conversational real-time.

### Lab / isolated benchmarks (2026-05-23)

Component-level scripts (not full LiveKit sessions):

- **Groq LLM streaming (`agent/experiments/verify_groq_streaming.py`)**
  - TTFT: **0.214 s**
  - Total completion: **0.295 s**
  - Verdict: **buffered_or_burst** (chunks clustered near completion)

- **Pipecat pipeline trace (`agent/experiments/trace_pipecat_streaming.py`)**
  - First LLM token: **+1.762 s**
  - First TTS input: **+1.764 s**
  - First audio frame: **+2.141 s**
  - LLM → first audio gap: **~0.379 s**

- **Deepgram TTS streaming (`agent/experiments/verify_deepgram_tts_streaming.py`)**
  - First audio chunk: **+1.352 s** (includes ~1.0 s websocket cold-start in isolated test)
  - Continuous chunk stream after first frame

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

3. **Extend observability beyond `latency_runtime.log`**
   - Per-stage metrics are logged locally today; add aggregation dashboards, OpenTelemetry traces, and fix pseudo-turn grouping so turn IDs match conversational turns.

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

- **Latency log turn IDs vs. conversation turns**
  - `agent/latency_runtime.log` may increment turn IDs on TTS audio frames; use `turn_latency_breakdown` events for accurate per-utterance stats.

- **End-to-end latency budget**
  - Production median response time is ~2 s; interruption p50 ~0.7–0.9 s with occasional 1.5+ s tails.
