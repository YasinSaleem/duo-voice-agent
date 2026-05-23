import asyncio
import json
import os
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
    def __init__(self, session_id: str, transport, on_turn_persisted=None, on_grammar_job_enqueued=None):
        super().__init__()
        self.session_id = session_id
        self.transport = transport
        self.on_turn_persisted = on_turn_persisted
        self.on_grammar_job_enqueued = on_grammar_job_enqueued
        self._buffered_frames: list[TranscriptionFrame] = []
        self._flush_task: asyncio.Task | None = None
        self._latency_enabled = os.environ.get("LATENCY_TRACE", "0") == "1"
        self._first_chunk_ts: float | None = None
        self._last_chunk_ts: float | None = None
        self._last_schedule_ts: float | None = None
        self._last_scheduled_delay: float | None = None

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

        if self._latency_enabled:
            if self._first_chunk_ts is None:
                self._first_chunk_ts = now
                print(f"[Latency] first_transcription_chunk at +{self._first_chunk_ts:.3f}s")
            self._last_chunk_ts = now
            print(f"[Latency] transcription_chunk at +{now:.3f}s | finalized={frame.finalized} | text='{text}'")
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
        if self._latency_enabled:
            self._last_schedule_ts = now
            self._last_scheduled_delay = delay
            print(f"[Latency] flush_scheduled at +{now:.3f}s | delay={delay:.2f}s")
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

        if self._latency_enabled:
            first_ts = self._first_chunk_ts
            last_ts = self._last_chunk_ts
            sched_ts = self._last_schedule_ts
            sched_delay = self._last_scheduled_delay
            if first_ts is not None and last_ts is not None:
                print(
                    "[Latency] flush_executed at +{:.3f}s | "
                    "first_chunk_delta={:.3f}s | last_chunk_delta={:.3f}s | "
                    "buffer_span={:.3f}s | scheduled_delay={}".format(
                        now,
                        now - first_ts,
                        now - last_ts,
                        last_ts - first_ts,
                        "{:.2f}s".format(sched_delay) if sched_delay is not None else "n/a",
                    )
                )
                if sched_ts is not None:
                    print(
                        "[Latency] flush_delay_actual={:.3f}s (since schedule)".format(
                            now - sched_ts
                        )
                    )

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
        if self._latency_enabled:
            print(f"[Latency] merged_frame_pushed at +{time.perf_counter():.3f}s")
        await self.push_frame(merged_frame, direction)
