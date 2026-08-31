"""
Zenvy Channel Gateway - port 8000

VOICE PIPELINE:

Browser
   ↓
WebM/Opus
   ↓
FFmpeg
   ↓
WAV
   ↓
STT :8001
   ↓
Transcript
   ↓
Appointment Orchestrator / LLM
   ↓
ACTUAL ASSISTANT REPLY
   ↓
TTS :8005
   ↓
WAV
   ↓
Browser


TEXT PIPELINE:

Browser Text
   ↓
Appointment Orchestrator / LLM
   ↓
ACTUAL ASSISTANT REPLY
   ↓
TTS :8005
   ↓
Browser
"""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

from fastapi import (
    FastAPI,
    UploadFile,
    File,
    Form,
    HTTPException,
)

from fastapi.responses import Response, FileResponse


# =========================================================
# PROJECT PATH
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

sys.path.insert(
    0,
    str(PROJECT_ROOT),
)


# =========================================================
# PROJECT IMPORTS
# =========================================================

from services.language_codes import LANGUAGE_CODE_TO_SHORT

from services.conversation_client import (
    create_session,
    add_turn,
)

from services.orchestrator.state_machine import (
    handle_turn,
)

from services.llm.client import (
    generate_reply,
)


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI(
    title="Zenvy Channel Gateway",
)


# =========================================================
# SERVICE URLS
# =========================================================

STT_URL = os.getenv("STT_URL", "http://127.0.0.1:8001/transcribe")
TTS_URL = os.getenv("TTS_URL", "http://127.0.0.1:8005/synthesize")


# =========================================================
# STATIC UI
# =========================================================

STATIC_DIR = (
    Path(__file__).resolve().parent / "static"
)


# =========================================================
# HOME
# =========================================================

@app.get("/")
async def serve_ui():
    """
    Serve the Zenvy voice assistant UI.
    """

    return FileResponse(
        STATIC_DIR / "index.html"
    )


# =========================================================
# LANGUAGE NORMALIZATION
# =========================================================

def _normalize_language(language: str | None) -> str:
    """
    Convert language values into our internal short format.

    Examples:

        en      -> en
        en-IN   -> en
        hi      -> hi
        hi-IN   -> hi
        kn      -> kn
        kn-IN   -> kn
    """

    if not language:
        return "en"

    language = language.strip().lower()

    if "-" in language:
        language = language.split("-")[0]

    if "_" in language:
        language = language.split("_")[0]

    supported = {
        "en",
        "hi",
        "kn",
        "ta",
        "te",
        "mr",
        "bn",
        "gu",
        "ml",
        "pa",
        "od",
    }

    if language in supported:
        return language

    return "en"


# =========================================================
# AUDIO CONVERSION
# =========================================================

def _convert_to_wav(
    input_bytes: bytes,
    input_suffix: str,
) -> bytes:
    """
    Convert browser audio such as WebM/Opus into
    16 kHz mono WAV using FFmpeg.
    """

    input_suffix = (
        input_suffix
        if input_suffix.startswith(".")
        else "." + input_suffix
    )

    input_file = None
    output_file = None

    try:

        with tempfile.NamedTemporaryFile(
            suffix=input_suffix,
            delete=False,
        ) as input_tmp:

            input_tmp.write(input_bytes)
            input_file = input_tmp.name

        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as output_tmp:

            output_file = output_tmp.name

        command = [
            "ffmpeg",
            "-y",
            "-i",
            input_file,
            "-ar",
            "16000",
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            output_file,
        ]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:

            raise HTTPException(
                status_code=500,
                detail=(
                    "FFmpeg audio conversion failed: "
                    + result.stderr[-1000:]
                ),
            )

        with open(
            output_file,
            "rb",
        ) as f:

            return f.read()

    finally:

        if input_file:

            try:
                Path(input_file).unlink(
                    missing_ok=True
                )
            except Exception:
                pass

        if output_file:

            try:
                Path(output_file).unlink(
                    missing_ok=True
                )
            except Exception:
                pass


# =========================================================
# TTS
# =========================================================

