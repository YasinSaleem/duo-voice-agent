import argparse
import asyncio
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv

try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass

from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.frames.frames import Frame, TranscriptionFrame, TextFrame, EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.deepgram.tts import DeepgramTTSService

DEFAULT_SYSTEM_PROMPT = (
    "You are a warm, patient, beginner-first Spanish tutor for a live voice lesson. "
    "Keep replies short and easy to process in audio: usually 1-2 short sentences. "
    "Teach targets in order, explain in English, then say Spanish, then ask for repetition. "
    "Increase English guidance if the learner struggles."
)

DEFAULT_USER_TEXT = (
    "I am ready to start. Please introduce the first vocabulary target and ask me to repeat it."
)


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class TextFrameLogger(FrameProcessor):
    def __init__(self, label: str) -> None:
        super().__init__()
        self.label = label
        self.first_ts: float | None = None
        self._start_ts: float = 0.0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            now = time.perf_counter()
            if self.first_ts is None:
                self.first_ts = now
                print(f"First {self.label} at +{now - self._start_ts:.3f}s")
        await self.push_frame(frame, direction)

    def bind_start(self, start_ts: float) -> None:
        self._start_ts = start_ts


class AudioFrameLogger(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.first_ts: float | None = None
        self._start_ts: float = 0.0
        self._first_audio_event = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        name = type(frame).__name__.lower()
        if "audio" in name or hasattr(frame, "audio") or hasattr(frame, "samples"):
            now = time.perf_counter()
            if self.first_ts is None:
                self.first_ts = now
                print(f"First audio frame at +{now - self._start_ts:.3f}s")
                self._first_audio_event.set()
        await self.push_frame(frame, direction)

    def bind_start(self, start_ts: float) -> None:
        self._start_ts = start_ts

    async def wait_for_first_audio(self, timeout: float) -> bool:
        try:
            await asyncio.wait_for(self._first_audio_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False


async def main() -> int:
    parser = argparse.ArgumentParser(description="Trace Pipecat streaming timings.")
    parser.add_argument("--system-prompt-file", help="Path to a system prompt file for production-equivalent testing.")
    parser.add_argument("--user-text", help="Override the default user text.")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.getenv("GROQ_API_KEY")
    tts_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key or not tts_key:
        print("Missing GROQ_API_KEY or DEEPGRAM_API_KEY in environment.")
        return 1

    system_prompt = os.getenv("AGENT_SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT
    if args.system_prompt_file:
        with open(args.system_prompt_file, "r", encoding="utf-8") as handle:
            system_prompt = handle.read().strip()

    user_text = args.user_text or DEFAULT_USER_TEXT

    model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    voice = os.getenv("DEEPGRAM_VOICE", "aura-2-selena-es")

    context = LLMContext([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ])

    context_pair = LLMContextAggregatorPair(context)
    user_agg = context_pair.user()
    assistant_agg = context_pair.assistant()

    llm = GroqLLMService(
        api_key=api_key,
        settings=GroqLLMService.Settings(model=model_name)
    )

    tts = DeepgramTTSService(
        api_key=tts_key,
        settings=DeepgramTTSService.Settings(voice=voice)
    )

    llm_text_logger = TextFrameLogger("LLM token")
    tts_input_logger = TextFrameLogger("TTS input")
    audio_logger = AudioFrameLogger()

    pipeline = Pipeline([
        user_agg,
        llm,
        llm_text_logger,
        tts_input_logger,
        tts,
        audio_logger,
        assistant_agg,
    ])

    task = PipelineTask(pipeline)

    print("=== Pipecat Streaming Trace ===")
    print(f"Model: {model_name}")
    print(f"Voice: {voice}")
    print(f"Request start (UTC): {_utc_timestamp()}")

    start_ts = time.perf_counter()
    llm_text_logger.bind_start(start_ts)
    tts_input_logger.bind_start(start_ts)
    audio_logger.bind_start(start_ts)

    await task.queue_frame(
        TranscriptionFrame(
            text=user_text,
            user_id="trace-user",
            timestamp=datetime.now(timezone.utc).isoformat(),
            language="en",
            result=None,
            finalized=True,
        )
    )
    runner = PipelineRunner()
    run_task = asyncio.create_task(runner.run(task))

    got_audio = await audio_logger.wait_for_first_audio(timeout=8.0)
    if not got_audio:
        print("No audio frame observed before timeout. Forcing pipeline shutdown.")

    await task.queue_frame(EndFrame())
    await run_task

    end_ts = time.perf_counter()
    print(f"Request end (UTC): {_utc_timestamp()}")
    print(f"Total completion time: {end_ts - start_ts:.3f}s")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
