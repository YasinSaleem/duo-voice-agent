import os
import asyncio
import numpy as np
import mlx.core as mx
from pipecat.services.tts_service import TTSService, TextAggregationMode

from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame

class Qwen3TTSService(TTSService):
    """
    Local Apple Silicon TTS service running Alibaba Qwen3-TTS 0.6B Instruct via MLX.
    """
    def __init__(
        self,
        model_name: str | None = None,
        speaker: str | None = None,
        language: str | None = None,
        text_aggregation_mode: TextAggregationMode = TextAggregationMode.SENTENCE,
        **kwargs
    ):
        super().__init__(text_aggregation_mode=text_aggregation_mode, **kwargs)
        self._model_name = model_name or os.environ.get("QWEN_TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-4bit")
        self._speaker = speaker or os.environ.get("QWEN_TTS_SPEAKER", "Vivian")
        self._language = language or os.environ.get("QWEN_TTS_LANGUAGE", "Spanish")
        self._instruct = os.environ.get(
            "QWEN_TTS_INSTRUCT", 
            "Speak as a native bilingual expert fluent in both English and Spanish. "
            "Maintain a warm, patient, encouraging, and helpful tone with a professional language teacher vibe."
        )

        
        self._model = None
        self._processor = None

    def _load_model_if_needed(self):
        """
        Loads the Qwen3-TTS model and processor on the main thread.
        """
        if self._model is None:
            from mlx_audio.tts.utils import load_model
            mx.set_default_device(mx.gpu)
            print(f"[Qwen3-TTS] Loading Instruct model: {self._model_name}...")
            self._model = load_model(self._model_name)
            if hasattr(self._model, "processor"):
                self._processor = self._model.processor
            print("[Qwen3-TTS] Model and processor initialized successfully.")

    async def run_tts(self, text: str, context_id: str = None):
        try:
            # Load model if not already loaded in a background thread to prevent blocking
            await asyncio.to_thread(self._load_model_if_needed)
            
            # Record latency / usage metrics
            await self.start_tts_usage_metrics(text)
            
            loop = asyncio.get_running_loop()
            queue = asyncio.Queue()
            
            formatted_prompt = (
                f"<|voice_description|>"
                f"Voice: {self._speaker}. Language capability: English and Spanish. "
                f"Style profile: {self._instruct}"
                f"<|text|>{text}"
            )

            def producer_worker():
                try:
                    import mlx.core as mx
                    mx.set_default_device(mx.gpu) # Lock stream context
                    
                    # Pull model generator
                    chunks = self._model.generate_custom_voice(
                        text=formatted_prompt,
                        speaker=self._speaker,
                        language=self._language,
                        stream=True
                    )
                    
                    for chunk in chunks:
                        if chunk is not None:
                            # Thread-safe push back to main thread queue
                            loop.call_soon_threadsafe(queue.put_nowait, chunk)
                except Exception as e:
                    print(f"[Qwen3-TTS] Background producer synthesis failed: {e}")
                finally:
                    # Signal completion thread-safely
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            # Fire and forget the producer to the thread pool
            asyncio.create_task(asyncio.to_thread(producer_worker))

            # Main thread consumes queue items immediately as they arrive
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                    
                audio_data = chunk.audio if hasattr(chunk, "audio") else chunk
                if not isinstance(audio_data, np.ndarray):
                    continue
                    
                pcm_data = (np.clip(audio_data, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                
                yield TTSAudioRawFrame(
                    audio=pcm_data,
                    sample_rate=12000,
                    num_channels=1
                )
        except Exception as e:
            print(f"[Qwen3-TTS] Synthesis failed: {e}")
            yield ErrorFrame(error=f"Qwen3-TTS local synthesis error: {e}")


