import os
import sys
from dotenv import load_dotenv

# Load env variables
load_dotenv(dotenv_path="agent/.env")
load_dotenv()

def main():
    model_name = os.environ.get("QWEN_TTS_MODEL", "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-4bit")
    print(f"=== Qwen3-TTS Pre-download Utility ===")
    print(f"Target Model: {model_name}")
    print("Downloading weights and config from Hugging Face and persisting to local cache...")
    try:
        from mlx_audio.tts.utils import load_model
        load_model(model_name)
        print("\nSuccess! Qwen3-TTS model is now fully persisted locally.")
        print("Subsequent runs of 'npm run dev:all' will use this cached model offline without downloading.")
    except Exception as e:
        print(f"\nFailed to pre-download model: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
