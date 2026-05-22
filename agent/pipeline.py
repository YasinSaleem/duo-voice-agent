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

from motor.motor_asyncio import AsyncIOMotorClient
from upstash_redis.asyncio import Redis
from context_loader import load_prior_context
from supabase import create_client, Client

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

supabase_url = os.environ.get("SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client | None = None
if supabase_url and supabase_key:
    supabase = create_client(supabase_url, supabase_key)

LESSON_TAG_START = "<lesson_item>"
LESSON_TAG_END = "</lesson_item>"

GLOBAL_TUTOR_POLICY = """
You are a warm, patient, beginner-first Spanish tutor for a live voice lesson.
This global policy is higher priority than scenario instructions. Scenario prompts only supply lesson content.

Core behavior:
- Teach like a structured language teacher, not a roleplay character.
- Use English as the primary instructional language at the start and add Spanish gradually after mastery.
- Voice-first brevity is mandatory: default to ONE short sentence (under ~20 words). Use TWO short sentences only when necessary. Never use three or more sentences in one turn.
- Do not preamble, recap at length, or stack multiple teaching points in a single reply.
- Ask at most one question at a time.
- Follow the scenario lesson plan exactly: teach vocab and phrases in the given order and do not add new targets.
- For each target: explain in English, give the Spanish, then ask the learner to repeat or use it.
- Mastery rule: 2 correct repetitions OR 1 correct contextual use.
- If the learner struggles for about 3 failed attempts, shift back to more English guidance and simplify.
- After all targets are mastered, mark the lesson complete and offer: continue practicing or end the lesson/call.
- If the learner asks for English or seems confused, respond briefly in English and guide the next step.
- If the learner goes off-topic, respond briefly in English and return to the current step.
- Do not give long vocabulary lectures unless explicitly asked.
- Do not invent phonetic spellings unless the learner explicitly asks for pronunciation help.

Lesson checkpointing (structured, no guessing):
- When you introduce a NEW lesson item (vocabulary or phrase) for the first time, append a metadata tag on a new line:
    <lesson_item>{"type":"vocab"|"phrase","spanish":"...","english":"..."}</lesson_item>
- Only emit this tag for the first introduction of a lesson item.
- Never speak or explain the tag; it will be removed before audio output.

Resume recap behavior:
- If a lesson checkpoint exists (introduced_items/last_item), use ONE short sentence to recall last_item, then ask the learner to repeat it. Do not add extra explanation in that same turn.
- Then continue with the next lesson item.

Correction style:
- Focus on helping spoken Spanish.
- Prefer implicit modeling over heavy correction during the live conversation.
- Give at most one small correction at a time, only when it is useful.
- Never comment on commas, punctuation, capitalization, accent marks (tildes), or typing conventions in the live lesson. These are speech-to-text transcription artifacts out of the user's hands.
- If the learner uses English to ask for help, answer that help request instead of pretending they completed the Spanish task.

Language policy:
- Start in English for instruction and structure.
- Use Spanish for modeled phrases and practice prompts.
- Increase Spanish only after mastery of the current targets.
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

async def enqueue_grammar_job(session_id: str, turn_id: str):
    """Enqueues user turn for asynchronous grammar analysis in Redis (FIFO)."""
    payload = json.dumps({"sessionId": session_id, "turnId": turn_id})
    await redis.rpush("grammar_jobs", payload)
    print(f"[Pipeline] Grammar job enqueued for turn {turn_id} (FIFO)")

def normalize_transcript_spacing(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()

CLAUSE_BOUNDARY_CHARS = frozenset(",.!?;:")


def drain_clause_boundary_phrases(buffer: str) -> tuple[list[str], str]:
    """Extract speakable phrases ending at comma, period, etc. Returns (phrases, remainder)."""
    phrases: list[str] = []
    i = 0
    n = len(buffer)

    while i < n:
        cut = None
        for j in range(i, n):
            ch = buffer[j]
            if ch not in CLAUSE_BOUNDARY_CHARS:
                continue

            if j == n - 1:
                if ch in ".!?":
                    cut = j + 1
                break

            nxt = buffer[j + 1]
            if ch == ",":
                if nxt.isdigit():
                    continue
                if nxt.isspace() or nxt in "\"')]}":
                    cut = j + 1
                    break
            elif nxt.isspace() or nxt in "\"')]}":
                cut = j + 1
                break

        if cut is None:
            break

        phrase = buffer[i:cut]
        if phrase.strip():
            phrases.append(phrase)
        i = cut
        while i < n and buffer[i].isspace():
            i += 1

    return phrases, buffer[i:]


def split_tts_phrases(text: str) -> list[str]:
    """Split full lines into clause-sized TTS phrases (commas, stops, etc.)."""
    normalized = normalize_transcript_spacing(text)
    if not normalized:
        return []
    phrases, remainder = drain_clause_boundary_phrases(normalized)
    if remainder.strip():
        phrases.append(remainder.strip())
    return phrases


def utterance_flush_delay(text: str) -> float:
    normalized = normalize_transcript_spacing(text)
    words = normalized.split()
    word_count = len(words)
    
    ends_with_terminal = normalized.endswith(("?", "!"))
    ends_with_period = normalized.endswith(".")
    
    # Only treat period as a terminal pause if we have more than 2 words (e.g. avoid splitting "Blanco.")
    if ends_with_terminal or (ends_with_period and word_count > 2):
        return 0.60
    return 2.00

def strip_lesson_item_tags(text: str) -> str:
    if LESSON_TAG_START not in text:
        return text
    output = []
    remaining = text
    while True:
        start_idx = remaining.find(LESSON_TAG_START)
        if start_idx == -1:
            output.append(remaining)
            break
        output.append(remaining[:start_idx])
        end_idx = remaining.find(LESSON_TAG_END, start_idx + len(LESSON_TAG_START))
        if end_idx == -1:
            break
        remaining = remaining[end_idx + len(LESSON_TAG_END):]
    return "".join(output).strip()

def parse_lesson_item_payload(payload: str) -> dict | None:
    try:
        item = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict):
        return None
    if item.get("type") not in {"vocab", "phrase"}:
        return None
    spanish = item.get("spanish")
    english = item.get("english")
    if not spanish or not english:
        return None
    return {
        "type": item["type"],
        "spanish": str(spanish).strip(),
        "english": str(english).strip()
    }

class LessonCheckpointStore:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.user_id: str | None = None
        self.introduced_items: list[dict] = []
        self.introduced_keys: set[str] = set()
        self.last_item: dict | None = None

    def _item_key(self, item: dict) -> str:
        return f"{item.get('type')}|{item.get('spanish', '').lower().strip()}"

    async def load(self):
        if not supabase:
            return
        try:
            res = supabase.table("lesson_states").select("introduced_items, last_item, user_id").eq("session_id", self.session_id).execute()
            if res.data and len(res.data) > 0:
                row = res.data[0]
                self.user_id = row.get("user_id")
                self.introduced_items = row.get("introduced_items") or []
                self.last_item = row.get("last_item")
                for item in self.introduced_items:
                    if isinstance(item, dict):
                        self.introduced_keys.add(self._item_key(item))
                return
        except Exception as e:
            print(f"[LessonCheckpoint] Failed to load lesson state: {e}")

        try:
            sess_res = supabase.table("sessions").select("user_id").eq("id", self.session_id).execute()
            if sess_res.data and len(sess_res.data) > 0:
                self.user_id = sess_res.data[0].get("user_id")
        except Exception as e:
            print(f"[LessonCheckpoint] Failed to fetch user id: {e}")

        if self.user_id:
            try:
                supabase.table("lesson_states").insert({
                    "session_id": self.session_id,
                    "user_id": self.user_id
                }).execute()
            except Exception as e:
                print(f"[LessonCheckpoint] Failed to create lesson state row: {e}")

    async def record_item(self, item: dict):
        if not supabase:
            return
        key = self._item_key(item)
        if key not in self.introduced_keys:
            self.introduced_keys.add(key)
            self.introduced_items.append(item)
        self.last_item = item
        try:
            supabase.table("lesson_states").update({
                "introduced_items": self.introduced_items,
                "last_item": self.last_item
            }).eq("session_id", self.session_id).execute()
        except Exception as e:
            print(f"[LessonCheckpoint] Failed to update lesson state: {e}")

class LessonItemProcessor(FrameProcessor):
    def __init__(self, session_id: str, checkpoint_store: LessonCheckpointStore):
        super().__init__()
        self.session_id = session_id
        self.checkpoint_store = checkpoint_store
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame):
            cleaned, items, buffer = self._extract_items(self._buffer + frame.text)
            self._buffer = buffer
            for item in items:
                await self.checkpoint_store.record_item(item)
            if cleaned:
                frame.text = cleaned
                await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    def _extract_items(self, text: str) -> tuple[str, list[dict], str]:
        items: list[dict] = []
        output: list[str] = []
        remaining = text

        while True:
            start_idx = remaining.find(LESSON_TAG_START)
            if start_idx == -1:
                suffix = self._tag_prefix_suffix(remaining)
                if suffix:
                    output.append(remaining[:-len(suffix)])
                    buffer = suffix
                else:
                    output.append(remaining)
                    buffer = ""
                return "".join(output), items, buffer

            output.append(remaining[:start_idx])
            end_idx = remaining.find(LESSON_TAG_END, start_idx + len(LESSON_TAG_START))
            if end_idx == -1:
                buffer = remaining[start_idx:]
                return "".join(output), items, buffer

            payload = remaining[start_idx + len(LESSON_TAG_START):end_idx].strip()
            item = parse_lesson_item_payload(payload)
            if item:
                items.append(item)
            remaining = remaining[end_idx + len(LESSON_TAG_END):]

    def _tag_prefix_suffix(self, text: str) -> str:
        max_len = min(len(text), len(LESSON_TAG_START) - 1)
        for length in range(max_len, 0, -1):
            if LESSON_TAG_START.startswith(text[-length:]):
                return text[-length:]
        return ""

class ClauseBoundaryTextChunker(FrameProcessor):
    """
    Buffer streaming LLM text and forward phrase-sized TextFrames at punctuation
    boundaries (comma, period, question mark, etc.) so TTS/audio can start early
    without synthesizing every token separately.
    """

    def __init__(self):
        super().__init__()
        self._buffer = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMFullResponseStartFrame):
            self._buffer = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TextFrame):
            self._buffer += frame.text
            phrases, self._buffer = drain_clause_boundary_phrases(self._buffer)
            for phrase in phrases:
                await self.push_frame(TextFrame(phrase), direction)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._buffer.strip():
                await self.push_frame(TextFrame(self._buffer), direction)
            self._buffer = ""
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)


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

# ── Custom User Turn Buffer Processor ────────────────────────────────────────
class UserTurnBufferProcessor(FrameProcessor):
    """
    Buffers nearby final STT chunks into a single utterance before they reach
    the LLM or grammar queue. This avoids fragmentary turns like "Mi" or
    "no gusta" being treated as complete user messages.
    """
    def __init__(self, session_id: str, transport):
        super().__init__()
        self.session_id = session_id
        self.transport = transport
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
        await enqueue_grammar_job(self.session_id, turn_id)
        try:
            payload = json.dumps({
                "type": "user_turn_final",
                "turnId": turn_id,
                "text": merged_text,
            })
            await self.transport.send_message(payload)
        except Exception as e:
            print(f"[UserTurnBufferProcessor] Error sending user turn event: {e}")
        await self.push_frame(merged_frame, direction)

# ── Main Runner ───────────────────────────────────────────────────────────────
async def run_agent(session_id: str, scenario_system_prompt: str):
    # Load turn history (empty list if new session, last 10 turns if resumed)
    prior_messages = load_prior_context(session_id)
    checkpoint_store = LessonCheckpointStore(session_id)
    await checkpoint_store.load()

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

    # 4. Groq LLM Service (Spanish language tutor model)
    llm_max_tokens = int(os.environ.get("GROQ_MAX_COMPLETION_TOKENS", "120"))
    llm = GroqLLMService(
        api_key=os.environ["GROQ_API_KEY"],
        settings=GroqLLMService.Settings(
            model="llama-3.1-8b-instant",
            max_completion_tokens=llm_max_tokens,
        ),
    )

    # 5. Deepgram TTS — TOKEN mode; clause chunker upstream sends phrase-sized TextFrames
    tts = DeepgramTTSService(
        api_key=os.environ["DEEPGRAM_API_KEY"],
        text_aggregation_mode=TextAggregationMode.TOKEN,
        settings=DeepgramTTSService.Settings(voice="aura-2-sirio-es"),
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
    user_turn_processor = UserTurnBufferProcessor(session_id, transport)
    
    # 8b. Clause chunker + tutor text streamer for UI captions
    clause_chunker = ClauseBoundaryTextChunker()
    tutor_speech_streamer = TutorSpeechStreamer(transport, session_id)
    lesson_item_processor = LessonItemProcessor(session_id, checkpoint_store)

    # 9. Pipecat Pipeline Assembly
    pipeline = Pipeline([
        transport.input(),
        stt,
        user_turn_processor,         # Downstream of stt
        user_aggregator,             # Pre-instantiated user aggregator
        llm,
        lesson_item_processor,       # Strip metadata + update checkpoint store
        clause_chunker,              # Flush TTS-sized phrases at punctuation boundaries
        tutor_speech_streamer,       # Stream text chunks in real-time
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
        if prior_messages and checkpoint_store.last_item:
            last_item = checkpoint_store.last_item
            spanish = last_item.get("spanish") if isinstance(last_item, dict) else None
            english = last_item.get("english") if isinstance(last_item, dict) else None
            if spanish and english:
                greeting = f"Welcome back. Repeat {spanish} — that means {english}."
            else:
                greeting = "Welcome back. Ready to continue?"
        elif prior_messages:
            greeting = "Welcome back. Ready to continue?"
        else:
            greeting = "Hi. We'll learn Spanish step by step. Ready?"
            
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
                clean_content = strip_lesson_item_tags(message.content)
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
