import os
from motor.motor_asyncio import AsyncIOMotorClient
from upstash_redis import Redis as SyncRedis
from upstash_redis.asyncio import Redis as AsyncRedis
from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 1. TLS/SSL Verification Configuration
tls_kwargs = {}
try:
    import certifi
    tls_kwargs["tlsCAFile"] = certifi.where()
except ImportError:
    pass

# 2. MongoDB Client Setup
mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"], **tls_kwargs)
turns_col = mongo_client["language_tutor"]["turns"]

# 3. Upstash Redis Clients Setup
# Synchronous Redis client for background workers and context loaders
redis_client = SyncRedis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

# Asynchronous Redis client for the main Pipecat pipeline
redis_async_client = AsyncRedis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

# 4. Supabase Client Setup
supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client | None = None
if supabase_url and supabase_key:
    supabase = create_client(supabase_url, supabase_key)

# 5. Shared Pipeline Turn Persistence and Job Enqueuers
import json
from datetime import datetime, timezone

# Retry decorator for transient DB/network failures
_db_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.1, max=2),
    reraise=True,
)

@_db_retry
async def write_turn(session_id: str, role: str, transcript: str, turn_id: str | None = None) -> str:
    """Persist conversation turn in MongoDB. Returns the inserted turn document ID.
    Retries up to 3 times with exponential backoff on transient failures."""
    from bson.objectid import ObjectId
    doc_id = ObjectId(turn_id) if turn_id else ObjectId()
    doc = {
        "_id": doc_id,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc),
        "role": role,
        "transcript": transcript,
        "corrections": None  # Filled asynchronously by grammar_worker
    }
    result = await turns_col.insert_one(doc)
    inserted_id = str(result.inserted_id)
    print(f"[Pipeline] Turn persisted to MongoDB: {role} -> ID: {inserted_id}")
    return inserted_id

@_db_retry
async def enqueue_grammar_job(session_id: str, turn_id: str):
    """Enqueues user turn for asynchronous grammar analysis in Redis (FIFO).
    Retries up to 3 times with exponential backoff on transient failures."""
    payload = json.dumps({"sessionId": session_id, "turnId": turn_id})
    await redis_async_client.rpush("grammar_jobs", payload)
    print(f"[Pipeline] Grammar job enqueued for turn {turn_id} (FIFO)")
