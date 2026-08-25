"""
Channel Gateway - port 8000.

Roadmap scope (Week 3): normalize phone/WhatsApp/web channels into one
internal pipeline. This is the FIRST channel route: /channels/web/chat.
Phone and WhatsApp routes are not built yet.

The web route accepts a browser-recorded audio clip (webm/ogg, from the
MediaRecorder API), converts it to wav, and runs it through the same
STT -> LLM -> TTS pipeline as scripts/voice_chat_via_services.py --
just reachable over HTTP instead of local files/mic.

Requires ffmpeg installed and on PATH (browsers record webm/ogg, not
wav, and the STT service only accepts wav/mp3). Check with:
    ffmpeg -version
"""
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import Response, FileResponse

from services.llm.client import generate_reply
from services.language_codes import LANGUAGE_CODE_TO_SHORT

app = FastAPI(title="Zenvy Channel Gateway")

STT_URL = "http://127.0.0.1:8001/transcribe"
TTS_URL = "http://127.0.0.1:8005/synthesize"

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/")
async def serve_ui():
    """Serve the push-to-talk demo page for the web channel."""
    return FileResponse(STATIC_DIR / "index.html")


def _convert_to_wav(input_bytes: bytes, input_suffix: str) -> bytes:
    """
    Browser MediaRecorder produces webm/ogg, not wav. Convert via ffmpeg
    (must be installed separately -- this is a system binary, not a pip
    package) before handing off to the STT service, which only accepts
    wav/mp3.
    """
    with tempfile.NamedTemporaryFile(suffix=input_suffix, delete=False) as in_file:
        in_file.write(input_bytes)
        in_path = Path(in_file.name)
    out_path = in_path.with_suffix(".converted.wav")

    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(in_path), "-ar", "16000", "-ac", "1", str(out_path)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr.decode(errors="ignore")[-500:]
            raise HTTPException(
                status_code=500,
                detail=(
                    "Audio conversion failed. Is ffmpeg installed and on PATH? "
                    f"ffmpeg said: {stderr_tail}"
                ),
            )
        return out_path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg not found. Install it and make sure it's on your PATH.",
        )
    finally:
        in_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


@app.post("/channels/web/chat")
async def web_chat(file: UploadFile = File(...)):
    """
    Single-turn web channel handler:
    browser audio blob -> wav -> STT -> LLM reply -> TTS -> wav back.

    Returns the reply audio directly as the response body (audio/wav),
    with the transcript and reply text attached as headers (percent-
    encoded, since HTTP headers can't carry raw Kannada/Hindi text) so
    the browser can display them alongside playback.
    """
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio is empty.")

    suffix = Path(file.filename or "input.webm").suffix or ".webm"
    wav_bytes = _convert_to_wav(raw_bytes, suffix)

    try:
        stt_response = requests.post(
            STT_URL,
            files={"file": ("input.wav", wav_bytes, "audio/wav")},
            timeout=30,
        )
        stt_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"STT service failed: {e}")

    stt_result = stt_response.json()
    transcript = (stt_result.get("text") or "").strip()
    lang_code = stt_result["language_code"]

    short_lang = LANGUAGE_CODE_TO_SHORT.get(lang_code)

    # Empty transcript (near-silence) or a language outside kn/hi/en usually
    # means the clip was too short or too quiet for STT to work with, not a
    # real request -- ask the patient to try again instead of forwarding
    # empty/garbage text to the LLM (which errors on empty content anyway).
    if not transcript or short_lang is None:
        retry_text = "Sorry, I didn't catch that. Please hold the button and speak clearly."
        try:
            tts_response = requests.post(
                TTS_URL,
                json={"text": retry_text, "language": "en"},
                timeout=30,
            )
            tts_response.raise_for_status()
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=502, detail=f"TTS service failed: {e}")

        return Response(
            content=tts_response.content,
            media_type="audio/wav",
            headers={
                "X-Transcript": quote(transcript or "(no speech detected)"),
                "X-Reply-Text": quote(retry_text),
                "X-Language": lang_code,
            },
        )

    try:
        reply_text = generate_reply(transcript, short_lang)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM reply generation failed: {e}")

    try:
        tts_response = requests.post(
            TTS_URL,
            json={"text": reply_text, "language": short_lang},
            timeout=30,
        )
        tts_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"TTS service failed: {e}")

    return Response(
        content=tts_response.content,
        media_type="audio/wav",
        headers={
            "X-Transcript": quote(transcript),
            "X-Reply-Text": quote(reply_text),
            "X-Language": lang_code,
        },
    )