import asyncio
import json
import time
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, EndFrame, TranscriptionFrame
from agent.utils.text import normalize_transcript_spacing, utterance_flush_delay

class UserTurnBufferProcessor(FrameProcessor):
    """
    Buffers nearby final STT chunks into a single utterance before they reach
    the LLM or grammar queue. This avoids fragmentary turns like "Mi" or
    "no gusta" being treated as complete user messages.
    """
    def __init__(
        self,
        session_id: str,
        transport,
        on_turn_persisted=None,
        on_grammar_job_enqueued=None,
        latency_tracker=None,
    ):
        super().__init__()
        self.session_id = session_id
        self.transport = transport
        self.on_turn_persisted = on_turn_persisted
        self.on_grammar_job_enqueued = on_grammar_job_enqueued
        self._latency = latency_tracker
        self._buffered_frames: list[TranscriptionFrame] = []
        self._flush_task: asyncio.Task | None = None
        self._last_stt_chunk_ts: float | None = None
        self._pending_flush_delay: float = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            await self._buffer_transcription(frame, direction)
            return

        if isinstance(frame, EndFrame):
            await self._flush_buffer(direction)

        await self.push_frame(frame, direction)

    async def _buffer_transcription(self, frame: TranscriptionFrame, direction: FrameDirection):
        now = time.perf_counter()
        text = normalize_transcript_spacing(frame.text)
        if not text:
            return

        # Strip all punctuation/symbols to check if there is any actual linguistic content
        import re
        clean_text = re.sub(r"[^\w\s]", "", text).strip()
        if not clean_text:
            print(f"[UserTurnBufferProcessor] Discarding STT noise/symbol-only artifact: '{text}'")
            return

        self._last_stt_chunk_ts = now
        print(f"[UserTurnBufferProcessor] Buffering transcript chunk: {text}")
        buffered_frame = TranscriptionFrame(
            text=text,
            user_id=frame.user_id,
            timestamp=frame.timestamp,
            language=frame.language,
            result=frame.result,
            finalized=frame.finalized,
        )
        self._buffered_frames.append(buffered_frame)

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

        delay = utterance_flush_delay(" ".join(item.text for item in self._buffered_frames))
        self._pending_flush_delay = delay
        self._flush_task = asyncio.create_task(self._flush_after_delay(delay, direction))

    async def _flush_after_delay(self, delay: float, direction: FrameDirection):
        try:
            await asyncio.sleep(delay)
            await self._flush_buffer(direction)
        except asyncio.CancelledError:
            pass

    async def _flush_buffer(self, direction: FrameDirection):
        now = time.perf_counter()
        if self._flush_task and self._flush_task.done():
            self._flush_task = None

        if not self._buffered_frames:
            return

        frames_to_flush = self._buffered_frames
        self._buffered_frames = []

        merged_text = normalize_transcript_spacing(" ".join(frame.text for frame in frames_to_flush))
        last_frame = frames_to_flush[-1]
        merged_frame = TranscriptionFrame(
            text=merged_text,
            user_id=last_frame.user_id,
            timestamp=last_frame.timestamp,
            language=last_frame.language,
            result=last_frame.result,
            finalized=True,
        )

        if self._latency and self._last_stt_chunk_ts is not None:
            proxy_ts = self._last_stt_chunk_ts
            if self._pending_flush_delay > 0:
                proxy_ts = max(0.0, now - self._pending_flush_delay)
            self._latency.on_user_stop_proxy(proxy_ts, source="utterance_flush_proxy")

        print(f"[UserTurnBufferProcessor] Flushing merged user turn: {merged_text}")
        
        turn_id = None
        if self.on_turn_persisted:
            turn_id = await self.on_turn_persisted(self.session_id, "user", merged_text)
            
        if turn_id and self.on_grammar_job_enqueued:
            await self.on_grammar_job_enqueued(self.session_id, turn_id)

        try:
            payload = json.dumps({
                "type": "user_turn_final",
                "turnId": turn_id if turn_id else "",
                "text": merged_text,
            })
            await self.transport.send_message(payload)
        except Exception as e:
            print(f"[UserTurnBufferProcessor] Error sending user turn event: {e}")
        if self._latency:
            self._latency.record_merged_user_frame()
        await self.push_frame(merged_frame, direction)
