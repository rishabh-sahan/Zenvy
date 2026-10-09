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


class SlotUnavailableError(Exception):
    """The slot was taken (or the hold lapsed) before it could be booked."""


_DOCTORS_URL = f"{TEAM_C_BASE_URL}/api/v1/doctors"
_SLOTS_URL = f"{TEAM_C_BASE_URL}/api/v1/slots"


def find_doctors(query: str) -> list[dict]:
    """
    Doctors matching a name or department ("Arjun Rao", "Cardiology").
    Each dict has doctor_id, name, specialty, hospital_name, city, slot_minutes.
    """
    response = requests.get(_DOCTORS_URL, params={"query": query}, timeout=10)
    response.raise_for_status()
    return response.json()


def get_free_slots(
    doctor_id: str, day: str, near: str | None = None, limit: int = 3
) -> list[dict]:
    """
    Free slots for one doctor on one IST day ('YYYY-MM-DD'), soonest first.
    With near='HH:MM', returns only the `limit` slots closest to that time.
    Each slot has slot_id and slot_start (ISO 8601 with +05:30).
    """
    params = {"date": day}
    if near:
        params.update({"near": near, "limit": limit})
    response = requests.get(f"{_DOCTORS_URL}/{doctor_id}/slots", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def hold_slot(slot_id: str, session_id: str) -> dict:
    """
    Reserve a slot for this conversation while the patient confirms.
    Raises SlotUnavailableError if someone else holds or has booked it.
    """
    response = requests.post(
        f"{_SLOTS_URL}/{slot_id}/hold", json={"session_id": session_id}, timeout=10
    )
    if response.status_code == 409:
        raise SlotUnavailableError(slot_id)
    response.raise_for_status()
    return response.json()


def release_slot(slot_id: str, session_id: str) -> bool:
    """Give a held slot back. Safe to call more than once."""
    response = requests.post(
        f"{_SLOTS_URL}/{slot_id}/release", json={"session_id": session_id}, timeout=10
    )
    response.raise_for_status()
    return bool(response.json().get("released"))


def create_appointment(
    session_id: str,
    patient_uhid: str,
    doctor_name: str | None = None,
    appointment_datetime: str | None = None,
    status: str = "pending",
    booking_info: dict | None = None,
    slot_id: str | None = None,
) -> dict:
    """
    Create an appointment against Team C's ai_appointments table.

    Preferred: pass slot_id (a slot this session is holding). The doctor and
    time then come from the slot, and the slot is locked for everyone else.
    Raises SlotUnavailableError if the slot is no longer ours.

    Without slot_id, doctor_name and appointment_datetime are required
    (ISO 8601, e.g. '2026-08-28T10:30:00+05:30') and nothing is locked.

    If the appointment was saved but Team C could not send the WhatsApp
    confirmation (it answers 502 "Appointment saved, ..."), the booking is
    still real, so this returns {"notification_failed": True} instead of raising.
    """
    payload = {
        "session_id": session_id,
        "patient_uhid": patient_uhid,
        "status": status,
        "booking_info": booking_info,
    }
    if slot_id:
        payload["slot_id"] = slot_id
    else:
        payload["doctor_name"] = doctor_name
        payload["appointment_datetime"] = appointment_datetime
    response = requests.post(_APPOINTMENTS_URL, json=payload, timeout=10)
    if response.status_code == 409:
        raise SlotUnavailableError(slot_id or "")
    if response.status_code == 502 and "Appointment saved" in response.text:
        return {"notification_failed": True}
    response.raise_for_status()
    return response.json()


def cancel_appointment(appointment_id: str, session_id: str) -> dict:
    """Cancel an appointment made by this session and free its slot."""
    response = requests.post(
        f"{_APPOINTMENTS_URL}/{appointment_id}/cancel",
        json={"session_id": session_id},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# the patient's own appointments: list, cancel, reschedule
# ---------------------------------------------------------------------------

class NotLoggedIn(Exception):
    """Team C does not know this patient (the web login was skipped or failed)."""


class ChangeRefused(Exception):
    """Team C refused to cancel/move the appointment. ``code`` says why
    (already_started, has_consultation, cancelled, not_reschedulable, ...)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def list_my_appointments(auth_id: str) -> list[dict]:
    """
    The patient's upcoming, non-cancelled appointments, soonest first. Each has
    appointment_id, doctor_id, doctor_name, hospital_name, appointment_datetime
    (IST), appointment_type, can_change and change_blocker.
    Raises NotLoggedIn if Team C has no such patient.
    """
    response = requests.get(f"{TEAM_C_BASE_URL}/api/v1/patients/{auth_id}/appointments", timeout=10)
    if response.status_code == 404:
        raise NotLoggedIn(auth_id)
    response.raise_for_status()
    return response.json()


def cancel_my_appointment(appointment_id: str, auth_id: str, reason: str = "patient_request") -> dict:
    """Cancel an appointment as the patient. Raises ChangeRefused if it cannot be cancelled."""
    response = requests.post(
        f"{_APPOINTMENTS_URL}/{appointment_id}/cancel",
        json={"auth_id": auth_id, "reason": reason},
        timeout=10,
    )
    if response.status_code == 409:
        raise ChangeRefused(_detail(response))
    response.raise_for_status()
    return response.json()


def reschedule_my_appointment(appointment_id: str, slot_id: str, auth_id: str, session_id: str | None = None) -> dict:
    """
    Move an appointment to another slot of the same doctor, as the patient. Returns
    the new appointment. Raises SlotUnavailableError if the slot was taken and
    ChangeRefused if the appointment cannot be moved.
    """
    body = {"slot_id": slot_id, "auth_id": auth_id}
    if session_id:
        body["session_id"] = session_id
    response = requests.post(f"{_APPOINTMENTS_URL}/{appointment_id}/reschedule", json=body, timeout=10)
    if response.status_code == 409:
        code = _detail(response)
        if code == "slot_unavailable":
            raise SlotUnavailableError(slot_id)
        raise ChangeRefused(code)
    response.raise_for_status()
    return response.json()


def _detail(response) -> str:
    try:
        return str(response.json().get("detail", "refused"))
    except ValueError:
        return "refused"
