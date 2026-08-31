import os
import base64
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY")

if not API_KEY:
    print("❌ SARVAM_API_KEY not found in .env")
    exit()

print("🔑 Sarvam API loaded")
print("=" * 50)

# --------------------------------------------------
# STEP 1: STT
# --------------------------------------------------

audio_file = "kannada_test.wav"

print("🎤 Sending audio to Sarvam STT...")

with open(audio_file, "rb") as f:
    files = {
        "file": (
            audio_file,
            f,
            "audio/wav"
        )
    }

    data = {
        "model": "saaras:v3",
        "language_code": "kn-IN"
    }

    headers = {
        "api-subscription-key": API_KEY
    }

    response = requests.post(
        "https://api.sarvam.ai/speech-to-text",
        headers=headers,
        files=files,
        data=data
    )

if response.status_code != 200:
    print("❌ STT failed")
    print(response.text)
    exit()

stt_result = response.json()
transcript = stt_result["transcript"]

print("✅ STT successful!")
print("📝 Transcript:", transcript)

# --------------------------------------------------
# STEP 2: TTS
# --------------------------------------------------

print("\n🔊 Sending transcript to Sarvam TTS...")

tts_data = {
    "text": transcript,
    "target_language_code": "kn-IN",
    "model": "bulbul:v3"
}

tts_headers = {
    "api-subscription-key": API_KEY,
    "Content-Type": "application/json"
}

tts_response = requests.post(
    "https://api.sarvam.ai/text-to-speech",
    headers=tts_headers,
    json=tts_data
)

if tts_response.status_code != 200:
    print("❌ TTS failed")
    print(tts_response.text)
    exit()

tts_result = tts_response.json()

audio_base64 = tts_result["audios"][0]

with open("voice_echo_output.wav", "wb") as f:
    f.write(base64.b64decode(audio_base64))

print("✅ TTS successful!")
print("🔊 Created: voice_echo_output.wav")

print("\n" + "=" * 50)
print("🎉 VOICE ECHO DEMO COMPLETE!")
print("=" * 50)