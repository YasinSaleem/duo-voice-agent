# Spanish Language Tutor — Conversational Voice AI System

A premium real-time conversational Spanish language tutor system. 

The system leverages **Pipecat**, **LiveKit WebRTC**, **Groq (Llama-3.1-8b-instant for voice conversation & Qwen-32B-instruct for deep reasoning grammar corrections)**, **Deepgram**, **MongoDB**, **Supabase**, and **Upstash Redis** to deliver ultra-low-latency real-time voice conversations paired with asynchronous Spanish grammar evaluation.

---

## 🏗️ System Architecture Flow

The system runs as an event-driven distributed architecture:

```
┌──────────┐         Join Room         ┌───────────────┐
│          ├──────────────────────────>│               │
│   User   │                           │ LiveKit Cloud │
│  Client  │<──────────────────────────┤               │
└──────────┘       Audio / Video       └──┬─────────┬───┘
                                          │         │
                   ┌──────────────────────┘         │ Webhook: room_started
                   │ Join Room as Agent             │ (Authentic cryptographic signature)
                   │                                v
┌──────────────────┴───┐                      ┌─────┴──────────┐
│                      │                      │                │
│    Pipecat Agent     │<── Spawn (Detached) ──│  Express API   │
│  (pipeline.py)       │                      │  (Port 3000)   │
│                      │                      │                │
└──────────┬───────────┘                      └────────┬───────┘
           │                                           │
           │ Finalized User Turns enqueued (FIFO)      │ Read / Write Sessions
           v                                           v
┌──────────────────────┐                      ┌────────────────┐
│    Upstash Redis     │                      │    Supabase    │
│  ("grammar_jobs")    │                      │  (Auth & DB)   │
└──────────┬───────────┘                      └────────────────┘
           │
           │ Non-blocking LPOP Pull
           v
┌──────────────────────┐   Deep Grammar Check   ┌────────────────┐
│    Grammar Worker    ├───────────────────────>│  MongoDB Atlas │
│ (grammar_worker.py)  │     (Qwen 32B)        │    (Turns &    │
└──────────────────────┘                        │  Corrections)  │
                                                └────────────────┘
```

---

## 🛠️ Prerequisites

Before launching, make sure you have the following installed on your machine:
- **Node.js** (v18.x or above)
- **Python** (v3.10 or above)
- **Cloudflare CLI (`cloudflared`)** for secure webhook exposure. Install on macOS using Homebrew:
  ```bash
  brew install cloudflare/cloudflare/cloudflared
  ```

---

## ⚙️ Configuration & Environment Setup

You need to set up environment variables in both the `api/` and `agent/` folders. Copy their respective `.env.example` templates and fill in the values.

### 1. API Workspace Configuration (`api/.env`)

```ini
PORT=3000

# Supabase Configurations
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5c...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5c... # Bypasses RLS safely

# MongoDB Atlas
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/dbname?retryWrites=true&w=majority

# Upstash Redis
UPSTASH_REDIS_REST_URL=https://your-redis-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_token

# LiveKit Credentials
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=API_your_api_key
LIVEKIT_API_SECRET=your_secret_key

# Agent Spawn Resolution
AGENT_PATH=/Users/yasinsaleem/Documents/Job_Work/ChittanshAI/duo-voice-agent/agent
PYTHON_BINARY=python3
```

### 2. Agent Workspace Configuration (`agent/.env`)

```ini
# Deepgram & AI Models
DEEPGRAM_API_KEY=your_deepgram_api_key
GROQ_API_KEY=gsk_your_groq_api_key

# Upstash Redis (For prior context loader and grammar queue)
UPSTASH_REDIS_REST_URL=https://your-redis-instance.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_token

# MongoDB Atlas (For conversation turns and corrections storage)
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/dbname?retryWrites=true&w=majority

# LiveKit Credentials
LIVEKIT_URL=wss://your-livekit-project.livekit.cloud
LIVEKIT_API_KEY=API_your_api_key
LIVEKIT_API_SECRET=your_secret_key
```

---

## 🚀 Step-by-Step Local Startup Guide

### Step 1: Install Dependencies

1. **Install Node.js packages for the API**:
   ```bash
   cd api
   npm install
   ```
2. **Install Python requirements for the Agent**:
   ```bash
   cd ../agent
   pip install -r requirements.txt
   ```

---

### Step 2: Start the Local Servers

Open two terminal windows to run both services simultaneously:

#### Terminal 1: Run the Express API
```bash
cd api
npm run dev
```
*The API is now running locally on `http://localhost:3000`.*

#### Terminal 2: Run the Grammar Worker
```bash
cd agent
python3 grammar_worker.py
```
*The background grammar worker is now listening on Upstash Redis for user utterances to check grammar asynchronously.*

---

## 🌐 Setting Up Webhooks with Cloudflare Tunnel

LiveKit Cloud needs a publicly accessible HTTPS endpoint to dispatch its `room_started` event when rooms are created. We will expose your local server (`localhost:3000`) securely using Cloudflare.

### 1. Launch a Free Cloudflare Tunnel
In a new terminal window, run the following command to expose port `3000`:
```bash
cloudflared tunnel --url http://localhost:3000
```

### 2. Copy the Tunnel URL
Cloudflare will spin up a tunnel and output a public URL in the logs. Look for a line resembling:
```text
+-------------------------------------------------------------+
|  Your quick tunnel has been created! Visit it at:           |
|  https://some-random-words.trycloudflare.com                |
+-------------------------------------------------------------+
```
Copy that HTTPS URL (e.g., `https://some-random-words.trycloudflare.com`).

### 3. Register the Webhook on LiveKit Cloud
1. Go to your **[LiveKit Cloud Dashboard](https://cloud.livekit.io/)**.
2. Select your project and navigate to **Settings** > **Webhooks**.
3. Click **Add Webhook**.
4. Set the **Webhook URL** to your Cloudflare Tunnel URL appended with `/internal/livekit/webhook`.
   *Example:*
   ```text
   https://some-random-words.trycloudflare.com/internal/livekit/webhook
   ```
5. Choose **Events to receive**: Make sure to check **`Room Started`** (or select all room events).
6. Click **Save Webhook**.

*Whenever a user triggers a session start (minting a token and starting a LiveKit room), LiveKit Cloud will hit your local API securely over the tunnel, prompting it to dynamically spin up the `agent/pipeline.py` background tutor agent!*

---

## 🩺 System Verification & Diagnostics

We have equipped the system with comprehensive diagnostic suites to verify that your environment, secrets, and integration endpoints are fully healthy.

### 1. Run API Diagnostics ("Doctor")
Verifies all environment configurations and checks direct connectivity to Supabase, MongoDB, Upstash Redis, and LiveKit Cloud:
```bash
cd api
npm run doctor
```

### 2. Run Agent Diagnostics ("Doctor")
Verifies Python environment imports and validates API connectivity to MongoDB, Upstash Redis, LiveKit, Groq, and Deepgram:
```bash
cd agent
python3 doctor.py
```

### 3. Run E2E Webhook Signature Simulation
Simulates an authentic cryptographic LiveKit webhook signature dispatch to verify your raw body parsing middleware, `WebhookReceiver` verification, database scenario prompt extraction, and background detached Python process spawning.
*(This script runs isolated tests, creates temporary mock objects, and cleans up after itself completely).*
```bash
cd api
npx ts-node src/verify_webhook.ts
```
