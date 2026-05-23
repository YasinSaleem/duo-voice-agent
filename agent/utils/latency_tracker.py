"""
Hardcoded latency + cost instrumentation for the voice tutor pipeline.

All events are appended as JSON lines to agent/latency_runtime.log (always on).
Does not gate on LATENCY_TRACE or external metrics export.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pipecat.frames.frames import (
    BotStoppedSpeakingFrame,
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TextFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LATENCY_LOG_PATH = os.path.join(_AGENT_DIR, "latency_runtime.log")

# Provider list prices used for session extrapolation (pay-as-you-go, May 2026).
# Groq: https://groq.com/pricing — llama-3.1-8b-instant
GROQ_INPUT_USD_PER_MILLION_TOKENS = 0.05
GROQ_OUTPUT_USD_PER_MILLION_TOKENS = 0.08
# Deepgram: https://deepgram.com/pricing — Nova-3 Multilingual streaming
DEEPGRAM_NOVA3_MULTILINGUAL_USD_PER_MINUTE = 0.0058
# Deepgram Aura-2 TTS (character billing)
DEEPGRAM_AURA2_USD_PER_1K_CHARACTERS = 0.030

FIVE_MINUTE_SESSION_SECONDS = 300.0
DEFAULT_USER_SPEECH_FRACTION = 0.5
CHARS_PER_TOKEN_ESTIMATE = 4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * percentile
    lower = int(k)
    upper = min(lower + 1, len(sorted_vals) - 1)
    if lower == upper:
        return sorted_vals[lower]
    weight = k - lower
    return sorted_vals[lower] * (1 - weight) + sorted_vals[upper] * weight


def _estimate_tokens(char_count: int) -> int:
    return max(1, char_count // CHARS_PER_TOKEN_ESTIMATE)


def _audio_frame_duration_seconds(frame: Frame) -> float | None:
    sample_rate = getattr(frame, "sample_rate", None)
    samples = getattr(frame, "samples", None)
    if sample_rate and samples is not None:
        try:
            return len(samples) / float(sample_rate)
        except Exception:
            return None
    audio = getattr(frame, "audio", None)
    if sample_rate and audio is not None:
        try:
            return len(audio) / float(sample_rate)
        except Exception:
            return None
    return None


def _is_input_audio_frame(frame: Frame) -> bool:
    name = type(frame).__name__
    return name in ("InputAudioRawFrame", "UserAudioRawFrame")


def _is_tts_audio_frame(frame: Frame) -> bool:
    return type(frame).__name__ == "TTSAudioRawFrame"


def _is_tts_stopped_frame(frame: Frame) -> bool:
    return "ttsstopped" in type(frame).__name__.lower()


class LatencyTracker:
    def __init__(self, session_id: str, log_path: str = LATENCY_LOG_PATH) -> None:
        self.session_id = session_id
        self.log_path = log_path
        self._turn_counter = 0
        self._active_turn_id: int | None = None
        self._turn_data: dict[int, dict[str, Any]] = {}
        self._metric_samples: dict[str, list[float]] = defaultdict(list)
        self._barge_in_start_ts: float | None = None
        self._tutor_speaking = False
        self._last_user_stop_ts: float | None = None
        self._current_mic_audio_seconds = 0.0
        self._current_tts_audio_seconds = 0.0
        self._session_mic_audio_seconds = 0.0
        self._session_tts_audio_seconds = 0.0
        self._prompt_stats: list[dict[str, int]] = []
        self._completion_stats: list[dict[str, int]] = []
        self._baseline_config: dict[str, Any] = {}
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._log_event("latency_logger_started", log_path=self.log_path)

    def register_baseline_config(self, **config: Any) -> None:
        self._baseline_config = config
        self._log_event("baseline_config", **config)

    def _log_event(self, event: str, turn_id: int | None = None, **fields: Any) -> None:
        record = {
            "ts_utc": _utc_now(),
            "ts_perf": time.perf_counter(),
            "event": event,
            "session_id": self.session_id,
        }
        if turn_id is not None:
            record["turn_id"] = turn_id
        record.update(fields)
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _ensure_turn(self, reason: str) -> int:
        if self._active_turn_id is None:
            self.start_turn(reason)
        return self._active_turn_id  # type: ignore[return-value]

    def start_turn(self, reason: str) -> int:
        self._turn_counter += 1
        self._active_turn_id = self._turn_counter
        self._turn_data[self._turn_counter] = {"start_reason": reason}
        self._current_mic_audio_seconds = 0.0
        self._current_tts_audio_seconds = 0.0
        self._log_event("turn_started", turn_id=self._turn_counter, reason=reason)
        return self._turn_counter

    def finish_turn(self) -> None:
        self._active_turn_id = None
        self._current_mic_audio_seconds = 0.0
        self._current_tts_audio_seconds = 0.0

    def mark_event(self, event: str, ts: float | None = None, **fields: Any) -> None:
        turn_id = self._ensure_turn("implicit")
        timestamp = ts if ts is not None else time.perf_counter()
        turn = self._turn_data.setdefault(turn_id, {})
        if event not in turn:
            turn[event] = timestamp
            self._log_event(event, turn_id=turn_id, **fields)

        if event == "user_stop_speaking":
            self._last_user_stop_ts = timestamp
        if event == "first_tts_audio":
            self._tutor_speaking = True
            self._maybe_log_breakdown(turn_id)
        if event in ("tts_stopped", "bot_stopped_speaking", "interruption"):
            self._tutor_speaking = False
            if self._barge_in_start_ts is not None:
                delta = timestamp - self._barge_in_start_ts
                self._metric_samples["barge_in_latency"].append(delta)
                self._log_event(
                    "barge_in_resolved",
                    turn_id=turn_id,
                    barge_in_latency=delta,
                    resolved_by=event,
                )
                self._maybe_log_percentiles("barge_in_latency")
                self._barge_in_start_ts = None

    def on_vad_user_started(self) -> None:
        if self._active_turn_id is None:
            self.start_turn("vad_user_started")
        self.mark_event("vad_user_started")

    def on_vad_user_stopped(self, stop_secs: float | None = None, source: str = "vad") -> None:
        self._ensure_turn("vad_user_stopped")
        self.mark_event("user_stop_speaking", vad_stop_secs=stop_secs, stop_source=source)

    def on_user_stop_proxy(self, ts: float, source: str) -> None:
        """Fallback when VAD frames are unavailable (e.g. transport ignores vad_* params)."""
        self._ensure_turn("user_stop_proxy")
        turn = self._turn_data.setdefault(self._active_turn_id, {})  # type: ignore[arg-type]
        if "user_stop_speaking" not in turn:
            self.mark_event("user_stop_speaking", ts=ts, stop_source=source)

    def record_prompt_stats(self, stats: dict[str, int]) -> None:
        self._prompt_stats.append(stats)
        self._log_event("llm_prompt_stats", **stats)

    def record_completion_stats(self, stats: dict[str, int]) -> None:
        self._completion_stats.append(stats)
        self._log_event("llm_completion_stats", **stats)

    def record_mic_audio_frame(self, frame: Frame) -> None:
        now = time.perf_counter()
        turn_id = self._ensure_turn("mic_audio")
        duration = _audio_frame_duration_seconds(frame)
        if duration:
            self._current_mic_audio_seconds += duration
            self._session_mic_audio_seconds += duration
        if "first_mic_audio" not in self._turn_data.get(turn_id, {}):
            self.mark_event("first_mic_audio", ts=now)
        if self._tutor_speaking and self._barge_in_start_ts is None:
            self._barge_in_start_ts = now
            self._log_event("barge_in_start", turn_id=turn_id)

    def record_tts_audio_frame(self, frame: Frame) -> None:
        now = time.perf_counter()
        turn_id = self._ensure_turn("tts_audio")
        duration = _audio_frame_duration_seconds(frame)
        if duration:
            self._current_tts_audio_seconds += duration
            self._session_tts_audio_seconds += duration
        if "first_tts_audio" not in self._turn_data.get(turn_id, {}):
            self.mark_event("first_tts_audio", ts=now)

    def record_transcription_frame(self, frame: Frame) -> None:
        now = time.perf_counter()
        self._ensure_turn("stt")
        finalized = getattr(frame, "finalized", False)
        if not finalized:
            self.mark_event("first_stt_interim", ts=now)
            if self._last_user_stop_ts is not None:
                self.mark_event("first_stt_interim_after_stop", ts=now)
        if finalized:
            self.mark_event("first_stt_final", ts=now)

    def record_merged_user_frame(self) -> None:
        self.mark_event("merged_user_frame")

    def record_inference_triggered(self) -> None:
        self.mark_event("aggregator_inference_triggered")

    def record_groq_request_start(self, context_stats: dict[str, int]) -> None:
        self.mark_event("groq_request_start", **context_stats)
        self.record_prompt_stats(context_stats)

    def record_first_llm_token(self) -> None:
        self.mark_event("first_llm_token")

    def record_first_tts_request(self) -> None:
        self.mark_event("first_tts_request")

    def record_tts_stopped(self) -> None:
        self.mark_event("tts_stopped")

    def record_bot_stopped_speaking(self) -> None:
        self.mark_event("bot_stopped_speaking")

    def record_interruption(self) -> None:
        self.mark_event("interruption")

    def _maybe_log_percentiles(self, metric_key: str) -> None:
        samples = self._metric_samples.get(metric_key, [])
        if len(samples) < 2:
            return
        p50 = _percentile(samples, 0.5)
        p95 = _percentile(samples, 0.95)
        if p50 is not None and p95 is not None:
            self._log_event(
                "latency_percentiles",
                metric=metric_key,
                p50=p50,
                p95=p95,
                samples=len(samples),
            )

    def _maybe_log_breakdown(self, turn_id: int) -> None:
        turn = self._turn_data.get(turn_id, {})
        if turn.get("breakdown_logged"):
            return

        durations: dict[str, float] = {}

        def add_delta(label: str, start_key: str, end_key: str) -> None:
            if start_key in turn and end_key in turn:
                durations[label] = turn[end_key] - turn[start_key]

        add_delta("mic_to_stt_interim", "first_mic_audio", "first_stt_interim")
        add_delta("mic_to_stt_final", "first_mic_audio", "first_stt_final")
        add_delta("stop_to_stt_interim", "user_stop_speaking", "first_stt_interim_after_stop")
        add_delta("stop_to_merged_user", "user_stop_speaking", "merged_user_frame")
        add_delta("merged_to_inference", "merged_user_frame", "aggregator_inference_triggered")
        add_delta("inference_to_groq", "aggregator_inference_triggered", "groq_request_start")
        add_delta("groq_to_first_token", "groq_request_start", "first_llm_token")
        add_delta("first_token_to_tts_request", "first_llm_token", "first_tts_request")
        add_delta("tts_request_to_audio", "first_tts_request", "first_tts_audio")

        if "user_stop_speaking" in turn and "first_tts_audio" in turn:
            end_to_end = turn["first_tts_audio"] - turn["user_stop_speaking"]
            durations["end_to_end_voice_latency"] = end_to_end
            durations["response_latency"] = end_to_end
            self._metric_samples["end_to_end_voice_latency"].append(end_to_end)
            self._metric_samples["response_latency"].append(end_to_end)

        self._log_event(
            "turn_latency_breakdown",
            turn_id=turn_id,
            mic_audio_seconds=self._current_mic_audio_seconds,
            tts_audio_seconds=self._current_tts_audio_seconds,
            **durations,
        )

        turn["breakdown_logged"] = True
        self._maybe_log_percentiles("end_to_end_voice_latency")
        self._maybe_log_percentiles("response_latency")
        self.finish_turn()

    def _avg_prompt_chars(self) -> int:
        if self._prompt_stats:
            return int(sum(item["total_chars"] for item in self._prompt_stats) / len(self._prompt_stats))
        return int(self._baseline_config.get("system_prompt_chars", 0))

    def _avg_completion_chars(self) -> int:
        if self._completion_stats:
            return int(
                sum(item["completion_chars"] for item in self._completion_stats) / len(self._completion_stats)
            )
        max_tokens = int(self._baseline_config.get("max_completion_tokens", 120))
        return max_tokens * CHARS_PER_TOKEN_ESTIMATE

    def log_session_summary(self) -> None:
        self._log_event(
            "session_summary",
            turns_completed=self._turn_counter,
            avg_prompt_chars=self._avg_prompt_chars(),
            avg_prompt_words=int(
                sum(item.get("total_words", 0) for item in self._prompt_stats)
                / max(len(self._prompt_stats), 1)
            ),
            avg_completion_chars=self._avg_completion_chars(),
            avg_completion_words=int(
                sum(item.get("completion_words", 0) for item in self._completion_stats)
                / max(len(self._completion_stats), 1)
            ),
            stt_audio_seconds=self._session_mic_audio_seconds,
            tts_audio_seconds=self._session_tts_audio_seconds,
        )

    def log_five_minute_cost_estimate(self) -> None:
        user_fraction = DEFAULT_USER_SPEECH_FRACTION
        measured_total = self._session_mic_audio_seconds + self._session_tts_audio_seconds
        if measured_total > 0:
            user_fraction = self._session_mic_audio_seconds / measured_total

        user_audio_seconds = FIVE_MINUTE_SESSION_SECONDS * user_fraction
        tutor_audio_seconds = FIVE_MINUTE_SESSION_SECONDS * (1.0 - user_fraction)

        if self._turn_counter > 0 and self._session_mic_audio_seconds > 0:
            avg_user_sec_per_turn = self._session_mic_audio_seconds / self._turn_counter
            estimated_turns = max(1.0, user_audio_seconds / avg_user_sec_per_turn)
        else:
            avg_user_sec_per_turn = 4.0
            estimated_turns = user_audio_seconds / avg_user_sec_per_turn

        avg_prompt_chars = self._avg_prompt_chars()
        avg_completion_chars = self._avg_completion_chars()
        prompt_tokens = _estimate_tokens(avg_prompt_chars)
        completion_tokens = _estimate_tokens(avg_completion_chars)

        llm_input_cost = (
            estimated_turns * prompt_tokens / 1_000_000 * GROQ_INPUT_USD_PER_MILLION_TOKENS
        )
        llm_output_cost = (
            estimated_turns * completion_tokens / 1_000_000 * GROQ_OUTPUT_USD_PER_MILLION_TOKENS
        )
        stt_cost = user_audio_seconds / 60.0 * DEEPGRAM_NOVA3_MULTILINGUAL_USD_PER_MINUTE
        tts_chars = estimated_turns * avg_completion_chars
        tts_cost = tts_chars / 1000.0 * DEEPGRAM_AURA2_USD_PER_1K_CHARACTERS
        total_usd = llm_input_cost + llm_output_cost + stt_cost + tts_cost

        self._log_event(
            "five_minute_cost_estimate",
            session_seconds=FIVE_MINUTE_SESSION_SECONDS,
            user_speech_fraction=user_fraction,
            user_audio_seconds=user_audio_seconds,
            tutor_audio_seconds=tutor_audio_seconds,
            estimated_turns=round(estimated_turns, 2),
            avg_user_sec_per_turn=round(avg_user_sec_per_turn, 3),
            avg_prompt_chars=avg_prompt_chars,
            avg_completion_chars=avg_completion_chars,
            prompt_tokens_per_turn=prompt_tokens,
            completion_tokens_per_turn=completion_tokens,
            llm_model=self._baseline_config.get("llm_model"),
            stt_model=self._baseline_config.get("stt_model"),
            tts_voice=self._baseline_config.get("tts_voice"),
            groq_input_usd=round(llm_input_cost, 6),
            groq_output_usd=round(llm_output_cost, 6),
            groq_total_usd=round(llm_input_cost + llm_output_cost, 6),
            deepgram_stt_usd=round(stt_cost, 6),
            deepgram_tts_usd=round(tts_cost, 6),
            total_usd=round(total_usd, 6),
            pricing_notes={
                "groq_source": "https://groq.com/pricing",
                "deepgram_source": "https://deepgram.com/pricing",
                "token_estimate": f"{CHARS_PER_TOKEN_ESTIMATE} chars per token",
                "speech_split": "50/50 default unless session mic/tts ratio measured",
            },
        )


class InputLatencyProcessor(FrameProcessor):
    """Mic input audio + VAD start/stop (primary KPI anchors)."""

    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._tracker.on_vad_user_started()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            stop_secs = getattr(frame, "stop_secs", None)
            self._tracker.on_vad_user_stopped(stop_secs=stop_secs, source="vad")
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._tracker.on_vad_user_stopped(source="user_stopped_speaking_frame")
        elif _is_input_audio_frame(frame):
            self._tracker.record_mic_audio_frame(frame)
        await self.push_frame(frame, direction)


class STTTranscriptionLogger(FrameProcessor):
    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self._tracker.record_transcription_frame(frame)
        await self.push_frame(frame, direction)


class LLMFirstTokenLogger(FrameProcessor):
    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            self._tracker.record_first_llm_token()
        await self.push_frame(frame, direction)


class LLMResponseLogger(FrameProcessor):
    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker
        self._buffer: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = []
        elif isinstance(frame, TextFrame):
            self._buffer.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame):
            content = "".join(self._buffer)
            stats = {
                "completion_chars": len(content),
                "completion_words": len(content.split()),
            }
            self._tracker.record_completion_stats(stats)
            self._buffer = []
        await self.push_frame(frame, direction)


class TTSRequestLogger(FrameProcessor):
    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker
        self._seen = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and not self._seen:
            self._tracker.record_first_tts_request()
            self._seen = True
        await self.push_frame(frame, direction)

    def reset(self) -> None:
        self._seen = False


class TTSAudioLogger(FrameProcessor):
    def __init__(self, tracker: LatencyTracker) -> None:
        super().__init__()
        self._tracker = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if _is_tts_audio_frame(frame):
            self._tracker.record_tts_audio_frame(frame)
        elif _is_tts_stopped_frame(frame):
            self._tracker.record_tts_stopped()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._tracker.record_bot_stopped_speaking()
        elif isinstance(frame, InterruptionFrame):
            self._tracker.record_interruption()
        await self.push_frame(frame, direction)
