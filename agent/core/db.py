import os
from motor.motor_asyncio import AsyncIOMotorClient
from upstash_redis import Redis as SyncRedis
from upstash_redis.asyncio import Redis as AsyncRedis
from supabase import create_client, Client

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
