"""
TTS microservice.
Wraps Sarvam Bulbul v2 behind a FastAPI endpoint so other services
(the future Channel Gateway, etc.) can request synthesized speech
over HTTP instead of hitting Sarvam directly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import base64
import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from services.config import SARVAM_API_KEY, SARVAM_BASE_URL

assert SARVAM_API_KEY is not None

app = FastAPI(title="Zenvy TTS Service")

LANGUAGE_CODES = {
    "kn": "kn-IN",
    "hi": "hi-IN",
    "en": "en-IN",
}

MAX_TEXT_LENGTH = 500


class SynthesizeRequest(BaseModel):
    """Request body for /synthesize: the text to speak and target language."""
    text: str
    language: str  # one of: kn, hi, en


@app.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """
    Accept text and a language code, call Sarvam Bulbul v2, and return
    the synthesized audio as a playable WAV response.
    Validates language and text length before calling the API, and
    returns a clean error instead of crashing on bad input or
    upstream failures.
    """
    if req.language not in LANGUAGE_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language: '{req.language}'. Allowed: {sorted(LANGUAGE_CODES)}",
        )

    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty.")

    if len(req.text) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Text exceeds {MAX_TEXT_LENGTH} character limit ({len(req.text)} given).",
        )

    lang_code = LANGUAGE_CODES[req.language]
    print(f"[TTS] Synthesizing ({lang_code}): {req.text}")

    try:
        response = requests.post(
            f"{SARVAM_BASE_URL}/text-to-speech",
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "text": req.text,
                "target_language_code": lang_code,
                "speaker": "priya",
                "model": "bulbul:v3",
            },
            timeout=30,
        )
        if response.status_code != 200:
            print(f"[TTS] Sarvam API error {response.status_code}: {response.text}")
            raise HTTPException(status_code=502, detail=f"TTS provider error: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[TTS] Sarvam API error: {e}")
        raise HTTPException(status_code=502, detail="TTS provider request failed.")
        
    audio_b64 = response.json()["audios"][0]
    audio_bytes = base64.b64decode(audio_b64)

    print(f"[TTS] Generated {len(audio_bytes)} bytes of audio")

    return Response(content=audio_bytes, media_type="audio/wav")