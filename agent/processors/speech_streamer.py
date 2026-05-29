import asyncio
import json
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, TextFrame, InterruptionFrame
from agent.core.db import redis_async_client as redis

class TutorSpeechStreamer(FrameProcessor):
    """
    Frame processor placed between the LLM and TTS to stream tutor text chunks
    in real-time to the client via the LiveKit data channel.
    """
    def __init__(self, transport, session_id: str):
        super().__init__()
        self.transport = transport
        self.session_id = session_id
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(self._queue_worker())

    async def _queue_worker(self) -> None:
        try:
            while True:
                text = await self._queue.get()
                try:
                    await self._notify_client(text)
                except Exception as e:
                    print(f"[TutorSpeechStreamer] Worker error: {e}")
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass

    async def _notify_client(self, text: str) -> None:
        try:
            # 1. Send WebSocket text chunk to client instantly!
            payload = json.dumps({"type": "tutor_text_chunk", "text": text})
            await self.transport.send_message(payload)
            print(f"[TutorSpeechStreamer] Sent chunk to client instantly: '{text}'")

            # 2. Perform Redis speaks-indicator writes non-blockingly in the background
            async def _bg_redis_updates():
                try:
                    await redis.set(f"agent_speaking:{self.session_id}", "1", ex=30)
                    await redis.set(f"agent_speaking_text:{self.session_id}", text, ex=30)
                except Exception as re:
                    print(f"[TutorSpeechStreamer] Redis background write error: {re}", flush=True)

            asyncio.create_task(_bg_redis_updates())
        except Exception as e:
            print(f"[TutorSpeechStreamer] Error: {e}", flush=True)

    def _schedule_notify(self, text: str) -> None:
        self._queue.put_nowait(text)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self._schedule_notify(frame.text)
        elif isinstance(frame, InterruptionFrame):
            print("[TutorSpeechStreamer] Interruption detected! Clearing queued text and notifying client.", flush=True)
            # Clear pending text queue immediately
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
            
            # Send interruption signal to client non-blockingly in the background
            async def _bg_notify():
                try:
                    payload = json.dumps({"type": "tutor_text_interrupted"})
                    await self.transport.send_message(payload)
                except Exception as e:
                    print(f"[TutorSpeechStreamer] Interruption notify error: {e}", flush=True)
            
            asyncio.create_task(_bg_notify())

            # Instantly clear Redis speaks indicators in the background
            async def _bg_redis_clear():
                try:
                    await redis.delete(f"agent_speaking:{self.session_id}")
                    await redis.delete(f"agent_speaking_text:{self.session_id}")
                except Exception as re:
                    print(f"[TutorSpeechStreamer] Redis background clear error: {re}", flush=True)
            asyncio.create_task(_bg_redis_clear())

        await self.push_frame(frame, direction)

    async def cleanup(self) -> None:
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
