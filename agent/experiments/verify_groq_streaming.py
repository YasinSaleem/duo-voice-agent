import argparse
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

from groq import Groq

DEFAULT_SYSTEM_PROMPT = (
    "You are a warm, patient, beginner-first Spanish tutor for a live voice lesson. "
    "Keep replies short and easy to process in audio: usually 1-2 short sentences, "
    "occasionally 3 if needed. Ask at most one question at a time. "
    "Start in English for instruction and structure, then add Spanish gradually. "
    "Teach each target in order: explain in English, give Spanish, then ask the learner to repeat. "
    "Do not move on until mastery (2 correct repetitions OR 1 correct contextual use). "
    "If the learner struggles, increase English guidance and simplify. "
    "After all targets, say the lesson is complete and ask whether to continue practicing or end the lesson.\n\n"
    "Scenario: Tapas Bar in Barcelona. Vocabulary targets: el menu (menu), el agua (water), "
    "el cafe (coffee), la cuenta (bill), el pan (bread). Phrase targets: 'Quiero ...', "
    "'Me trae ...?', 'La cuenta, por favor.'"
)

DEFAULT_USER_PROMPT = (
    "Start a short lesson following the scenario. Introduce the first vocabulary target, "
    "explain it in English, then say the Spanish, then ask me to repeat it."
)

STREAM_GAP_THRESHOLD_SECS = 0.05
FIRST_CHUNK_EARLY_THRESHOLD_SECS = 0.30


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verdict(start_ts: float, chunk_times: list[float], end_ts: float) -> str:
    if not chunk_times:
        return "buffered_or_failed (no chunks)"

    total = end_ts - start_ts
    first = chunk_times[0] - start_ts
    gaps = [t2 - t1 for t1, t2 in zip(chunk_times, chunk_times[1:])]
    significant_gaps = [g for g in gaps if g >= STREAM_GAP_THRESHOLD_SECS]
    spread = chunk_times[-1] - chunk_times[0]

    early = first <= max(total * 0.6, FIRST_CHUNK_EARLY_THRESHOLD_SECS)
    incremental = len(significant_gaps) >= 1 or spread >= STREAM_GAP_THRESHOLD_SECS * 3

    if early and incremental:
        return "streaming (incremental chunks over time)"

    return "buffered_or_burst (chunks clustered near completion)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Groq LLM streaming behavior.")
    parser.add_argument("--system-prompt-file", help="Path to a system prompt file for production-equivalent testing.")
    parser.add_argument("--user-prompt", help="Override the default user prompt.")
    args = parser.parse_args()

    load_dotenv()

    system_prompt = os.getenv("AGENT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT
    if args.system_prompt_file:
        with open(args.system_prompt_file, "r", encoding="utf-8") as handle:
            system_prompt = handle.read().strip()

    user_prompt = args.user_prompt or DEFAULT_USER_PROMPT

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("Missing GROQ_API_KEY in environment.")
        return 1

    client = Groq(api_key=api_key)
    model_name = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    print("=== Groq Streaming Verification ===")
    print(f"Model: {model_name}")
    print(f"Request start (UTC): {_utc_timestamp()}")

    start_ts = time.perf_counter()
    first_chunk_ts = None
    chunk_times: list[float] = []
    chunk_count = 0

    stream = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=512,
        temperature=0.6,
        stream=True,
    )

    for chunk in stream:
        now = time.perf_counter()
        delta = chunk.choices[0].delta.content or ""
        if delta:
            chunk_count += 1
            chunk_times.append(now)
            if first_chunk_ts is None:
                first_chunk_ts = now
                print(f"First token at +{first_chunk_ts - start_ts:.3f}s")
            print(f"Chunk {chunk_count:03d} at +{now - start_ts:.3f}s | size={len(delta)}")

    end_ts = time.perf_counter()
    print(f"Request end (UTC): {_utc_timestamp()}")
    print(f"Total completion time: {end_ts - start_ts:.3f}s")
    print(f"Total chunks: {chunk_count}")

    verdict = _verdict(start_ts, chunk_times, end_ts)
    print(f"Verdict: {verdict}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
