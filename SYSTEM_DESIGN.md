# Duo Voice Agent: System Design & Architecture

This document outlines the architectural decisions, trade-offs, latency benchmarks, cost considerations, and future roadmaps for the Duo Voice Agent project.

## 🏗️ Architecture

The system is built as a distributed, event-driven architecture designed to decouple real-time voice streaming from expensive/slow background tasks (like grammar evaluation and memory compilation).

- **Real-Time Voice Pipeline (Pipecat + LiveKit):** 
  When a user joins a room, a webhook triggers a detached `pipeline.py` process. This Pipecat pipeline streams audio via **LiveKit WebRTC**. It uses **Deepgram STT** for speech recognition, **Groq (Llama-3.1-8b-instant)** for ultra-fast conversational text generation, and **Deepgram TTS** (Aura) for speech synthesis.
  
- **Asynchronous Workers (Upstash Redis + Python Daemons):**
  - **Grammar Worker (`grammar_worker.py`):** Listens to a `grammar_jobs` queue on Upstash Redis. It uses **Groq (Qwen-32B-instruct)** to deeply analyze each user turn for spoken grammar mistakes and writes corrections to **MongoDB Atlas**.
  - **Memory Worker (`memory_worker.py`):** Listens to a `memory_jobs` queue. Upon session completion, it pulls the full transcript from MongoDB and uses Llama-3.1-8b to compress the session into structured pedagogical memory, saved to **Supabase**.

- **State & Storage:**
  - **Supabase (PostgreSQL):** Stores users, session metadata, and long-term generated memories.
  - **MongoDB Atlas:** Stores the high-throughput, unstructured conversation turns and real-time grammar corrections.

## ⚖️ Trade-offs

1. **Async Polling vs. True Pub/Sub:** The background workers currently use a continuous polling loop (`LPOP` with an `asyncio.sleep`) against an Upstash Redis REST API. 
   *Trade-off:* This is easy to deploy on serverless/local environments without persistent TCP connections, but it introduces a slight polling delay (up to 1 second) and isn't as instantly reactive as a true WebSocket or persistent Redis `BLPOP`.
2. **Synchronous SDKs in Asyncio:** The background workers use synchronous SDK calls (like Supabase `.execute()` and standard Groq completions) inside an `asyncio` loop. 
   *Trade-off:* This works perfectly for a single-worker background daemon (MVP) but would bottleneck if horizontally scaled behind a single process without a process pool or thread pool redesign.
3. **Detached Subprocesses vs. Kubernetes Pods:** The Node.js API spawns the Pipecat agent as a detached Python subprocess. 
   *Trade-off:* Great for local development and single-VM deployments, but not scalable across a cluster out of the box.

## ⚡ Latency Measurements

Based on the latency verification test files (`verify_groq_streaming.py` and Pipecat tracing):

- **LLM Time-To-First-Token (TTFT):** `~300ms`
  - *Groq Llama-3.1-8b-instant* consistently hits the first token around 300ms.
- **LLM Completion Time:** `~430ms`
  - The model streams chunks extremely fast, completing a typical 50-chunk response in roughly 130ms after the first token.
- **Pipeline E2E Latency:** 
  - Using clause-boundary chunking (`ClauseBoundaryTextChunker`), the Pipecat pipeline sends phrases to Deepgram TTS as soon as a comma or period is generated.
  - Deepgram TTS adds approximately `300-500ms` of processing.
  - **Total Conversational Latency (User stops speaking -> Agent starts speaking):** Consistently `under 1.0 second` (typically ~800ms), creating a highly fluid, natural voice conversation.

## 💰 Costs

The stack was chosen to heavily optimize for both low latency and low cost:
- **Groq:** Extremely low cost per 1M tokens, with generous free tiers for `llama-3.1-8b` and `qwen-32b`.
- **Deepgram:** Industry-leading pricing for STT (Nova-2/Nova-3) and TTS (Aura), costing fractions of a cent per minute.
- **Upstash Redis / Supabase / MongoDB Atlas:** All offer robust Serverless/Free tiers that easily support development and small-scale production without fixed overhead.
- **LiveKit Cloud:** Free tier provides ample bandwidth for WebRTC streaming before switching to a predictable pay-as-you-go model.

## 🚀 What I'd Build Next

1. **Containerized Agent Fleet (Kubernetes / Docker):** Replace the detached subprocess spawning with a proper job queue (e.g., temporal.io or Kubernetes Jobs) to spin up isolated, scalable Pipecat agents.
2. **WebSockets for Workers:** Upgrade the Upstash REST polling to persistent Redis connections (`BLPOP`) to eliminate the 1-second polling latency on grammar checks.
3. **Frontend Dashboard:** Build a Next.js frontend where users can review their generated session memories, grammar corrections, and track their Spanish fluency progression over time.
4. **RAG for Context:** Inject the user's historical `grammar_insights` into the Pipecat agent's system prompt dynamically, so the agent remembers what the user struggled with last week.

## ⚠️ Known Limitations

- **Speech-to-Text Formatting:** Deepgram STT provides punctuation and accent marks which the user didn't explicitly "say" (e.g., `à` vs `á`). The grammar worker is instructed to ignore these, but heavy transcription errors can still occasionally mislead the grammar evaluator.
- **Simultaneous Speaking:** While Pipecat supports interruptions (VAD), extreme cross-talk can sometimes clip the agent's context or result in truncated MongoDB turn logging.
- **Polling Rate Limits:** The background workers poll Upstash via HTTP REST. Heavy traffic could hit Upstash REST rate limits unless upgraded to a persistent Redis client.
