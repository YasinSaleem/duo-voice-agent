import os
import sys
import asyncio
from bson import ObjectId

from agent.voice_agent import _mint_agent_token
from agent.core.db import write_turn, enqueue_grammar_job, redis_async_client as redis, turns_col
from agent.processors.turn_buffer import UserTurnBufferProcessor

async def test_pipeline_integration():
    print("[verify_pipeline] Starting pipeline integration and DB tests...")
    
    session_id = "test_pipeline_session"
    
    # 1. Test LiveKit Token Minting
    print("[verify_pipeline] Testing LiveKit token minting...")
    token = _mint_agent_token(session_id)
    assert token.startswith("eyJ"), f"Expected JWT token starting with 'eyJ', got '{token}'"
    print(f"[verify_pipeline] LiveKit token minted successfully: {token[:25]}...")
    
    # 2. Test MongoDB Turn Persistence
    print("[verify_pipeline] Testing MongoDB turn persistence...")
    role = "user"
    transcript = "Hola, me gustaría comprar una ensalada grande."
    
    turn_id = await write_turn(session_id, role, transcript)
    assert turn_id is not None, "Expected valid turn ID"
    assert len(turn_id) == 24, f"Expected 24-character ObjectId string, got '{turn_id}'"
    print(f"[verify_pipeline] MongoDB write turn success. Document ID: {turn_id}")
    
    # Verify the document exists in MongoDB
    doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    assert doc is not None, "Expected document to exist in MongoDB"
    assert doc["session_id"] == session_id
    assert doc["role"] == role
    assert doc["transcript"] == transcript
    assert doc["corrections"] is None
    print("[verify_pipeline] MongoDB document values validated successfully.")
    
    await enqueue_grammar_job(session_id, turn_id)
    
    # Pop the item back from Redis to verify FIFO queue pop and matching payload
    result = await redis.lpop("grammar_jobs")
    assert result is not None, "Expected enqueued job in Redis"
    print(f"[verify_pipeline] Popped job payload from Redis: {result}")
    
    job_payload = json_loads_safe(result)
    assert job_payload["sessionId"] == session_id
    assert job_payload["turnId"] == turn_id
    print("[verify_pipeline] Redis FIFO queue payload matches enqueued values.")
    
    # 4. Clean up MongoDB
    print("[verify_pipeline] Cleaning up database...")
    await turns_col.delete_one({"_id": ObjectId(turn_id)})
    
    # Check deleted
    doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    assert doc is None, "Expected document to be deleted"
    print("[verify_pipeline] MongoDB database cleaned up successfully.")
    
    # 5. Verify FrameProcessor Instantiation
    print("[verify_pipeline] Testing UserTurnBufferProcessor instantiation...")
    processor = UserTurnBufferProcessor(session_id, transport=None)
    assert processor is not None
    print("[verify_pipeline] UserTurnBufferProcessor instantiated cleanly.")
    
    print("\n[verify_pipeline] === SUCCESS: Pipeline Integration verified successfully! ===\n")

def json_loads_safe(data):
    # upstash_redis might return string or bytes depending on configuration
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    import json
    return json.loads(data)

if __name__ == "__main__":
    try:
        asyncio.run(test_pipeline_integration())
    except AssertionError as e:
        print(f"[verify_pipeline] FAILURE: Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[verify_pipeline] FAILURE: Unexpected error: {e}")
        sys.exit(1)
