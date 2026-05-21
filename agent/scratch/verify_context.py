import os
import sys
import json

# Add parent directory to sys.path to enable imports from agent/
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from context_loader import load_prior_context, redis

def test_context_loader():
    print("[verify_context] Starting context loader verification tests...")
    
    session_id = "test_resumption_session"
    redis_key = f"resume:{session_id}"
    
    # 1. Define mock history payload as written by the API on resume
    mock_turns = [
        {"role": "user", "transcript": "Hola, me gustaría comprar café.", "timestamp": "2026-05-21T12:00:00Z"},
        {"role": "agent", "transcript": "¡Hola! Claro, ¿qué tamaño prefieres?", "timestamp": "2026-05-21T12:01:00Z"}
    ]
    
    # 2. Seed Upstash Redis
    redis.set(redis_key, json.dumps(mock_turns), ex=60)
    print(f"[verify_context] Seeded '{redis_key}' in Redis.")
    
    # 3. Call loader
    messages = load_prior_context(session_id)
    print(f"[verify_context] Loaded context messages: {messages}")
    
    # 4. Assert correctness
    assert len(messages) == 2, f"Expected 2 messages, got {len(messages)}"
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "Hola, me gustaría comprar café."
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "¡Hola! Claro, ¿qué tamaño prefieres?"
    print("[verify_context] Verified message structure and role mapping.")
    
    # 5. Assert key was deleted (consumed)
    raw = redis.get(redis_key)
    assert raw is None, f"Expected cache key '{redis_key}' to be consumed and deleted, but it still exists!"
    print("[verify_context] Verified cache consumption (key was deleted).")
    
    print("\n[verify_context] === SUCCESS: Context Loader verified successfully! ===\n")

if __name__ == "__main__":
    try:
        test_context_loader()
    except AssertionError as e:
        print(f"[verify_context] FAILURE: Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[verify_context] FAILURE: Unexpected error: {e}")
        sys.exit(1)
