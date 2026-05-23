import asyncio
import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to sys.path so we can import pipeline
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import ClauseBoundaryTextChunker
from pipecat.frames.frames import (
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection

class MockPushedFramesCollector:
    def __init__(self):
        self.pushed_frames = []

    async def push_frame(self, frame, direction):
        self.pushed_frames.append(frame)

async def run_test():
    print("--- Starting Single-Word TTS Grouping Unit Test ---")
    chunker = ClauseBoundaryTextChunker()
    collector = MockPushedFramesCollector()
    
    # Set the chunker's push_frame method to our collector's push_frame
    chunker.push_frame = collector.push_frame
    
    # Test case 1: "Hola. ¿Cómo estás?"
    # "Hola." is a single word, should be grouped with "¿Cómo estás?".
    print("\nTest Case 1: 'Hola. ¿Cómo estás?'")
    await chunker.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(TextFrame("Hola"), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(TextFrame(". ¿Cómo estás?"), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    
    for f in collector.pushed_frames:
        if isinstance(f, TextFrame):
            print(f"  Pushed: '{f.text}'")
            
    # Reset collector
    collector.pushed_frames = []
    
    # Test case 2: "Yes. Indeed. Let's do that."
    # "Yes." (single word), "Indeed." (single word), combined = "Yes. Indeed." (2 words, not single).
    # Then "Let's do that." (multi word).
    print("\nTest Case 2: 'Yes. Indeed. Let's do that.'")
    await chunker.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(TextFrame("Yes. Indeed. Let's do that."), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    
    for f in collector.pushed_frames:
        if isinstance(f, TextFrame):
            print(f"  Pushed: '{f.text}'")
            
    # Reset collector
    collector.pushed_frames = []
    
    # Test case 3: Response ending with a single word "Blanco."
    print("\nTest Case 3: Response ending with single word: 'El carro es Blanco.'")
    await chunker.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(TextFrame("El carro es"), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(TextFrame(" Blanco."), FrameDirection.DOWNSTREAM)
    await chunker.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)
    
    for f in collector.pushed_frames:
        if isinstance(f, TextFrame):
            print(f"  Pushed: '{f.text}'")

    print("\n--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(run_test())
