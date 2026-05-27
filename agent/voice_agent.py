import agent.core.bootstrap
import asyncio
import os
import json
import sys

# Pipecat 1.2.1 imports
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_start import TranscriptionUserTurnStartStrategy
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.frames.frames import (
    TTSSpeakFrame,
    EndFrame,
)

from agent.utils.session_context import load_prior_context
from agent.core.db import redis_async_client as redis, write_turn
from agent.prompts.tutor_policy import GLOBAL_TUTOR_POLICY
from agent.processors.text_chunker import ClauseBoundaryTextChunker
from agent.processors.speech_streamer import TutorSpeechStreamer
from agent.processors.turn_buffer import UserTurnBufferProcessor
from agent.utils.latency_tracker import (
    LATENCY_LOG_PATH,
    InputLatencyProcessor,
    LatencyTracker,
    LLMFirstTokenLogger,
    LLMResponseLogger,
    STTTranscriptionLogger,
    TTSAudioLogger,
    TTSRequestLogger,
)


class TimedGroqLLMService(GroqLLMService):
    def __init__(self, *args, context=None, latency_tracker: LatencyTracker | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._context_ref = context
        self._latency = latency_tracker

    def _context_stats(self) -> dict[str, int]:
        if not self._context_ref:
            return {}
        try:
            messages = list(self._context_ref.messages)
        except Exception:
            return {}

        def _count_words(text: str) -> int:
            return len(text.split())

        total_chars = sum(len(m.get("content", "")) for m in messages)
        total_words = sum(_count_words(m.get("content", "")) for m in messages)
        system_chars = len(messages[0].get("content", "")) if messages else 0
        system_words = _count_words(messages[0].get("content", "")) if messages else 0
        return {
            "total_chars": total_chars,
            "total_words": total_words,
            "system_chars": system_chars,
            "system_words": system_words,
            "history_chars": total_chars - system_chars,
            "history_words": total_words - system_words,
        }

    def get_chat_completions(self, *args, **kwargs):
        if self._latency:
            stats = self._context_stats()
            if stats:
                self._latency.record_groq_request_start(stats)
        return super().get_chat_completions(*args, **kwargs)

def build_system_prompt(scenario_system_prompt: str) -> str:
    return f"{GLOBAL_TUTOR_POLICY.strip()}\n\nScenario instructions:\n{scenario_system_prompt.strip()}"

# ── Helper Functions ───────────────────────────────────────────────────────────

from agent.utils.text import split_tts_phrases

# ── Main Runner ───────────────────────────────────────────────────────────────
async def run_agent(session_id: str, scenario_system_prompt: str):
    system_prompt = build_system_prompt(scenario_system_prompt)
    latency_tracker = LatencyTracker(session_id)
    llm_max_tokens = int(os.environ.get("GROQ_MAX_COMPLETION_TOKENS", "120"))
    latency_tracker.register_baseline_config(
        llm_model="llama-3.3-70b-versatile",
        stt_model="nova-3-general",
        stt_language="multi",
        tts_voice="aura-2-javier-es",
        max_completion_tokens=llm_max_tokens,
        system_prompt_chars=len(system_prompt),
        system_prompt_words=len(system_prompt.split()),
        latency_log_path=LATENCY_LOG_PATH,
    )
    print(f"[Startup] Latency log: {LATENCY_LOG_PATH}")

    # Load turn history (empty list if new session, last 10 turns if resumed)
    prior_messages = load_prior_context(session_id)

    try:
        await redis.set(f"agent_pid:{session_id}", str(os.getpid()), ex=3600)
    except Exception as e:
        print(f"[Pipeline] Failed to store agent pid: {e}")

    # 1. Silero Voice Activity Detector (optimized for noise hallucination filtering)
    vad = SileroVADAnalyzer(
        params=VADParams(
            start_secs=0.35,      # Filter brief transient noises (<350ms)
            stop_secs=0.50,       # Allow natural conversational/breathing pauses
            confidence=0.80,      # Prevent low-level speech breathing triggers
            min_volume=0.6,       # Drop background ambient hums
        )
    )

    # 2. LiveKit WebRTC Transport (VAD enabled)
    transport = LiveKitTransport(
        url=os.environ["LIVEKIT_URL"],
        token=_mint_agent_token(session_id),
        room_name=session_id,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=vad,
        )
    )

    # 3. Deepgram STT Service (enables English & Spanish detection)
    stt = DeepgramSTTService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramSTTService.Settings(
            language="multi",
            model="nova-3-general",
            endpointing="400",    # Tuned from 200ms to allow thinking pauses without early cut-off
            smart_format=True,
            interim_results=True,
        )
    )

    # 4. Deepgram TTS — TOKEN mode; clause chunker upstream sends phrase-sized TextFrames
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        text_aggregation_mode=TextAggregationMode.TOKEN,
        settings=DeepgramTTSService.Settings(
            voice="aura-2-javier-es",
            extra={"speed": 1.1}
        ),
    )


    # 5. LLM Context Initialization
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(prior_messages)

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                start=[TranscriptionUserTurnStartStrategy()],
                stop=[ExternalUserTurnStopStrategy(timeout=0.05)],
            ),
        ),
    )

    # 6. Groq LLM Service (Spanish language tutor model)
    llm = TimedGroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="llama-3.3-70b-versatile",
            max_completion_tokens=llm_max_tokens,
        ),
        context=context,
        latency_tracker=latency_tracker,
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
        on_grammar_job_enqueued=enqueue_grammar_job,
        latency_tracker=latency_tracker,
    )

    # 8b. Clause chunker + tutor text streamer for UI captions
    clause_chunker = ClauseBoundaryTextChunker()
    tutor_speech_streamer = TutorSpeechStreamer(transport, session_id)
    input_latency = InputLatencyProcessor(latency_tracker)
    stt_transcription_logger = STTTranscriptionLogger(latency_tracker)
    llm_response_logger = LLMResponseLogger(latency_tracker)
    llm_token_logger = LLMFirstTokenLogger(latency_tracker)
    tts_request_logger = TTSRequestLogger(latency_tracker)
    tts_audio_logger = TTSAudioLogger(latency_tracker)

    # 9. Pipecat Pipeline Assembly
    pipeline = Pipeline([
        transport.input(),
        input_latency,
        stt,
        stt_transcription_logger,
        user_turn_processor,
        user_aggregator,
        llm,
        llm_response_logger,
        llm_token_logger,
        clause_chunker,
        tutor_speech_streamer,
        tts_request_logger,
        tts,
        tts_audio_logger,
        transport.output(),
        assistant_aggregator,
    ])

    # 10. Sequential PipelineTask Creation before attaching event listeners
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    # ── Pipeline Hooks and Event Handlers ─────────────────────────────────────
    @user_aggregator.event_handler("on_user_turn_inference_triggered")
    async def on_user_turn_inference_triggered(processor, message, *args):
        latency_tracker.record_inference_triggered()
        tts_request_logger.reset()
    
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
        
        await task.queue_frame(TTSSpeakFrame(greeting))

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
    try:
        await runner.run(task)
    finally:
        await tutor_speech_streamer.cleanup()
        latency_tracker.log_session_summary()
        latency_tracker.log_five_minute_cost_estimate()
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
        print("Usage: python3 -m agent.voice_agent <session_id>")
        sys.exit(1)
    
    session_id = sys.argv[1]
    
    system_prompt = os.environ.get("AGENT_SYSTEM_PROMPT")
    if not system_prompt:
        print("[Pipeline] Error: AGENT_SYSTEM_PROMPT environment variable is not defined.")
        sys.exit(1)
        
    asyncio.run(run_agent(session_id, system_prompt))
