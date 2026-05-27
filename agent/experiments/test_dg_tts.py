import os
import urllib.request
import json
import ssl
import certifi
from dotenv import load_dotenv

load_dotenv()

# We will try a simple HTTPS POST request to Deepgram's REST API for TTS to see the actual error message!
api_key = os.getenv("DEEPGRAM_API_KEY")
url = "https://api.deepgram.com/v1/speak?model=aura-2-selena-es"

headers = {
    "Authorization": f"Token {api_key}",
    "Content-Type": "application/json"
}

data = json.dumps({"text": "Hola, ¿cómo estás?"}).encode("utf-8")

context = ssl.create_default_context(cafile=certifi.where())
req = urllib.request.Request(url, data=data, headers=headers, method="POST")

try:
    with urllib.request.urlopen(req, context=context) as response:
        print("Success! Status code:", response.status)
        print("Response headers:", response.headers)
except Exception as e:
    print("Failed!")
    if hasattr(e, "read"):
        print("Error response:", e.read().decode())
    else:
        print("Error:", e)
