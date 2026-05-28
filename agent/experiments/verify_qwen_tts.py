import os
import asyncio
import numpy as np
import soundfile as sf
import mlx.core as mx
from dotenv import load_dotenv

# Load environment
load_dotenv(dotenv_path="agent/.env")
load_dotenv()

from agent.services.qwen_tts import Qwen3TTSService
from pipecat.services.tts_service import TextAggregationMode
from pipecat.frames.frames import TTSAudioRawFrame, ErrorFrame

async def main():
    # Crucial: Initialize a valid GPU stream for this thread
    mx.set_default_device(mx.gpu)
    print("=== Qwen3-TTS Local Verification (12kHz MLX) ===")

    
    # Initialize service
    service = Qwen3TTSService(
        text_aggregation_mode=TextAggregationMode.SENTENCE
    )
    
    test_text = "Hello! Today we will learn Spanish. Bienvenido a tu clase de español hoy. Ready to begin?"
    print(f"Synthesizing text: '{test_text}'")
    
    audio_buffers = []
    
    # Run the generator
    async for frame in service.run_tts(test_text):
        if isinstance(frame, TTSAudioRawFrame):
            audio_bytes = frame.audio
            sample_rate = frame.sample_rate
            print(f"Received audio frame: {len(audio_bytes)} bytes, sample_rate={sample_rate}")
            # Convert PCM 16-bit bytes back to numpy float32 for soundfile saving
            pcm_array = np.frombuffer(audio_bytes, dtype=np.int16)
            float32_array = pcm_array.astype(np.float32) / 32767.0
            audio_buffers.append(float32_array)
        elif isinstance(frame, ErrorFrame):
            print(f"ERROR returned from synthesis: {frame.error}")
            
    if audio_buffers:
        full_audio = np.concatenate(audio_buffers)
        output_path = "agent/experiments/qwen_tts_test.wav"
        print(f"Writing concatenated audio to '{output_path}'...")
        sf.write(output_path, full_audio, sample_rate)
        print("Success! Test complete.")
    else:
        print("Failed to generate any audio frames.")

if __name__ == "__main__":
    asyncio.run(main())
