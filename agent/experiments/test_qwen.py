import os
import sys
from dotenv import load_dotenv
load_dotenv(dotenv_path="../.env") # load from agent/.env or local
load_dotenv()

from groq import Groq
try:
    groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    response = groq_client.chat.completions.create(
        model="qwen/qwen3-32b",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello in Spanish."}
        ],
        max_tokens=100
    )
    print("Success! Response:", response.choices[0].message.content)
except Exception as e:
    print("Failed to call Groq Qwen:", e)
