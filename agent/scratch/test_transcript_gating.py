"""Quick checks for transcript commit gating heuristics."""

import time

from transcript_gating import (
    AgentSpeechState,
    TranscriptGatingConfig,
    evaluate_transcript_commit,
    is_echo_of_tutor,
)


def test_passes_when_tutor_silent():
    state = AgentSpeechState()
    decision = evaluate_transcript_commit(
        "Bye. But he would love me.",
        speech_state=state,
        confidence=0.4,
        config=TranscriptGatingConfig(),
    )
    assert decision.accept


def test_rejects_hallucination_during_greeting_armed():
    state = AgentSpeechState()
    state.arm_tutor_output("Hi. I will teach you step by step in English.")
    decision = evaluate_transcript_commit(
        "Bye. But he would love me.",
        speech_state=state,
        confidence=0.55,
        config=TranscriptGatingConfig(),
        now=time.monotonic(),
    )
    assert not decision.accept


def test_accepts_allowlisted_barge_in():
    state = AgentSpeechState()
    state.on_bot_started_speaking()
    decision = evaluate_transcript_commit(
        "wait",
        speech_state=state,
        confidence=0.9,
        config=TranscriptGatingConfig(),
        now=time.monotonic() + 1.0,
    )
    assert decision.accept


def test_rejects_tutor_echo():
    tutor = "ready to start"
    user = "ready to start"
    assert is_echo_of_tutor(user, tutor, 0.62)


if __name__ == "__main__":
    test_passes_when_tutor_silent()
    test_rejects_hallucination_during_greeting_armed()
    test_accepts_allowlisted_barge_in()
    test_rejects_tutor_echo()
    print("transcript_gating tests passed")
