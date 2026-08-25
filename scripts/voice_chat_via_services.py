"""
Voice conversation loop (standalone, no persistence).

Extends voice_echo_via_services.py: instead of just echoing back the
transcript as speech, this calls the LLM (Sarvam Chat Completions) in
between to generate an actual reply, then speaks that reply back in
the same language the patient used.

audio in -> STT service (8001) -> LLM reply (services/llm/client.py)
    -> TTS service (8005) -> audio out

This is a standalone test script (no session/turn persistence via
Team C's service yet -- see scripts/voice_echo_with_persistence.py
for that pattern once you're ready to wire this in).

Scope note: this script covers response *generation* only (roadmap
Day 26). It does not do intent/entity extraction or multi-turn state
(GREETING -> COLLECTING_DOCTOR -> ... in roadmap Days 31-35) -- each
call is a single, independent turn.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from services.llm.client import generate_reply
from services.language_codes import LANGUAGE_CODE_TO_SHORT

STT_URL = "http://127.0.0.1:8001/transcribe"
TTS_URL = "http://127.0.0.1:8005/synthesize"


def transcribe_via_service(file_path: str) -> dict:
    """Call the running STT service and return {text, language_code}."""
    filename = Path(file_path).name
    with open(file_path, "rb") as f:
        response = requests.post(
            STT_URL,
            files={"file": (filename, f, "audio/wav")},
        )
    response.raise_for_status()
    return response.json()


def synthesize_via_service(text: str, short_lang: str, out_path: str) -> str:
    """Call the running TTS service and save the returned WAV audio."""
    response = requests.post(
        TTS_URL,
        json={"text": text, "language": short_lang},
    )
    response.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(response.content)
    return out_path


def voice_chat_via_services(input_audio_path: str, reply_output_path: str) -> None:
    """Full round trip: audio -> STT -> LLM reply -> TTS -> audio, single turn."""
    stt_result = transcribe_via_service(input_audio_path)
    transcript = stt_result["text"]
    lang_code = stt_result["language_code"]
    print(f"Heard ({lang_code}): {transcript}")

    short_lang = LANGUAGE_CODE_TO_SHORT[lang_code]

    reply_text = generate_reply(transcript, short_lang)
    print(f"Reply ({short_lang}): {reply_text}")

    synthesize_via_service(reply_text, short_lang, reply_output_path)
    print(f"Reply audio saved to: {reply_output_path}")


if __name__ == "__main__":
    voice_chat_via_services("scripts/output_kn-IN.wav", "scripts/chat_reply_kn-IN.wav")