import core.bootstrap
import json
from core.db import redis_client as redis

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
