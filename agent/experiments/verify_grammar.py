import os
import sys
import asyncio
from bson import ObjectId

from agent.workers.grammar_worker import process_job, turns_col, redis

async def test_grammar_worker_integration():
    print("[verify_grammar] Starting grammar worker integration and AI checks...")
    
    session_id = "verify_grammar_session"
    
    # 1. Create a mock user turn with a deliberate grammar mistake
    print("[verify_grammar] Writing mock turn with grammar error to MongoDB...")
    mock_turn = {
        "session_id": session_id,
        "timestamp": None,
        "role": "user",
        "transcript": "Yo tener dos perros y ella gusta los gatos.", # errors: "tener" -> "tengo", "ella gusta los gatos" -> "a ella le gustan los gatos"
        "corrections": None
    }
    result = await turns_col.insert_one(mock_turn)
    turn_id = str(result.inserted_id)
    assert turn_id is not None, "Expected valid turn ID"
    print(f"[verify_grammar] Mock turn persisted to MongoDB. Document ID: {turn_id}")
    
    # 2. Invoke grammar processing directly using the payload
    payload = {"sessionId": session_id, "turnId": turn_id}
    print("[verify_grammar] Invoking process_job with mock payload...")
    await process_job(payload)
    
    # 3. Retrieve and inspect the updated turn document from MongoDB
    print("[verify_grammar] Retrieving updated document from MongoDB...")
    updated_doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    assert updated_doc is not None, "Expected document to exist"
    
    corrections = updated_doc.get("corrections")
    print(f"[verify_grammar] Corrections stored in DB: {corrections}")
    
    # Validate structure of corrections
    assert corrections is not None, "Expected corrections to be populated by Groq"
    assert isinstance(corrections, dict), f"Expected corrections to be a dictionary, got {type(corrections)}"
    
    # The prompt requests: { "error": "...", "correction": "...", "explanation": "..." }
    assert "error" in corrections, "Expected 'error' in corrections"
    assert "correction" in corrections, "Expected 'correction' in corrections"
    assert "explanation" in corrections, "Expected 'explanation' in corrections"
    
    print(f"[verify_grammar] Verified correction: {corrections.get('error')} -> {corrections.get('correction')}")
    print(f"[verify_grammar] Explanation: {corrections.get('explanation')}")
    
    # 4. Clean up MongoDB
    print("[verify_grammar] Cleaning up database...")
    await turns_col.delete_one({"_id": ObjectId(turn_id)})
    
    # Double-check cleanup
    clean_doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    assert clean_doc is None, "Expected document to be deleted"
    print("[verify_grammar] MongoDB cleaned up successfully.")
    
    print("\n[verify_grammar] === SUCCESS: Grammar Worker integration verified successfully! ===\n")

if __name__ == "__main__":
    try:
        asyncio.run(test_grammar_worker_integration())
    except AssertionError as e:
        print(f"[verify_grammar] FAILURE: Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"[verify_grammar] FAILURE: Unexpected error: {e}")
        sys.exit(1)
