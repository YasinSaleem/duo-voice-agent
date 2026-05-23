# Duo Voice Agent

Real-time conversational Spanish tutor powered by LiveKit WebRTC, Pipecat, Groq, Deepgram, MongoDB, Supabase, and Upstash Redis. The system streams low-latency voice conversations and runs asynchronous grammar and memory jobs in the background.

## Key Components

- **API (Node/Express):** session/auth routes, LiveKit webhook handler, static UI hosting.
- **Voice Agent (Python/Pipecat):** LiveKit transport → Deepgram STT → Groq LLM → Deepgram TTS.
- **Workers (Python):** grammar and memory daemons consuming Redis queues.
- **Storage:** MongoDB for turns/corrections, Supabase for sessions/memories.

## Prerequisites

- Node.js 18+
- Python 3.10+
- cloudflared (for LiveKit webhook tunneling in local dev)

## Setup

Create env files for both services.

### api/.env

```ini
PORT=3000
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/dbname?retryWrites=true&w=majority
UPSTASH_REDIS_REST_URL=https://your-redis-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_token
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=API_your_api_key
LIVEKIT_API_SECRET=your_secret_key
PYTHON_BINARY=python3
```

### agent/.env

```ini
DEEPGRAM_API_KEY=your_deepgram_api_key
GROQ_API_KEY=your_groq_api_key
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
UPSTASH_REDIS_REST_URL=https://your-redis-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_token
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/dbname?retryWrites=true&w=majority
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=API_your_api_key
LIVEKIT_API_SECRET=your_secret_key
```

## Install

```bash
cd api
npm install

cd ../agent
pip install -r requirements.txt
```

## Run (Local)

```bash
cd api
npm run dev:all
```

This starts the API, grammar worker, and memory worker. The agent process is spawned on demand via LiveKit webhooks.

## LiveKit Webhook (Local Tunnel)

```bash
cloudflared tunnel --url http://localhost:3000
```

Register the tunnel URL in LiveKit Cloud as:

```
https://<your-tunnel>.trycloudflare.com/internal/livekit/webhook
```

## Diagnostics

```bash
cd api
npm run doctor

cd ../agent
python3 doctor.py

cd ../api
npx ts-node src/verify_webhook.ts
```

## Evaluation Scripts

```bash
python3 agent/evaluation/eval_tutor.py
python3 agent/experiments/verify_groq_streaming.py
python3 agent/experiments/trace_pipecat_streaming.py
python3 agent/experiments/verify_deepgram_tts_streaming.py
```
