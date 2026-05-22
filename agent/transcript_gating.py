"""Heuristics for rejecting echo/hallucinated STT while the tutor is speaking."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

# Short learner interjections that should still barge-in during tutor speech.
BARGE_IN_ALLOWLIST = frozenset({
    "sí", "si", "no", "ya", "ok", "okay", "wait", "stop", "hola", "ayuda",
    "help", "repeat", "again", "espere", "perdón", "perdon", "disculpa",
    "gracias", "thanks", "thank you",
})


@dataclass(frozen=True)
class TranscriptGatingConfig:
    """Tunable thresholds (override via env vars in load_gating_config)."""

    bot_start_cooldown_secs: float = 0.45
    min_confidence_while_bot_speaking: float = 0.78
    short_utterance_min_confidence: float = 0.88
    min_words_while_bot_speaking: int = 2
    min_chars_while_bot_speaking: int = 3
    tutor_similarity_reject: float = 0.62
    require_vad_for_short: bool = True
    short_word_max: int = 2


def load_gating_config() -> TranscriptGatingConfig:
    def _float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return float(raw)

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return int(raw)

    def _bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name)
        if raw is None or raw.strip() == "":
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    return TranscriptGatingConfig(
        bot_start_cooldown_secs=_float("TRANSCRIPT_GATE_BOT_COOLDOWN_SECS", 0.45),
        min_confidence_while_bot_speaking=_float("TRANSCRIPT_GATE_MIN_CONFIDENCE", 0.78),
        short_utterance_min_confidence=_float("TRANSCRIPT_GATE_SHORT_MIN_CONFIDENCE", 0.88),
        min_words_while_bot_speaking=_int("TRANSCRIPT_GATE_MIN_WORDS", 2),
        min_chars_while_bot_speaking=_int("TRANSCRIPT_GATE_MIN_CHARS", 3),
        tutor_similarity_reject=_float("TRANSCRIPT_GATE_TUTOR_SIMILARITY", 0.62),
        require_vad_for_short=_bool("TRANSCRIPT_GATE_REQUIRE_VAD_FOR_SHORT", True),
        short_word_max=_int("TRANSCRIPT_GATE_SHORT_WORD_MAX", 2),
    )


@dataclass
class AgentSpeechState:
    """Shared tutor-speaking state for transcript commit gating."""

    bot_speaking: bool = False
    bot_started_at: float | None = None
    tutor_output_armed: bool = False
    tutor_text: str = ""
    user_vad_active: bool = False
    user_vad_started_at: float | None = None
    user_vad_seen_since_bot_start: bool = False

    def arm_tutor_output(self, text: str) -> None:
        cleaned = normalize_for_match(text)
        if cleaned:
            self.tutor_text = merge_tutor_snippets(self.tutor_text, cleaned)
        self.tutor_output_armed = True

    def note_tutor_text(self, text: str) -> None:
        cleaned = normalize_for_match(text)
        if cleaned:
            self.tutor_text = merge_tutor_snippets(self.tutor_text, cleaned)

    def on_bot_started_speaking(self) -> None:
        self.bot_speaking = True
        self.tutor_output_armed = False
        self.bot_started_at = time.monotonic()
        self.user_vad_seen_since_bot_start = False

    def on_bot_stopped_speaking(self) -> None:
        self.bot_speaking = False
        self.tutor_output_armed = False
        self.bot_started_at = None
        self.tutor_text = ""
        self.user_vad_seen_since_bot_start = False

    def on_user_vad_started(self) -> None:
        self.user_vad_active = True
        self.user_vad_started_at = time.monotonic()
        if self.is_gating_active():
            self.user_vad_seen_since_bot_start = True

    def on_user_vad_stopped(self) -> None:
        self.user_vad_active = False
        self.user_vad_started_at = None

    def is_gating_active(self) -> bool:
        return self.bot_speaking or self.tutor_output_armed


def normalize_for_match(text: str) -> str:
    lowered = text.lower().strip()
    lowered = re.sub(r"[^\w\s']", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def merge_tutor_snippets(existing: str, new: str, max_chars: int = 600) -> str:
    if not existing:
        combined = new
    elif new in existing:
        combined = existing
    else:
        combined = f"{existing} {new}".strip()
    if len(combined) > max_chars:
        return combined[-max_chars:]
    return combined


def extract_deepgram_confidence(result: Any | None) -> float | None:
    if result is None:
        return None
    try:
        channel = getattr(result, "channel", None)
        if not channel:
            return None
        alternatives = getattr(channel, "alternatives", None)
        if not alternatives:
            return None
        confidence = getattr(alternatives[0], "confidence", None)
        if confidence is None:
            return None
        return float(confidence)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def similarity_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_allowlisted_barge_in(text: str) -> bool:
    normalized = normalize_for_match(text)
    if not normalized:
        return False
    words = normalized.split()
    if len(words) == 1 and words[0] in BARGE_IN_ALLOWLIST:
        return True
    if normalized in BARGE_IN_ALLOWLIST:
        return True
    return False


def is_echo_of_tutor(transcript: str, tutor_text: str, threshold: float) -> bool:
    user = normalize_for_match(transcript)
    tutor = normalize_for_match(tutor_text)
    if not user or not tutor:
        return False
    if user in tutor or tutor in user:
        return True
    if similarity_ratio(user, tutor) >= threshold:
        return True
    user_words = set(user.split())
    tutor_words = set(tutor.split())
    if len(user_words) >= 3 and user_words.issubset(tutor_words):
        return True
    return False


@dataclass
class GatingDecision:
    accept: bool
    reason: str


def evaluate_transcript_commit(
    text: str,
    *,
    speech_state: AgentSpeechState,
    confidence: float | None,
    config: TranscriptGatingConfig,
    now: float | None = None,
) -> GatingDecision:
    normalized = normalize_for_match(text)
    if not normalized:
        return GatingDecision(False, "empty")

    if not speech_state.is_gating_active():
        return GatingDecision(True, "tutor_not_speaking")

    now = now if now is not None else time.monotonic()
    words = normalized.split()
    word_count = len(words)
    allowlisted = is_allowlisted_barge_in(normalized)

    if speech_state.bot_started_at is not None:
        elapsed = now - speech_state.bot_started_at
        if elapsed < config.bot_start_cooldown_secs and not allowlisted:
            return GatingDecision(
                False,
                f"bot_start_cooldown ({elapsed:.2f}s < {config.bot_start_cooldown_secs}s)",
            )

    if is_echo_of_tutor(normalized, speech_state.tutor_text, config.tutor_similarity_reject):
        return GatingDecision(False, "tutor_echo_similarity")

    if len(normalized) < config.min_chars_while_bot_speaking and not allowlisted:
        return GatingDecision(False, "too_short")

    if (
        config.require_vad_for_short
        and word_count <= config.short_word_max
        and not allowlisted
        and not speech_state.user_vad_seen_since_bot_start
    ):
        return GatingDecision(False, "short_without_vad")

    if confidence is not None:
        if allowlisted:
            min_conf = config.short_utterance_min_confidence
        elif word_count <= config.short_word_max:
            min_conf = config.short_utterance_min_confidence
        else:
            min_conf = config.min_confidence_while_bot_speaking

        if confidence < min_conf:
            return GatingDecision(False, f"low_confidence ({confidence:.3f} < {min_conf:.3f})")

    elif word_count <= config.short_word_max and not allowlisted:
        # No confidence from STT: be strict on very short echo-prone snippets.
        return GatingDecision(False, "short_without_confidence")

    if word_count < config.min_words_while_bot_speaking and not allowlisted:
        return GatingDecision(False, f"min_words ({word_count} < {config.min_words_while_bot_speaking})")

    return GatingDecision(True, "accepted_during_tutor_speech")
