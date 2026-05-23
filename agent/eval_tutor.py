import os
import sys
import json
from dotenv import load_dotenv
from groq import Groq

# Ensure we can import from the agent directory
sys.path.append(os.path.dirname(__file__))
from pipeline import build_system_prompt
from prompts.grammar_policy import GRAMMAR_SYSTEM_PROMPT

load_dotenv()

# We use the fast/cheap model for evaluations to avoid rate limits or permission issues.
EVAL_MODEL = "llama-3.1-8b-instant"
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SCENARIO_PROMPT = """
Scenario: Ordering at a Cafe
Vocabulary targets: el cafe (coffee), el agua (water).
"""

def test_tutor_brevity_and_tags():
    print("Running Test 1: Tutor Brevity...")
    messages = [
        {"role": "system", "content": build_system_prompt(SCENARIO_PROMPT)},
        {"role": "user", "content": "Hi, I'm ready to learn. What's the first word?"}
    ]
    
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=messages,
        max_tokens=150,
        temperature=0.3
    )
    
    content = response.choices[0].message.content.strip()
    print(f"Agent Response:\n{content}\n")
    
    # Assertions
    # The tutor should NOT emit <lesson_item> tags anymore as per user request #1.
    assert "<lesson_item>" not in content, "Failed: Tutor emitted deprecated <lesson_item> tags."
    
    words = content.split()
    assert len(words) < 50, f"Failed: Tutor response was too verbose ({len(words)} words). Should be 1-2 short sentences."
    print("✅ Test 1 Passed\n")


def test_tutor_english_fallback():
    print("Running Test 2: Tutor falls back to English when asked for help...")
    messages = [
        {"role": "system", "content": build_system_prompt(SCENARIO_PROMPT)},
        {"role": "user", "content": "What does 'el cafe' mean? Explain in English please, I'm confused."}
    ]
    
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=messages,
        max_tokens=150,
        temperature=0.3
    )
    
    content = response.choices[0].message.content.strip()
    print(f"Agent Response:\n{content}\n")
    
    # Assertions
    # We want to ensure the agent responded in English, not just blindly continuing in Spanish.
    assert "coffee" in content.lower(), "Failed: Tutor did not explain the word 'coffee' in English."
    print("✅ Test 2 Passed\n")


def test_grammar_worker_ignores_accents():
    print("Running Test 3: Grammar worker ignores accent marks (tildes) as transcription artifacts...")
    
    # Simulate a STT artifact where the user said "hola" but STT output "holá" or they said "el menu" and STT output "el menú"
    # We want to ensure it DOES NOT flag this as a grammar error since the prompt explicitly says to ignore accent marks.
    test_utterance = "Quiero el menú, por favor."
    
    messages = [
        {"role": "system", "content": GRAMMAR_SYSTEM_PROMPT},
        {"role": "user", "content": test_utterance}
    ]
    
    response = client.chat.completions.create(
        model=EVAL_MODEL,
        messages=messages,
        max_tokens=200,
        temperature=0.1
    )
    
    raw_content = response.choices[0].message.content.strip()
    print(f"Grammar Worker Response:\n{raw_content}\n")
    
    # Extract JSON ignoring <think> tags if present
    clean_content = raw_content
    if "</think>" in clean_content:
        clean_content = clean_content.split("</think>")[1].strip()
    if clean_content.startswith("```"):
        lines = clean_content.split("\n")[1:-1]
        clean_content = "\n".join(lines).strip()
        
    try:
        corrections = json.loads(clean_content)
        error = corrections.get("error")
        correction = corrections.get("correction")
        is_clean = (error is None) or (error == correction) or (error == "null")
        assert is_clean, f"Failed: Grammar worker incorrectly flagged an error: {error} -> {correction}"
        print("✅ Test 3 Passed\n")
    except json.JSONDecodeError:
        print("❌ Test 3 Failed: Invalid JSON output.")


if __name__ == "__main__":
    if not os.environ.get("GROQ_API_KEY"):
        print("Please set GROQ_API_KEY environment variable to run evaluations.")
        sys.exit(1)
        
    print("Starting Evaluation Harness...\n" + "="*40 + "\n")
    
    try:
        test_tutor_brevity_and_tags()
        test_tutor_english_fallback()
        test_grammar_worker_ignores_accents()
        print("🎉 All regression tests passed!")
    except AssertionError as e:
        print(f"❌ Assertion Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected Error: {e}")
        sys.exit(1)
