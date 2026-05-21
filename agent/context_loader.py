import json
import os
from dotenv import load_dotenv
from upstash_redis import Redis

# Load environment variables from .env
load_dotenv()

# Lazily initialize Redis client using environment variables
redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

def load_prior_context(session_id: str) -> list[dict]:
    """
    Reads prior turns cached by the API on session resume.
    Converts them to OpenAI message dicts and deletes the key to consume once.
    """
    key = f"resume:{session_id}"
    raw = redis.get(key)
    if not raw:
        print(f"[ContextLoader] No prior context found in Redis at key '{key}' (New session)")
        return []
    
    try:
        turns = json.loads(raw)
        messages = []
        for turn in turns:
            role = "user" if turn["role"] == "user" else "assistant"
            messages.append({"role": role, "content": turn["transcript"]})
        
        # Consume the cached key immediately after loading
        redis.delete(key)
        print(f"[ContextLoader] Successfully loaded and consumed {len(messages)} prior turns for session {session_id}")
        return messages
    except Exception as e:
        print(f"[ContextLoader] Error loading prior context: {e}")
        return []
