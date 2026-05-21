import asyncio
import os
import json
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

# Secure macOS SSL certificate environment override to resolve CERTIFICATE_VERIFY_FAILED
try:
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
except ImportError:
    pass


# Pipecat 1.2.1 imports
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame, TranscriptionFrame, TTSSpeakFrame, EndFrame

from motor.motor_asyncio import AsyncIOMotorClient
from upstash_redis import Redis
from context_loader import load_prior_context

# ── Database & Redis Connections ──────────────────────────────────────────────
tls_kwargs = {}
try:
    import certifi
    tls_kwargs["tlsCAFile"] = certifi.where()
except ImportError:
    pass

mongo_client = AsyncIOMotorClient(os.environ["MONGO_URI"], **tls_kwargs)
turns_col = mongo_client["language_tutor"]["turns"]

redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"]
)

# ── Helper Functions ───────────────────────────────────────────────────────────
async def write_turn(session_id: str, role: str, transcript: str) -> str:
    """Persist conversation turn in MongoDB. Returns the inserted turn document ID."""
    doc = {
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc),
        "role": role,
        "transcript": transcript,
        "corrections": None  # Filled asynchronously by grammar_worker
    }
    result = await turns_col.insert_one(doc)
    turn_id = str(result.inserted_id)
    print(f"[Pipeline] Turn persisted to MongoDB: {role} -> ID: {turn_id}")
    return turn_id

def enqueue_grammar_job(session_id: str, turn_id: str):
    """Enqueues user turn for asynchronous grammar analysis in Redis (FIFO)."""
    payload = json.dumps({"sessionId": session_id, "turnId": turn_id})
    redis.rpush("grammar_jobs", payload)
    print(f"[Pipeline] Grammar job enqueued for turn {turn_id} (FIFO)")

# ── Custom User Turn Frame Processor ─────────────────────────────────────────
class UserTurnProcessor(FrameProcessor):
    """
    Custom FrameProcessor placed immediately downstream of Deepgram STT.
    Intercepts user TranscriptionFrames to log turns and trigger grammar evaluation.
    """
    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        # Intercept completed user speech transcription frames
        # In Pipecat 1.2.1, InterimTranscriptionFrame does not inherit from TranscriptionFrame
        # This check is guaranteed to trigger exclusively on final transcripts
        if isinstance(frame, TranscriptionFrame):
            print(f"[UserTurnProcessor] Intercepted user turn: {frame.text}")
            # Persist to MongoDB & enqueue grammar check
            turn_id = await write_turn(self.session_id, "user", frame.text)
            enqueue_grammar_job(self.session_id, turn_id)
            
        await self.push_frame(frame, direction)

# ── Main Runner ───────────────────────────────────────────────────────────────
async def run_agent(session_id: str, scenario_system_prompt: str):
    # Load turn history (empty list if new session, last 10 turns if resumed)
    prior_messages = load_prior_context(session_id)

    # 1. Silero Voice Activity Detector
    vad = SileroVADAnalyzer()

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
        language="multi",
    )

    # 4. Groq LLM Service (Spanish language tutor model)
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(model="llama-3.1-8b-instant")
    )

    # 5. Deepgram TTS Service (natural Spanish speaker voice)
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        settings=DeepgramTTSService.Settings(voice="aura-2-sirio-es")
    )


    # 6. LLM Context Initialization
    messages = [{"role": "system", "content": scenario_system_prompt}]
    messages.extend(prior_messages)

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)

    # 7. Single instantiation of aggregators to prevent event routing bugs
    user_aggregator = context_aggregator.user()
    assistant_aggregator = context_aggregator.assistant()

    # 8. Custom User Turn Interceptor
    user_turn_processor = UserTurnProcessor(session_id)

    # 9. Pipecat Pipeline Assembly
    pipeline = Pipeline([
        transport.input(),
        stt,
        user_turn_processor,         # Downstream of stt
        user_aggregator,             # Pre-instantiated user aggregator
        llm,
        tts,
        transport.output(),
        assistant_aggregator         # Pre-instantiated assistant aggregator
    ])

    # 10. Sequential PipelineTask Creation before attaching event listeners
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))

    # ── Pipeline Hooks and Event Handlers ─────────────────────────────────────
    
    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant_id):
        print(f"[Pipeline] First participant joined room. ID: {participant_id}")
        # Greet the user in Spanish (tú casual for difficulty 1 level)
        # TTSSpeakFrame is processed by TTS and intercepted by the assistant aggregator
        greeting = "¡Hola! Bienvenido. ¿Qué te gustaría pedir hoy?"
        await task.queue_frame(TTSSpeakFrame(greeting))

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport, participant_id):
        print(f"[Pipeline] User left room: {participant_id}. Ending task.")
        await task.queue_frame(EndFrame())

    # Monitor assistant aggregator for agent turns (including the greeting text)
    @assistant_aggregator.event_handler("on_assistant_turn_stopped")
    async def on_agent_turn(processor, message):
        if message.content:
            print(f"[Pipeline] Intercepted agent turn: {message.content}")
            await write_turn(session_id, "agent", message.content)

    # ── Execution ─────────────────────────────────────────────────────────────
    runner = PipelineRunner()
    
    print(f"[Pipeline] Starting voice loop for room: {session_id}")
    await runner.run(task)

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
    if len(sys.argv) < 3:
        print("Usage: python3 pipeline.py <session_id> <system_prompt>")
        sys.exit(1)
    
    session_id = sys.argv[1]
    system_prompt = sys.argv[2]
    asyncio.run(run_agent(session_id, system_prompt))
