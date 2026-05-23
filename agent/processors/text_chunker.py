from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseStartFrame,
    LLMFullResponseEndFrame,
    TextFrame,
)
from agent.utils.text import drain_clause_boundary_phrases

class ClauseBoundaryTextChunker(FrameProcessor):
    """
    Buffer streaming LLM text and forward phrase-sized TextFrames at punctuation
    boundaries (comma, period, question mark, etc.) so TTS/audio can start early
    without synthesizing every token separately.

    The first speakable phrase is always forwarded immediately (including short
  encouragements like "¡Perfecto!"). Later single-word fragments may be held
    briefly so they merge with the following phrase (e.g. "Muy" + "bien.").
    """

    def __init__(self):
        super().__init__()
        self._buffer = ""
        self._pending_phrase = ""
        self._has_emitted = False

    def is_single_word(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False
        words = stripped.split()
        return len(words) <= 1

    def _should_hold_single_word(self, phrase: str) -> bool:
        return self.is_single_word(phrase) and self._has_emitted

    async def _emit_phrase(self, phrase: str, direction: FrameDirection) -> None:
        stripped = phrase.strip()
        if not stripped:
            return
        await self.push_frame(TextFrame(stripped), direction)
        self._has_emitted = True

    async def _emit_phrases(self, phrases: list[str], direction: FrameDirection) -> None:
        for phrase in phrases:
            if self._pending_phrase:
                combined = f"{self._pending_phrase} {phrase}".strip()
                if self._should_hold_single_word(combined):
                    self._pending_phrase = combined
                else:
                    await self._emit_phrase(combined, direction)
                    self._pending_phrase = ""
            elif self._should_hold_single_word(phrase):
                self._pending_phrase = phrase.strip()
            else:
                await self._emit_phrase(phrase, direction)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            self._pending_phrase = ""
            self._has_emitted = False
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            self._buffer += frame.text
            phrases, self._buffer = drain_clause_boundary_phrases(self._buffer)
            await self._emit_phrases(phrases, direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._buffer.strip():
                phrases, remainder = drain_clause_boundary_phrases(self._buffer)
                if remainder.strip():
                    phrases.append(remainder.strip())
                self._buffer = ""
                await self._emit_phrases(phrases, direction)

            if self._pending_phrase.strip():
                await self._emit_phrase(self._pending_phrase, direction)
                self._pending_phrase = ""

            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)
