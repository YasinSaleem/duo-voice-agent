import asyncio
import json
import os
import re
import time
from dotenv import load_dotenv
load_dotenv()

# Secure macOS SSL certificate environment override to resolve CERTIFICATE_VERIFY_FAILED
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass


from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from upstash_redis import Redis

# ── Services Initialization ───────────────────────────────────────────────────
groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])

tls_kwargs = {}
try:
    import certifi
    tls_kwargs["tlsCAFile"] = certifi.where()
except ImportError:
    pass

mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"], **tls_kwargs)
turns_col = mongo_client["language_tutor"]["turns"]

redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

# ── Grammar Evaluation Specifications ─────────────────────────────────────────
GRAMMAR_SYSTEM_PROMPT = """
You are a Spanish grammar checker for a live voice tutoring app.
Given a spoken Spanish or mixed Spanish-English utterance from a learner, identify ONE helpful spoken-Spanish grammar correction if present.

Important rules:
- Only flag issues that matter for spoken Spanish grammar.
- Never flag punctuation, commas, capitalization, quotation marks, or typing-style issues.
- Ignore missing or incorrect accent marks (tildes), as this is an artifact of the speech-to-text transcript and not a spoken error.
- Do not correct pure English help requests, names by themselves, fillers, acknowledgements, or punctuation-only noise.
- If the learner's utterance is already acceptable, too short to judge, or not clearly a grammar mistake, return null values.
- Prefer no correction over a low-confidence or nitpicky correction.

Keep your internal <think> reasoning extremely brief and concise (under 2 sentences).
You MUST output the final response ONLY in this exact JSON shape (do not include any conversational text outside the JSON):
{
  "error": "<what they said>",
  "correction": "<correct form>",
  "explanation": "<one sentence in English>"
}
If there is no grammar error, return a valid JSON object with null values:
{
  "error": null,
  "correction": null,
  "explanation": null
}
"""

LOW_SIGNAL_RE = re.compile(r"^[\W_]+$")
HELP_REQUEST_RE = re.compile(
    r"\b(i don't understand|i do not understand|can you explain|in english|what does that mean|no entiendo|expl[ií]calo en ingl[eé]s)\b",
    re.IGNORECASE,
)

def should_skip_grammar_check(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return True
    if LOW_SIGNAL_RE.fullmatch(normalized):
        return True
    if HELP_REQUEST_RE.search(normalized):
        return True
    return False

async def process_job(payload: dict):
    turn_id = payload.get("turnId")
    if not turn_id:
        return
        
    doc = await turns_col.find_one({"_id": ObjectId(turn_id)})
    if not doc or not doc.get("transcript"):
        print(f"[GrammarWorker] Document not found or missing transcript: {turn_id}")
        return

    user_text = doc["transcript"]
    print(f"[GrammarWorker] Analyzing user turn: '{user_text}'")

    if should_skip_grammar_check(user_text):
        corrections = {"error": None, "correction": None, "explanation": None}
        await turns_col.update_one(
            {"_id": ObjectId(turn_id)},
            {"$set": {"corrections": corrections}}
        )
        print(f"[GrammarWorker] Skipped grammar check for low-signal or help-request turn: {turn_id}")
        return

    try:
        response = groq_client.chat.completions.create(
            model="qwen/qwen3-32b",
            messages=[
                {"role": "system", "content": GRAMMAR_SYSTEM_PROMPT},
                {"role": "user", "content": user_text}
            ],
            max_tokens=2048
        )
        
        raw_content = response.choices[0].message.content.strip()
        print(f"[GrammarWorker] Raw Groq response: {raw_content}")
        
        # Strip thinking tags and markdown backticks to extract the raw JSON object
        clean_content = raw_content
        if "<think>" in clean_content and "</think>" in clean_content:
            parts = clean_content.split("</think>", 1)
            if len(parts) > 1:
                clean_content = parts[1].strip()
        
        if clean_content.startswith("```"):
            lines = clean_content.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            clean_content = "\n".join(lines).strip()
            if clean_content.endswith("```"):
                clean_content = clean_content[:-3].strip()
                
        # Load and validate json structure
        corrections = json.loads(clean_content)
    except Exception as e:
        print(f"[GrammarWorker] Error parsing Groq corrections: {e}")
        corrections = None

    # Write corrections back to MongoDB turn document
    await turns_col.update_one(
        {"_id": ObjectId(turn_id)},
        {"$set": {"corrections": corrections}}
    )
    print(f"[GrammarWorker] Updated MongoDB for turn {turn_id} with: {corrections}")

async def main():
    print("[GrammarWorker] Starting background grammar checker worker...")
    print("[GrammarWorker] Listening on queue: 'grammar_jobs'...")
    
    # Polling loop using LPOP (since blpop/brpop are not supported in upstash_redis REST client)
    while True:
        try:
            result = redis.lpop("grammar_jobs")
            if result:
                # Upstash Redis SDK's LPOP returns the serialized string directly
                # Parse the JSON payload
                payload = json.loads(result)
                await process_job(payload)
                # Process next item immediately without sleeping
                continue
        except Exception as e:
            print(f"[GrammarWorker] Queue polling lifecycle error: {e}")
        
        # Sleep for 1.0 second if no job is present (prevents high CPU / serverless rate limit exhaust)
        await asyncio.sleep(1.0)

if __name__ == "__main__":
    asyncio.run(main())
