"""
HTTP client for Team C's Conversation Service (sessions + turns).

Team A never talks to Postgres/Supabase directly -- Team C owns the
schema and exposes it over REST. This wraps that API the same way
services/llm/client.py wraps Sarvam's API: plain requests calls, no
ORM, no DB driver here at all.

Team C's service must be running separately (their own repo/venv),
default at http://127.0.0.1:8002 in local dev -- see
TEAM_C_BASE_URL below. Configure via env var if it moves.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

TEAM_C_BASE_URL = os.getenv("TEAM_C_BASE_URL", "http://127.0.0.1:8002")

_SESSIONS_URL = f"{TEAM_C_BASE_URL}/api/v1/sessions"


def create_session(user_id: str, channel: str, language: str, uhid: str | None = None) -> dict:
    """
    Create a new conversation session. channel must be 'phone', 'sms', or 'web'.
    Returns the full session dict, including the generated 'session_id'.
    Raises requests.exceptions.RequestException if Team C's service is
    unreachable or rejects the request -- callers should decide whether
    to degrade gracefully (log locally, continue without persistence)
    or fail hard, depending on context.
    """
    response = requests.post(
        _SESSIONS_URL,
        json={
            "user_id": user_id,
            "channel": channel,
            "language": language,
            "uhid": uhid,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def add_turn(
    session_id: str,
    speaker: str,
    content: str,
    language: str,
    input_text: str | None = None,
    response_text: str | None = None,
) -> dict:
    """
    Log one conversation turn against an existing session.
    speaker must be 'user', 'assistant', or 'system'.

    Convention used by the gateway: each exchange is logged as TWO
    turns -- one speaker='user' turn (content=input_text=what the
    patient said) and one speaker='assistant' turn (content=
    response_text=what the bot replied) -- rather than packing both
    directions into a single turn row.
    """
    response = requests.post(
        f"{_SESSIONS_URL}/{session_id}/turns",
        json={
            "speaker": speaker,
            "content": content,
            "language": language,
            "input_text": input_text,
            "response_text": response_text,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_turns(session_id: str) -> list[dict]:
    """Fetch all turns for a session, in order."""
    response = requests.get(f"{_SESSIONS_URL}/{session_id}/turns", timeout=10)
    response.raise_for_status()
    return response.json()


def get_session(session_id: str) -> dict:
    """Fetch a session's metadata (does not include turns)."""
    response = requests.get(f"{_SESSIONS_URL}/{session_id}", timeout=10)
    response.raise_for_status()
    return response.json()


_APPOINTMENTS_URL = f"{TEAM_C_BASE_URL}/api/v1/appointments"


def create_appointment(
    session_id: str,
    patient_uhid: str,
    doctor_name: str,
    appointment_datetime: str,
    status: str = "pending",
    booking_info: dict | None = None,
) -> dict:
    """
    Create an appointment against Team C's ai_appointments table.
    appointment_datetime must be an ISO 8601 string (e.g.
    '2026-08-28T10:30:00'). patient_uhid and doctor_name are required
    and cannot be empty per Team C's schema.
    """
    response = requests.post(
        _APPOINTMENTS_URL,
        json={
            "session_id": session_id,
            "patient_uhid": patient_uhid,
            "doctor_name": doctor_name,
            "appointment_datetime": appointment_datetime,
            "status": status,
            "booking_info": booking_info,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()