import agent.core.bootstrap
import asyncio
import os
import json
import sys
import re
import time
from datetime import datetime, timezone


# Pipecat 1.2.1 imports
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    EndFrame,
    TextFrame,
)

from agent.utils.session_context import load_prior_context
from agent.core.db import redis_async_client as redis, write_turn
from agent.prompts.tutor_policy import GLOBAL_TUTOR_POLICY
from agent.processors.text_chunker import ClauseBoundaryTextChunker
from agent.processors.speech_streamer import TutorSpeechStreamer
from agent.processors.turn_buffer import UserTurnBufferProcessor

class TranscriptionTimingLogger(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._enabled = os.environ.get("LATENCY_TRACE", "0") == "1"
        self._first_interim_ts: float | None = None
        self._first_final_ts: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not self._enabled:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            now = time.perf_counter()
            if self._first_interim_ts is None:
                self._first_interim_ts = now
                print(f"[Latency] first_transcription_frame at +{now:.3f}s | finalized={frame.finalized}")
            if frame.finalized and self._first_final_ts is None:
                self._first_final_ts = now
                print(f"[Latency] first_final_transcript at +{now:.3f}s")
            print(f"[Latency] transcript_frame at +{now:.3f}s | finalized={frame.finalized} | text='{frame.text}'")

        await self.push_frame(frame, direction)


class LLMFirstTokenLogger(FrameProcessor):
    def __init__(self):
        super().__init__()
        self._enabled = os.environ.get("LATENCY_TRACE", "0") == "1"
        self._first_token_ts: float | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not self._enabled:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            now = time.perf_counter()
            if self._first_token_ts is None:
                self._first_token_ts = now
                print(f"[Latency] first_llm_token at +{now:.3f}s")

        await self.push_frame(frame, direction)


class TimedGroqLLMService(GroqLLMService):
    def __init__(self, *args, context=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._latency_enabled = os.environ.get("LATENCY_TRACE", "0") == "1"
        self._context_ref = context

    def _context_stats(self) -> str:
        if not self._context_ref:
            return "context_stats=unavailable"
        try:
            messages = list(self._context_ref.messages)
        except Exception:
            return "context_stats=unavailable"

        def _count_words(text: str) -> int:
            return len(text.split())

        total_chars = sum(len(m.get("content", "")) for m in messages)
        total_words = sum(_count_words(m.get("content", "")) for m in messages)
        system_chars = len(messages[0].get("content", "")) if messages else 0
        system_words = _count_words(messages[0].get("content", "")) if messages else 0
        history_chars = total_chars - system_chars
        history_words = total_words - system_words

        return (
            f"context_chars={total_chars} context_words={total_words} "
            f"system_chars={system_chars} system_words={system_words} "
            f"history_chars={history_chars} history_words={history_words}"
        )

    def get_chat_completions(self, *args, **kwargs):
        if self._latency_enabled:
            now = time.perf_counter()
            print(f"[Latency] groq_request_start at +{now:.3f}s | {self._context_stats()}")
        return super().get_chat_completions(*args, **kwargs)

def build_system_prompt(scenario_system_prompt: str) -> str:
    return f"{GLOBAL_TUTOR_POLICY.strip()}\n\nScenario instructions:\n{scenario_system_prompt.strip()}"

# ── Helper Functions ───────────────────────────────────────────────────────────

from agent.utils.text import split_tts_phrases

# ── Main Runner ───────────────────────────────────────────────────────────────
async def run_agent(session_id: str, scenario_system_prompt: str):
    print(f"[Startup] LATENCY_TRACE={os.environ.get('LATENCY_TRACE', '0')}")
    # Load turn history (empty list if new session, last 10 turns if resumed)
    prior_messages = load_prior_context(session_id)

    try:
        await redis.set(f"agent_pid:{session_id}", str(os.getpid()), ex=3600)
    except Exception as e:
        print(f"[Pipeline] Failed to store agent pid: {e}")

    # 1. Silero Voice Activity Detector
    vad = SileroVADAnalyzer(
        params=VADParams(
            start_secs=0.20,
            stop_secs=0.50,
            confidence=0.75,
            min_volume=0.6,
        )
    )

    # 2. LiveKit WebRTC Transport
    transport = LiveKitTransport(
        url=os.environ["LIVEKIT_URL"],
        token=_mint_agent_token(session_id),
        room_name=session_id,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=vad
        )
    )

    # 3. Deepgram STT Service (enables English & Spanish detection)
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramSTTService.Settings(
            language="multi",
            model="nova-3-general",
            endpointing="300",
            smart_format=True,
        )
    )

    # 4. Deepgram TTS — TOKEN mode; clause chunker upstream sends phrase-sized TextFrames
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        text_aggregation_mode=TextAggregationMode.TOKEN,
        settings=DeepgramTTSService.Settings(voice="aura-2-sirio-es"),
    )


    # 5. LLM Context Initialization
    messages = [{"role": "system", "content": build_system_prompt(scenario_system_prompt)}]
    messages.extend(prior_messages)

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)

    # 6. Groq LLM Service (Spanish language tutor model)
    llm_max_tokens = int(os.environ.get("GROQ_MAX_COMPLETION_TOKENS", "120"))
    llm = TimedGroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="llama-3.1-8b-instant",
            max_completion_tokens=llm_max_tokens,
        ),
        context=context,
    )

    # 7. Single instantiation of aggregators to prevent event routing bugs
    user_aggregator = context_aggregator.user()
    assistant_aggregator = context_aggregator.assistant()

    # 8. Custom User Turn Interceptor
    from agent.core.db import enqueue_grammar_job
    user_turn_processor = UserTurnBufferProcessor(
        session_id=session_id,
        transport=transport,
        on_turn_persisted=write_turn,
        on_grammar_job_enqueued=enqueue_grammar_job
    )
    
    # 8b. Clause chunker + tutor text streamer for UI captions
    clause_chunker = ClauseBoundaryTextChunker()
    tutor_speech_streamer = TutorSpeechStreamer(transport, session_id)
    transcription_logger = TranscriptionTimingLogger()
    llm_token_logger = LLMFirstTokenLogger()

    # 9. Pipecat Pipeline Assembly
    pipeline = Pipeline([
        transport.input(),
        stt,
        transcription_logger,
        user_turn_processor,         # Downstream of stt
        user_aggregator,             # Pre-instantiated user aggregator
        llm,
        llm_token_logger,
        clause_chunker,              # Flush TTS-sized phrases at punctuation boundaries
        tutor_speech_streamer,       # Stream text chunks in real-time
        tts,
        transport.output(),
        assistant_aggregator         # Pre-instantiated assistant aggregator
    ])

    # 10. Sequential PipelineTask Creation before attaching event listeners
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    # ── Pipeline Hooks and Event Handlers ─────────────────────────────────────
    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(processor, message, *args):
        if os.environ.get("LATENCY_TRACE", "0") == "1":
            print(f"[Latency] aggregator_user_turn_started at +{time.perf_counter():.3f}s")

    @user_aggregator.event_handler("on_user_turn_inference_triggered")
    async def on_user_turn_inference_triggered(processor, message, *args):
        if os.environ.get("LATENCY_TRACE", "0") == "1":
            print(f"[Latency] aggregator_inference_triggered at +{time.perf_counter():.3f}s")

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(processor, message, *args):
        if os.environ.get("LATENCY_TRACE", "0") == "1":
            print(f"[Latency] aggregator_user_turn_stopped at +{time.perf_counter():.3f}s")
    
    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant_id):
        print(f"[Pipeline] First participant joined room. ID: {participant_id}")
        
        # Greet the user based on whether this is a new or resumed session
        if prior_messages:
            greeting = "Welcome back! Ready to continue our Spanish lesson?"
        else:
            greeting = "Hi there! I'm your Spanish tutor. We'll learn Spanish step by step, and have lots of fun along the way. Ready to begin?"
            
        print(f"[Pipeline] Enqueueing greeting turn: '{greeting}'")
        # Save greeting turn manually to MongoDB since it bypasses the LLM generator
        await write_turn(session_id, "agent", greeting)
        
        # Send greeting text chunk to client so it streams/shows immediately
        try:
            payload_chunk = json.dumps({"type": "tutor_text_chunk", "text": greeting})
            await transport.send_message(payload_chunk)
            payload_end = json.dumps({"type": "tutor_text_end"})
            await transport.send_message(payload_end)
        except Exception as e:
            print(f"[Pipeline] Error sending greeting text: {e}")
        
        for phrase in split_tts_phrases(greeting):
            await task.queue_frame(TTSSpeakFrame(phrase))

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport, participant_id):
        print(f"[Pipeline] User left room: {participant_id}. Ending task.")
        await task.queue_frame(EndFrame())

    # Monitor assistant aggregator for agent turns (including the greeting text)
    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_agent_turn(processor, message):
        if message.content:
            interrupted = getattr(message, "interrupted", False)
            if interrupted:
                print(f"[Pipeline] Tutor was interrupted. Skipping database log writing for: {message.content}")
            else:
                clean_content = message.content.strip()
                print(f"[Pipeline] Intercepted agent turn: {clean_content}")
                await write_turn(session_id, "agent", clean_content)
            try:
                payload = json.dumps({"type": "tutor_text_end"})
                await transport.send_message(payload)
                await redis.delete(f"agent_speaking:{session_id}")
                await redis.delete(f"agent_speaking_text:{session_id}")
            except Exception as e:
                print(f"[Pipeline] Error sending tutor_text_end: {e}")

    # ── Execution ─────────────────────────────────────────────────────────────
    runner = PipelineRunner()
    
    print(f"[Pipeline] Starting voice loop for room: {session_id}")
    await runner.run(task)
    try:
        await redis.delete(f"agent_pid:{session_id}")
        await redis.delete(f"agent_speaking:{session_id}")
        await redis.delete(f"agent_speaking_text:{session_id}")
    except Exception as e:
        print(f"[Pipeline] Failed to clear agent redis keys: {e}")

def _mint_agent_token(session_id: str) -> str:
    """Mint a LiveKit JWT token for the agent participant."""
    from livekit.api import AccessToken, VideoGrants
    token = AccessToken(
        os.environ["LIVEKIT_API_KEY"],
        os.environ["LIVEKIT_API_SECRET"]
    ).with_identity("agent") \
     .with_grants(VideoGrants(room_join=True, room=session_id)) \
     .to_jwt()
    return token

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 pipeline.py <session_id>")
        sys.exit(1)
    
    session_id = sys.argv[1]
    
    system_prompt = os.environ.get("AGENT_SYSTEM_PROMPT")
    if not system_prompt:
        print("[Pipeline] Error: AGENT_SYSTEM_PROMPT environment variable is not defined.")
        sys.exit(1)
        
    asyncio.run(run_agent(session_id, system_prompt))
