from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TextFrame,
)
from utils.text import drain_clause_boundary_phrases

class ClauseBoundaryTextChunker(FrameProcessor):
    """
    Buffer streaming LLM text and forward phrase-sized TextFrames at punctuation
    boundaries (comma, period, question mark, etc.) so TTS/audio can start early
    without synthesizing every token separately.

    Enhancement: Single-word clause phrases are buffered and grouped with the next phrase
    to prevent unnatural speech pauses in the TTS pipeline.
    """

    def __init__(self):
        super().__init__()
        self._buffer = ""
        self._pending_phrase = ""

    def is_single_word(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        words = stripped.split()
        return len(words) <= 1

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._pending_phrase = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            self._buffer += frame.text
            phrases, self._buffer = drain_clause_boundary_phrases(self._buffer)
            for phrase in phrases:
                if self._pending_phrase:
                    combined = self._pending_phrase + " " + phrase
                    if self.is_single_word(combined):
                        self._pending_phrase = combined
                    else:
                        await self.push_frame(TextFrame(combined), direction)
                        self._pending_phrase = ""
                else:
                    if self.is_single_word(phrase):
                        self._pending_phrase = phrase
                    else:
                        await self.push_frame(TextFrame(phrase), direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            # Flush any remaining text from the drain buffer
            if self._buffer.strip():
                phrases, remainder = drain_clause_boundary_phrases(self._buffer)
                if remainder.strip():
                    phrases.append(remainder.strip())
                self._buffer = ""
                
                for phrase in phrases:
                    if self._pending_phrase:
                        combined = self._pending_phrase + " " + phrase
                        if self.is_single_word(combined):
                            self._pending_phrase = combined
                        else:
                            await self.push_frame(TextFrame(combined), direction)
                            self._pending_phrase = ""
                    else:
                        if self.is_single_word(phrase):
                            self._pending_phrase = phrase
                        else:
                            await self.push_frame(TextFrame(phrase), direction)
            
            # Flush any remaining pending phrase
            if self._pending_phrase.strip():
                await self.push_frame(TextFrame(self._pending_phrase.strip()), direction)
                self._pending_phrase = ""
                
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
