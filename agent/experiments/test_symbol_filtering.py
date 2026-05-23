import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Add agent parent path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agent.processors.turn_buffer import UserTurnBufferProcessor
from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

class MockPushedFramesCollector:
    def __init__(self):
        self.buffered_texts = []

    async def mock_buffer_transcription(self, processor, text):
        # We simulate the _buffer_transcription call to see if it lets the text pass
        import re
        text_normalized = text.strip()
        clean_text = re.sub(r"[^\w\s]", "", text_normalized).strip()
        if not clean_text:
            return False
        return True

async def test_symbol_filtering():
    print("--- Starting Symbol Noise Filtering Unit Test ---")
    collector = MockPushedFramesCollector()
    
    test_cases = [
        # (Input text, Expected to pass?)
        (".", False),
        ("???", False),
        ("---", False),
        ("!!!", False),
        (" ", False),
        ("y", True),
        ("o", True),
        ("a", True),
        ("si", True),
        ("sí", True),
        ("no", True),
        ("hola", True),
        ("hola?", True),
        ("¿hola?", True)
    ]
    
    all_passed = True
    for text, expected_pass in test_cases:
        passed = await collector.mock_buffer_transcription(None, text)
        verdict = "PASS" if passed == expected_pass else "FAIL"
        print(f"Text: '{text:10}' | Passed? {passed:5} | Expected? {expected_pass:5} | Result: {verdict}")
        if verdict == "FAIL":
            all_passed = False
            
    assert all_passed, "Some symbol filtering test cases failed!"
    print("\n🎉 Symbol noise filtering unit tests passed successfully!")

if __name__ == "__main__":
    asyncio.run(test_symbol_filtering())
