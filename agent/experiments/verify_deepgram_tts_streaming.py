import argparse
import os
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
import asyncio

try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, TTSSpeakFrame, EndFrame
from pipecat.services.deepgram.tts import DeepgramTTSService

DEFAULT_TEXT = (
    "Hola. Hoy vamos a practicar como pedir comida en un restaurante. "
    "Primero, el menu. Di conmigo: el menu. Luego, el agua. Di: el agua."
)

STREAM_GAP_THRESHOLD_SECS = 0.05
FIRST_CHUNK_EARLY_THRESHOLD_SECS = 0.30


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_audio_frame(frame: Frame) -> bool:
    name = type(frame).__name__.lower()
    if "audio" in name:
        return True
    return hasattr(frame, "audio") or hasattr(frame, "samples") or hasattr(frame, "data")


def _payload_size(frame: Frame) -> int:
    for attr in ("audio", "samples", "data"):
        if hasattr(frame, attr):
            value = getattr(frame, attr)
            if isinstance(value, (bytes, bytearray, memoryview)):
                return len(value)
            if hasattr(value, "__len__"):
                try:
                    return len(value)
                except TypeError:
                    return 0
    return 0


class AudioFrameLogger(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.first_audio_ts: float | None = None
        self.chunk_times: list[float] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        now = time.perf_counter()
        if _is_audio_frame(frame):
            if self.first_audio_ts is None:
                self.first_audio_ts = now
                print(f"First audio chunk at +{now - self._start_ts:.3f}s")
            self.chunk_times.append(now)
            print(
                f"Audio chunk {len(self.chunk_times):03d} at +{now - self._start_ts:.3f}s "
                f"| size={_payload_size(frame)} | type={type(frame).__name__}"
            )
        await self.push_frame(frame, direction)

    def bind_start(self, start_ts: float) -> None:
        self._start_ts = start_ts


def _verdict(start_ts: float, chunk_times: list[float], end_ts: float) -> str:
    if not chunk_times:
        return "buffered_or_failed (no audio chunks)"

    total = end_ts - start_ts
    first = chunk_times[0] - start_ts
    gaps = [t2 - t1 for t1, t2 in zip(chunk_times, chunk_times[1:])]
    significant_gaps = [g for g in gaps if g >= STREAM_GAP_THRESHOLD_SECS]
    spread = chunk_times[-1] - chunk_times[0]

    early = first <= max(total * 0.6, FIRST_CHUNK_EARLY_THRESHOLD_SECS)
    incremental = len(significant_gaps) >= 1 or spread >= STREAM_GAP_THRESHOLD_SECS * 3

    if early and incremental:
        return "streaming (incremental audio chunks over time)"

    return "buffered_or_burst (chunks clustered near completion)"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Deepgram TTS streaming via Pipecat.")
    parser.add_argument("--text", help="Override the default TTS text input.")
    args = parser.parse_args()

    load_dotenv()

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        print("Missing DEEPGRAM_API_KEY in environment.")
        return 1

    voice = os.getenv("DEEPGRAM_VOICE", "aura-2-sirio-es")
    text = args.text or os.getenv("TTS_TEST_TEXT", DEFAULT_TEXT)

    tts = DeepgramTTSService(
        api_key=api_key,
        settings=DeepgramTTSService.Settings(voice=voice)
    )

    audio_logger = AudioFrameLogger()
    pipeline = Pipeline([tts, audio_logger])
    task = PipelineTask(pipeline)

    print("=== Deepgram TTS Streaming Verification (Pipecat) ===")
    print(f"Voice: {voice}")
    print(f"Request start (UTC): {_utc_timestamp()}")

    start_ts = time.perf_counter()
    audio_logger.bind_start(start_ts)

    await task.queue_frame(TTSSpeakFrame(text))
    await task.queue_frame(EndFrame())

    runner = PipelineRunner()
    await runner.run(task)

    end_ts = time.perf_counter()
    print(f"Request end (UTC): {_utc_timestamp()}")
    print(f"Total completion time: {end_ts - start_ts:.3f}s")
    print(f"Total audio chunks: {len(audio_logger.chunk_times)}")

    verdict = _verdict(start_ts, audio_logger.chunk_times, end_ts)
    print(f"Verdict: {verdict}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
