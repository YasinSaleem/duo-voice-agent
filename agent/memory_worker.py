import asyncio
import json
import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Secure macOS SSL certificate override
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass

from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
from supabase import create_client, Client

# ------------------------------------------------------------------------------
# Single-Worker Low-Throughput Background Processor Wording:
# NOTE: This background memory worker uses synchronous SDK calls (like sync
# supabase-py execute(), synchronous Groq completions, and sync Upstash Redis
# REST SDK polling) inside an asyncio loops. This is an intentional MVP tradeoff
# that works perfectly for a single-worker background daemon. It should not be
# horizontally scaled or treated as a fully parallel production-grade async pipeline
# without a future event-driven or worker-pool redesign.
# ------------------------------------------------------------------------------

# Initialize Services
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

tls_kwargs = {"tlsCAFile": certifi.where()} if "certifi" in sys.modules else {}
mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"], **tls_kwargs)
turns_col = mongo_client["language_tutor"]["turns"]

# Initialize official Supabase client using Service Role key
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_ROLE_KEY"]
)

# Connect to Upstash Redis
from upstash_redis import Redis
redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

MEMORY_SYSTEM_PROMPT = """
You are a warm, expert Spanish tutor's pedagogical assistant.
Your job is to analyze the full chat transcript of a voice session and compress it into highly structured metadata to be stored as the learner's long-term memory.

Analyze the flow, identified mistakes, and general language abilities.
Generate a valid JSON object matching the following structure EXACTLY (do not include any formatting, markdown, or chat text outside the JSON):
{
  "summary": "<2-3 sentence English paragraph outlining what scenario was practiced, how they performed, and their fluency/confidence level.>",
  "grammar_insights": [
    {
      "topic": "<Concept name, e.g. Gender Agreement>",
      "status": "<Needs Work | Proficient>",
      "details": "<1-sentence pedagogical details.>"
    }
  ],
  "vocabulary_learned": [
    {
      "spanish": "<spanish word>",
      "english": "<english translation>"
    }
  ],
  "key_takeaways": [
    "<1-2 actionable tips for their next practice.>"
  ]
}
"""

async def fetch_scenario_title(session_id: str) -> str:
    """Deterministic scenario title lookup using official supabase-py client."""
    try:
        # 1. Get session to extract scenario_id
        session_res = supabase.table("sessions").select("scenario_id").eq("id", session_id).execute()
        if not session_res.data or len(session_res.data) == 0:
            print(f"[MemoryWorker] Session {session_id} not found in database.")
            return "Tutor Practice Session"
        
        scenario_id = session_res.data[0]["scenario_id"]
        
        # 2. Get scenario details by scenario_id
        scenario_res = supabase.table("scenarios").select("title").eq("id", scenario_id).execute()
        if scenario_res.data and len(scenario_res.data) > 0:
            return scenario_res.data[0]["title"]
            
    except Exception as e:
        print(f"[MemoryWorker] Error looking up scenario: {e}")
    return "Tutor Practice Session"

async def check_memory_exists(session_id: str) -> bool:
    """Helper to check if a memory row already exists (Idempotency Protection)."""
    try:
        res = supabase.table("memories").select("id").eq("session_id", session_id).execute()
        return len(res.data) > 0
    except Exception as e:
        print(f"[MemoryWorker] Error checking memory existence: {e}")
        return False

async def process_memory_job(payload: dict):
    session_id = payload.get("sessionId")
    user_id = payload.get("userId")
    if not session_id or not user_id:
        return

    # 1. Idempotency Check: Gracefully exit if memory is already processed
    if await check_memory_exists(session_id):
        print(f"[MemoryWorker] Memory already exists for session {session_id}. Gracefully skipped.")
        return

    print(f"[MemoryWorker] Compiling memory for session: {session_id}")
    
    # 2. Correct Motor API: fetch all turns from MongoDB in order
    cursor = turns_col.find({"session_id": session_id}).sort("timestamp", 1)
    raw_turns = await cursor.to_list(length=None)
    
    if len(raw_turns) < 2:
        print(f"[MemoryWorker] Session {session_id} has too few turns to summarize. Skipping.")
        return

    # 3. Format transcript
    transcript_text = ""
    for turn in raw_turns:
        role = "Learner" if turn["role"] == "user" else "Tutor"
        transcript_text += f"{role}: {turn['transcript']}\n"

    try:
        # 4. Call Groq with Enforced response format JSON
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": MEMORY_SYSTEM_PROMPT},
                {"role": "user", "content": f"Transcript:\n{transcript_text}"}
            ],
            response_format={"type": "json_object"}
        )
        raw_json = response.choices[0].message.content.strip()
        
        # 5. Load and validate
        memory_data = json.loads(raw_json)
        
        # 6. Fetch scenario title deterministically
        title = await fetch_scenario_title(session_id)
        
        # 7. Build Supabase payload
        supabase_payload = {
            "user_id": user_id,
            "session_id": session_id,
            "scenario_title": title,
            "summary": memory_data["summary"],
            "grammar_insights": memory_data["grammar_insights"],
            "vocabulary_learned": memory_data["vocabulary_learned"],
            "key_takeaways": memory_data["key_takeaways"]
        }
        
        # 8. Save memory to database; handle uniqueness constraint gracefully
        try:
            supabase.table("memories").insert(supabase_payload).execute()
            print(f"[MemoryWorker] Memory successfully created for session {session_id}")
        except Exception as insert_err:
            err_msg = str(insert_err)
            if "duplicate key" in err_msg or "unique constraint" in err_msg or "already exists" in err_msg:
                print(f"[MemoryWorker] Duplicate write conflict detected for session {session_id}. Skipped gracefully (idempotency preserved).")
            else:
                raise insert_err
        
    except Exception as e:
        print(f"[MemoryWorker] Error during memory generation process: {e}")

async def main():
    print("[MemoryWorker] Starting background memory aggregator...")
    print("[MemoryWorker] Listening on queue: 'memory_jobs'...")
    while True:
        try:
            result = redis.lpop("memory_jobs")
            if result:
                payload = json.loads(result)
                await process_memory_job(payload)
                # Check next item immediately
                continue
        except Exception as e:
            print(f"[MemoryWorker] Lifecycle loop exception: {e}")
        await asyncio.sleep(2.0)

if __name__ == "__main__":
    asyncio.run(main())
