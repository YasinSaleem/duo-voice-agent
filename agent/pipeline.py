import asyncio
import os
import json
import sys
import re
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
from pipecat.audio.vad.vad_analyzer import VADParams
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

GLOBAL_TUTOR_POLICY = """
You are a warm, patient, low-pressure Spanish conversation tutor for a live voice app.

Core behavior:
- Prioritize the learner's confidence, clarity, and speaking flow over perfect accuracy.
- Keep replies short and easy to process in audio: usually 1-2 short sentences, occasionally 3 if needed.
- Ask at most one question at a time.
- Stay aligned with the scenario, but adapt naturally to what the learner actually says instead of forcing the script.
- If the learner says they do not understand, seems confused, or asks for English, briefly explain the immediately previous Spanish in simple English, say one natural Spanish response the learner could use next, then gently invite a short Spanish retry.
- For English help requests, stay on topic and teach the next useful phrase for the current scenario instead of restarting the conversation.
- A strong clarification pattern is: 1. what the last Spanish line meant, 2. what the learner can say next, 3. a brief invitation to try it in Spanish.
- Do not give long vocabulary lectures unless explicitly asked.
- Do not invent phonetic spellings unless the learner explicitly asks for pronunciation help.
- If the learner says something off-topic like their name or how they feel, respond naturally and then gently guide them back to the scenario.

Correction style:
- Focus on helping spoken Spanish.
- Prefer implicit modeling over heavy correction during the live conversation.
- Give at most one small correction at a time, only when it is useful.
- Never comment on commas, punctuation, capitalization, or typing conventions in the live lesson.
- If the learner uses English to ask for help, answer that help request instead of pretending they completed the Spanish task.

Language policy:
- Default to Spanish for normal tutoring.
- Switch to brief English only for clarification, confusion, or direct English requests.
"""

def build_system_prompt(scenario_system_prompt: str) -> str:
    return f"{GLOBAL_TUTOR_POLICY.strip()}\n\nScenario instructions:\n{scenario_system_prompt.strip()}"

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

def normalize_transcript_spacing(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

def utterance_flush_delay(text: str) -> float:
    normalized = normalize_transcript_spacing(text)
    word_count = len(normalized.split())
    if normalized.endswith(("?", "!", ".")):
        return 0.30
    if word_count <= 1:
        return 0.80
    if word_count <= 3:
        return 0.65
    return 0.45

# ── Custom User Turn Buffer Processor ────────────────────────────────────────
class UserTurnBufferProcessor(FrameProcessor):
    """
    Buffers nearby final STT chunks into a single utterance before they reach
    the LLM or grammar queue. This avoids fragmentary turns like "Mi" or
    "no gusta" being treated as complete user messages.
    """
    def __init__(self, session_id: str):
        super().__init__()
        self.session_id = session_id
        self._buffered_frames: list[TranscriptionFrame] = []
        self._flush_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            await self._buffer_transcription(frame, direction)
            return

        if isinstance(frame, EndFrame):
            await self._flush_buffer(direction)

        await self.push_frame(frame, direction)

    async def _buffer_transcription(self, frame: TranscriptionFrame, direction: FrameDirection):
        text = normalize_transcript_spacing(frame.text)
        if not text:
            return

        print(f"[UserTurnBufferProcessor] Buffering transcript chunk: {text}")
        buffered_frame = TranscriptionFrame(
            text=text,
            user_id=frame.user_id,
            timestamp=frame.timestamp,
            language=frame.language,
            result=frame.result,
            finalized=frame.finalized,
        )
        self._buffered_frames.append(buffered_frame)

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()

        delay = utterance_flush_delay(" ".join(item.text for item in self._buffered_frames))
        self._flush_task = asyncio.create_task(self._flush_after_delay(delay, direction))

    async def _flush_after_delay(self, delay: float, direction: FrameDirection):
        try:
            await asyncio.sleep(delay)
            await self._flush_buffer(direction)
        except asyncio.CancelledError:
            pass

    async def _flush_buffer(self, direction: FrameDirection):
        if self._flush_task and self._flush_task.done():
            self._flush_task = None

        if not self._buffered_frames:
            return

        frames_to_flush = self._buffered_frames
        self._buffered_frames = []

        merged_text = normalize_transcript_spacing(" ".join(frame.text for frame in frames_to_flush))
        last_frame = frames_to_flush[-1]
        merged_frame = TranscriptionFrame(
            text=merged_text,
            user_id=last_frame.user_id,
            timestamp=last_frame.timestamp,
            language=last_frame.language,
            result=last_frame.result,
            finalized=True,
        )

        print(f"[UserTurnBufferProcessor] Flushing merged user turn: {merged_text}")
        turn_id = await write_turn(self.session_id, "user", merged_text)
        enqueue_grammar_job(self.session_id, turn_id)
        await self.push_frame(merged_frame, direction)

# ── Main Runner ───────────────────────────────────────────────────────────────
async def run_agent(session_id: str, scenario_system_prompt: str):
    # Load turn history (empty list if new session, last 10 turns if resumed)
    prior_messages = load_prior_context(session_id)

    # 1. Silero Voice Activity Detector
    vad = SileroVADAnalyzer(
        params=VADParams(
            start_secs=0.15,
            stop_secs=0.35,
            confidence=0.7,
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
        )
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
    messages = [{"role": "system", "content": build_system_prompt(scenario_system_prompt)}]
    messages.extend(prior_messages)

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)

    # 7. Single instantiation of aggregators to prevent event routing bugs
    user_aggregator = context_aggregator.user()
    assistant_aggregator = context_aggregator.assistant()

    # 8. Custom User Turn Interceptor
    user_turn_processor = UserTurnBufferProcessor(session_id)

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
        
        # Greet the user based on whether this is a new or resumed session
        if prior_messages:
            greeting = "¡Hola de nuevo! Continuemos con nuestra conversación. ¿En qué nos quedamos?"
        else:
            greeting = "¡Hola! Bienvenido. ¿Qué te gustaría pedir hoy?"
            
        print(f"[Pipeline] Enqueueing greeting turn: '{greeting}'")
        # Save greeting turn manually to MongoDB since it bypasses the LLM generator
        await write_turn(session_id, "agent", greeting)
        
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
