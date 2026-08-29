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
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import Response, FileResponse

from services.language_codes import LANGUAGE_CODE_TO_SHORT
from services.conversation_client import create_session, add_turn
from services.orchestrator.state_machine import handle_turn
from services.llm.client import generate_reply

app = FastAPI(title="Zenvy Channel Gateway")

STT_URL = os.getenv("STT_URL", "http://127.0.0.1:8001/transcribe")
TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:8005/synthesize")

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
async def web_chat(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """
    Web channel handler, now session-aware:
    browser audio blob -> wav -> STT -> LLM reply -> TTS -> wav back,
    with the exchange persisted via Team C's Conversation Service.

    If session_id is not provided, a new session is created and its id
    returned in the X-Session-Id header so the browser can send it on
    the next request, keeping a conversation as one continuous session.

    Persistence failures (Team C's service unreachable) are logged and
    swallowed rather than raised -- the voice pipeline itself should
    keep working even if the DB-backed session service is temporarily
    down, matching the HTTP-only, no-hard-dependency integration
    boundary with Team C.
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

    # Session handling: create one if the browser didn't send an existing
    # session_id yet. This must happen BEFORE generating the reply, since
    # the orchestrator's booking state machine needs a session_id to track
    # progress (which slots are filled) across turns. Persistence failures
    # are logged, not raised -- the patient should still get a spoken reply
    # even if Team C's service or Supabase is briefly unreachable (booking
    # simply won't be trackable across turns in that case).
    if not session_id:
        try:
            session = create_session(
                user_id=str(uuid.uuid4()), channel="web", language=short_lang
            )
            session_id = session["session_id"]
        except Exception as e:
            print(f"[Gateway] Could not create session (continuing without persistence): {e}")
            session_id = None

    try:
        if session_id:
            reply_text = handle_turn(session_id, short_lang, transcript)
        else:
            # No session at all (Team C's service unreachable) -- fall back
            # to plain Q&A; booking flows require a session, so they're
            # unavailable in this degraded case.
            reply_text = generate_reply(transcript, short_lang)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Reply generation failed: {e}")

    if session_id:
        try:
            add_turn(session_id, "user", transcript, short_lang, input_text=transcript)
            add_turn(session_id, "assistant", reply_text, short_lang, response_text=reply_text)
        except Exception as e:
            print(f"[Gateway] Could not log turn (continuing): {e}")

    try:
        tts_response = requests.post(
            TTS_URL,
            json={"text": reply_text, "language": short_lang},
            timeout=30,
        )
        tts_response.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"TTS service failed: {e}")

    headers = {
        "X-Transcript": quote(transcript),
        "X-Reply-Text": quote(reply_text),
        "X-Language": lang_code,
    }
    if session_id:
        headers["X-Session-Id"] = str(session_id)

    return Response(
        content=tts_response.content,
        media_type="audio/wav",
        headers=headers,
    )