def _call_tts(
    text: str,
    short_lang: str,
) -> requests.Response:
    """
    Convert the ACTUAL ASSISTANT REPLY into speech.

    IMPORTANT:

        WRONG:
        user text -> TTS

        CORRECT:
        user text
             ↓
        orchestrator / LLM
             ↓
        assistant reply
             ↓
        TTS
    """

    if not text or not text.strip():

        raise HTTPException(
            status_code=500,
            detail="Cannot generate TTS for empty text.",
        )

    text = text.strip()

    # Normalize language.
    short_lang = _normalize_language(
        short_lang
    )

    language_map = {
        "en": "en-IN",
        "hi": "hi-IN",
        "kn": "kn-IN",
        "ta": "ta-IN",
        "te": "te-IN",
        "mr": "mr-IN",
        "bn": "bn-IN",
        "gu": "gu-IN",
        "ml": "ml-IN",
        "pa": "pa-IN",
        "od": "od-IN",
    }

    language_code = language_map.get(
        short_lang,
        "en-IN",
    )

    print(
        "\n----------------------------------------"
    )

    print(
        "[Gateway] TTS INPUT = ASSISTANT REPLY"
    )

    print(
        f"[Gateway] TTS TEXT: {text}"
    )

    print(
        f"[Gateway] TTS LANGUAGE: {language_code}"
    )

    print(
        "----------------------------------------"
    )

    try:

        response = requests.post(
            TTS_URL,

            json={
                "text": text,
                "language": short_lang,
            },

            timeout=60,
        )

        print(
            f"[Gateway] TTS STATUS: "
            f"{response.status_code}"
        )

        if not response.ok:

            print(
                "[Gateway] TTS ERROR:",
                response.text,
            )

        response.raise_for_status()

        return response

    except requests.exceptions.RequestException as e:

        raise HTTPException(
            status_code=502,
            detail=f"TTS service failed: {e}",
        )


# =========================================================
# SESSION
# =========================================================

def _get_or_create_session(
    session_id: str | None,
    short_lang: str,
) -> str | None:
    """
    Reuse an existing conversation session.

    If no session exists, create one.
    """

    short_lang = _normalize_language(
        short_lang
    )

    if session_id:

        print(
            f"[Gateway] EXISTING SESSION: "
            f"{session_id}"
        )

        return session_id

    try:

        session = create_session(
            user_id=str(uuid.uuid4()),
            channel="web",
            language=short_lang,
        )

        new_session_id = session[
            "session_id"
        ]

        print(
            f"[Gateway] NEW SESSION: "
            f"{new_session_id}"
        )

        return new_session_id

    except Exception as e:

        print(
            "[Gateway] Session creation failed:",
            e,
        )

        return None


# =========================================================
# TEXT → AI REPLY → TTS
# =========================================================

@app.post("/channels/web/tts")
async def web_tts(
    text: str = Form(...),

    # Accept both names so old/new frontend code works.
    language: str = Form("en"),

    language_code: str | None = Form(None),

    session_id: str | None = Form(None),
):
    """
    Conversational Text-to-Speech.

    USER TEXT IS NEVER SENT DIRECTLY TO TTS.

    Example:

        User:
        "I wanted to book an appointment at 3 PM"

             ↓

        Orchestrator

             ↓

        Assistant:
        "Sure, I can help with that.
         Which department or doctor would you like to see?"

             ↓

        TTS

             ↓

        Spoken assistant response
    """

    text = text.strip()

    if not text:

        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    # Prefer language_code if frontend sends it.
    requested_language = (
        language_code
        if language_code
        else language
    )

    short_lang = _normalize_language(
        requested_language
    )

    print(
        "\n========================================"
    )

    print(
        "[Gateway] NEW TEXT REQUEST"
    )

    print(
        f"[Gateway] USER TEXT: {text}"
    )

    print(
        f"[Gateway] LANGUAGE: "
        f"{requested_language} -> {short_lang}"
    )

    # =====================================================
    # 1. CREATE / RESTORE SESSION
    # =====================================================

    session_id = _get_or_create_session(
        session_id,
        short_lang,
    )

    # =====================================================
    # 2. GENERATE ACTUAL ASSISTANT REPLY
    # =====================================================

    print(
        "[Gateway] GENERATING ACTUAL ASSISTANT REPLY..."
    )

    try:

        if session_id:

            reply_text = handle_turn(
                session_id,
                short_lang,
                text,
            )

        else:

            reply_text = generate_reply(
                text,
                short_lang,
            )

    except Exception as e:

        print(
            "[Gateway] REPLY GENERATION ERROR:",
            e,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Reply generation failed: "
                f"{e}"
            ),
        )

    # =====================================================
    # 3. SAFETY CHECK
    # =====================================================

    if not reply_text:

        reply_text = (
            "Sorry, I was unable to generate "
            "a response."
        )

    reply_text = str(
        reply_text
    ).strip()

    # =====================================================
    # VERY IMPORTANT DEBUG
    # =====================================================

    print(
        "\n******** CONVERSATION ********"
    )

    print(
        f"USER     : {text}"
    )

    print(
        f"ASSISTANT : {reply_text}"
    )

    print(
        "*******************************"
    )

    # =====================================================
    # 4. SAVE CONVERSATION
    # =====================================================

    if session_id:

        try:

            add_turn(
                session_id,
                "user",
                text,
                short_lang,
                input_text=text,
            )

            add_turn(
                session_id,
                "assistant",
                reply_text,
                short_lang,
                response_text=reply_text,
            )

        except Exception as e:

            print(
                "[Gateway] Conversation logging failed:",
                e,
            )

    # =====================================================
    # 5. ASSISTANT REPLY → TTS
    # =====================================================

    print(
        "[Gateway] SENDING ASSISTANT REPLY TO TTS..."
    )

    tts_response = _call_tts(
        reply_text,
        short_lang,
    )

    # =====================================================
    # 6. RETURN AUDIO
    # =====================================================

    headers = {

        "X-Input-Text": quote(
            text
        ),

        "X-Transcript": quote(
            text
        ),

        "X-Reply-Text": quote(
            reply_text
        ),

        "X-Session-Id": (
            session_id or ""
        ),

        "X-Language": (
            requested_language
        ),
    }

    print(
        "[Gateway] TEXT RESPONSE SENT"
    )

    print(
        "========================================\n"
    )

    return Response(
        content=tts_response.content,
        media_type="audio/wav",
        headers=headers,
    )


