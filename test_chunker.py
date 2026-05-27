import asyncio
from agent.processors.text_chunker import ClauseBoundaryTextChunker
from pipecat.frames.frames import TextFrame, LLMFullResponseStartFrame, LLMFullResponseEndFrame

async def main():
    chunker = ClauseBoundaryTextChunker()
    
    # Mock push_frame
    emitted = []
    async def mock_push_frame(frame, direction):
        if isinstance(frame, TextFrame):
            emitted.append(frame.text)
    chunker.push_frame = mock_push_frame
    
    await chunker.process_frame(LLMFullResponseStartFrame(), None)
    
    text = 'Let\'s start by looking at a menu, which is called "el menú" in Spanish, and it means "the menu".'
    for char in text:
        await chunker.process_frame(TextFrame(char), None)
        
    await chunker.process_frame(LLMFullResponseEndFrame(), None)
    
    print("Emitted frames:")
    for e in emitted:
        print(f"'{e}'")

asyncio.run(main())
