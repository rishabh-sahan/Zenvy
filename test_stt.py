import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("SARVAM_API_KEY")

if not api_key:
    print("❌ SARVAM_API_KEY not found in .env")
    exit()

print("🔑 API key loaded")
print("🎤 Testing Sarvam STT...")

url = "https://api.sarvam.ai/speech-to-text"

headers = {
    "api-subscription-key": api_key
}

files = {
    "file": (
        "kannada_test.wav",
        open("kannada_test.wav", "rb"),
        "audio/wav"
    )
}

data = {
    "model": "saaras:v3",
    "language_code": "kn-IN"
}

response = requests.post(
    url,
    headers=headers,
    files=files,
    data=data
)

print("HTTP Status:", response.status_code)

if response.status_code == 200:
    result = response.json()

    print("✅ STT WORKS!")
    print("📝 Transcription:")
    print(result)

else:
    print("❌ STT FAILED")
    print(response.text)