# =========================================================
# VOICE CHAT
# =========================================================

@app.post("/channels/web/chat")
async def web_chat(
    file: UploadFile = File(...),
    session_id: str | None = Form(None),
):
    """
    Complete voice assistant pipeline.

    Browser
       ↓
    STT
       ↓
    Orchestrator
       ↓
    ACTUAL ASSISTANT REPLY
       ↓
    TTS
       ↓
    Browser
    """

    # =====================================================
    # 1. RECEIVE AUDIO
    # =====================================================

    raw_bytes = await file.read()

    if not raw_bytes:

        raise HTTPException(
            status_code=400,
            detail="Uploaded audio is empty.",
        )

    print(
        "\n========================================"
    )

    print(
        "[Gateway] NEW VOICE REQUEST"
    )

    print(
        f"[Gateway] FILE: {file.filename}"
    )

    print(
        f"[Gateway] CONTENT TYPE: "
        f"{file.content_type}"
    )

    print(
        f"[Gateway] SIZE: "
        f"{len(raw_bytes)} bytes"
    )

    # =====================================================
    # 2. WEBM → WAV
    # =====================================================

    suffix = (
        Path(
            file.filename or "input.webm"
        ).suffix
        or ".webm"
    )

    wav_bytes = _convert_to_wav(
        raw_bytes,
        suffix,
    )

    print(
        f"[Gateway] WAV SIZE: "
        f"{len(wav_bytes)} bytes"
    )

    # =====================================================
    # 3. STT
    # =====================================================

    try:

        stt_response = requests.post(

            STT_URL,

            files={
                "file": (
                    "input.wav",
                    wav_bytes,
                    "audio/wav",
                )
            },

            data={
                "language_code": "en-IN",
            },

            timeout=30,
        )

        stt_response.raise_for_status()

    except requests.exceptions.RequestException as e:

        raise HTTPException(
            status_code=502,
            detail=f"STT service failed: {e}",
        )

    stt_result = stt_response.json()

    print(
        f"[Gateway] STT RESPONSE: "
        f"{stt_result}"
    )

    transcript = (
        stt_result.get("transcript")
        or stt_result.get("text")
        or ""
    ).strip()

    detected_language = (
        stt_result.get("language_code")
        or "en-IN"
    )

    short_lang = _normalize_language(
        detected_language
    )

    # =====================================================
    # 4. EMPTY SPEECH
    # =====================================================

    if not transcript:

        retry_text = (
            "Sorry, I didn't catch that. "
            "Please hold the button and speak clearly."
        )

        tts_response = _call_tts(
            retry_text,
            "en",
        )

        return Response(

            content=tts_response.content,

            media_type="audio/wav",

            headers={
                "X-Transcript": quote(
                    "(no speech detected)"
                ),

                "X-Reply-Text": quote(
                    retry_text
                ),

                "X-Session-Id": (
                    session_id or ""
                ),

                "X-Language": (
                    detected_language
                ),
            },
        )

    # =====================================================
    # 5. CREATE / RESTORE SESSION
    # =====================================================

    session_id = _get_or_create_session(
        session_id,
        short_lang,
    )

    # =====================================================
    # 6. GENERATE ACTUAL ASSISTANT REPLY
    # =====================================================

    print(
        f"\n[Gateway] USER SAID:"
    )

    print(
        transcript
    )

    print(
        "\n[Gateway] GENERATING ACTUAL ASSISTANT REPLY..."
    )

    try:

        if session_id:

            reply_text = handle_turn(
                session_id,
                short_lang,
                transcript,
            )

        else:

            reply_text = generate_reply(
                transcript,
                short_lang,
            )

    except Exception as e:

        print(
            "[Gateway] REPLY GENERATION ERROR:",
            e,
        )

        raise HTTPException(
            status_code=502,
            detail=(
                "Reply generation failed: "
                f"{e}"
            ),
        )

    # =====================================================
    # 7. SAFETY CHECK
    # =====================================================

    if not reply_text:

        reply_text = (
            "Sorry, I was unable to generate "
            "a response."
        )

    reply_text = str(
        reply_text
    ).strip()

    # =====================================================
    # CRITICAL DEBUG
    # =====================================================

    print(
        "\n******** VOICE CONVERSATION ********"
    )

    print(
        f"USER TRANSCRIPT : {transcript}"
    )

    print(
        f"ASSISTANT REPLY : {reply_text}"
    )

    print(
        "************************************"
    )

    # =====================================================
    # 8. SAVE CONVERSATION
    # =====================================================

    if session_id:

        try:

            add_turn(
                session_id,
                "user",
                transcript,
                short_lang,
                input_text=transcript,
            )

            add_turn(
                session_id,
                "assistant",
                reply_text,
                short_lang,
                response_text=reply_text,
            )

        except Exception as e:

            print(
                "[Gateway] Conversation logging failed:",
                e,
            )

    # =====================================================
    # 9. ASSISTANT REPLY → TTS
    # =====================================================

    print(
        "\n[Gateway] SENDING ASSISTANT REPLY TO TTS"
    )

    tts_response = _call_tts(
        reply_text,
        short_lang,
    )

    # =====================================================
    # 10. RETURN AUDIO + TEXT
    # =====================================================

    headers = {

        "X-Transcript": quote(
            transcript
        ),

        "X-Reply-Text": quote(
            reply_text
        ),

        "X-Session-Id": (
            session_id or ""
        ),

        "X-Language": (
            detected_language
        ),
    }

    print(
        "\n[Gateway] VOICE RESPONSE SENT"
    )

    print(
        "========================================\n"
    )

    return Response(

        content=tts_response.content,

        media_type="audio/wav",

        headers=headers,
    )
