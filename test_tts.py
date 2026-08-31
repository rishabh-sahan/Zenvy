import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SARVAM_API_KEY")

if not api_key:
    print("❌ SARVAM_API_KEY not found in .env")
    exit()

print("🔑 API key loaded")
print("🚀 Testing Sarvam TTS...")

url = "https://api.sarvam.ai/text-to-speech"

headers = {
    "api-subscription-key": api_key,
    "Content-Type": "application/json"
}

data = {
    "text": "ನಮಸ್ಕಾರ, ನಿಮಗೆ ಹೇಗಿದೆ?",
    "target_language_code": "kn-IN",
    "model": "bulbul:v3"
}

response = requests.post(
    url,
    headers=headers,
    json=data
)

print("HTTP Status:", response.status_code)

if response.status_code == 200:
    result = response.json()

    audio_base64 = result["audios"][0]

    with open("kannada_test.wav", "wb") as f:
        f.write(base64.b64decode(audio_base64))

    print("✅ TTS WORKS!")
    print("🔊 Created: kannada_test.wav")

else:
    print("❌ TTS FAILED")
    print(response.text)