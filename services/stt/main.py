"""
Zenvy STT microservice.

Wraps Sarvam Saaras v3 Speech-to-Text behind FastAPI.

Flow:
    Browser audio
        -> Gateway converts to WAV
        -> this service receives WAV
        -> Sarvam Saaras v3
        -> transcript + detected language
"""
import sys
from pathlib import Path

# Allow imports such as services.config when running inside the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
from fastapi import FastAPI, UploadFile, File, HTTPException

from services.config import SARVAM_API_KEY, SARVAM_BASE_URL


assert SARVAM_API_KEY is not None

app = FastAPI(title="Zenvy STT Service")


ALLOWED_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
}

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """
    Receive an audio file and transcribe it using Sarvam Saaras v3.

    The Gateway sends a WAV file, so we forward it to Sarvam as WAV.

    We use:
        model = saaras:v3
        mode = transcribe
        language_code = unknown

    'unknown' lets Sarvam automatically detect Kannada, Hindi,
    English, etc.
    """

    print(
        f"[STT] Incoming file: "
        f"name={file.filename!r}, "
        f"content_type={file.content_type!r}"
    )

    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type: {file.content_type}. "
                f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}"
            ),
        )

    audio_bytes = await file.read()

    print(f"[STT] Received {len(audio_bytes)} bytes")

    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="Uploaded file is empty.",
        )

    if len(audio_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail="File exceeds 10MB limit.",
        )

    try:
        response = requests.post(
            f"{SARVAM_BASE_URL}/speech-to-text",
            headers={
                "api-subscription-key": SARVAM_API_KEY,
            },
            files={
                "file": (
                    file.filename or "input.wav",
                    audio_bytes,
                    "audio/wav",
                )
            },
            data={
                "model": "saaras:v3",
                "mode": "transcribe",
                "language_code": "unknown",
            },
            timeout=30,
        )

    except requests.exceptions.RequestException as e:
        print(f"[STT] Network error calling Sarvam: {e}")

        raise HTTPException(
            status_code=502,
            detail="STT provider request failed.",
        )

    print(f"[STT] Sarvam HTTP status: {response.status_code}")

    if not response.ok:
        print(f"[STT] Sarvam error body: {response.text[:2000]}")

        raise HTTPException(
            status_code=502,
            detail=(
                "STT provider returned an error: "
                f"{response.status_code}"
            ),
        )

    try:
        result = response.json()
    except ValueError:
        print(f"[STT] Sarvam returned non-JSON: {response.text[:2000]}")

        raise HTTPException(
            status_code=502,
            detail="STT provider returned invalid JSON.",
        )

    print(f"[STT] Sarvam response: {result}")

    transcript = (result.get("transcript") or "").strip()
    language_code = result.get("language_code")

    print(
        f"[STT] FINAL transcript={transcript!r} "
        f"language_code={language_code!r}"
    )

    return {
        "text": transcript,
        "language_code": language_code,
    }