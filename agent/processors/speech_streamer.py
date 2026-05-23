import json
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, TextFrame
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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            try:
                await redis.set(f"agent_speaking:{self.session_id}", "1", ex=30)
                await redis.set(f"agent_speaking_text:{self.session_id}", frame.text, ex=30)
                payload = json.dumps({"type": "tutor_text_chunk", "text": frame.text})
                await self.transport.send_message(payload)
            except Exception as e:
                print(f"[TutorSpeechStreamer] Error: {e}")
        await self.push_frame(frame, direction)