# =========================================================
# ASK ZENVY - TEXT CHAT
# =========================================================

@app.post("/channels/web/ask")
async def ask_zenvy(
    text: str = Form(...),
    language: str = Form("en"),
    session_id: str | None = Form(None),
):
    """
    Text chat endpoint.

    User text
        ↓
    Orchestrator / LLM
        ↓
    Assistant reply
    """

    text = text.strip()

    if not text:
        raise HTTPException(
            status_code=400,
            detail="Text cannot be empty.",
        )

    short_lang = _normalize_language(language)

    print("\n========================================")
    print("[Gateway] NEW ASK ZENVY REQUEST")
    print(f"[Gateway] USER TEXT: {text}")
    print(f"[Gateway] LANGUAGE: {short_lang}")

    # Create or reuse session
    session_id = _get_or_create_session(
        session_id,
        short_lang,
    )

    # Generate assistant reply
    try:

        if session_id:

            reply_text = handle_turn(
                session_id,
                short_lang,
                text,
            )

        else:

            reply_text = generate_reply(
                text,
                short_lang,
            )

    except Exception as e:

        print(
            "[Gateway] ASK ZENVY ERROR:",
            e,
        )

        raise HTTPException(
            status_code=502,
            detail=f"Reply generation failed: {e}",
        )

    # Safety check
    if not reply_text:

        reply_text = (
            "Sorry, I was unable to generate "
            "a response."
        )

    reply_text = str(reply_text).strip()

    # Save conversation
    if session_id:

        try:

            add_turn(
                session_id,
                "user",
                text,
                short_lang,
                input_text=text,
            )

            add_turn(
                session_id,
                "assistant",
                reply_text,
                short_lang,
                response_text=reply_text,
            )

        except Exception as e:

            print(
                "[Gateway] Conversation logging failed:",
                e,
            )

    print(
        f"[Gateway] ASSISTANT REPLY: {reply_text}"
    )

    print("========================================\n")

    return {
        "success": True,
        "input": text,
        "reply": reply_text,
        "language": short_lang,
        "session_id": session_id,
    }