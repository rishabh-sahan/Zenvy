"""
Appointment booking state machine (roadmap Days 31-32).

States: ASK_DOCTOR -> ASK_DATE -> ASK_TIME -> CONFIRM -> COMPLETED
(a session with no active booking has state = None, and normal Q&A
via services/llm/client.py is used instead of this module).

State is kept in-memory, per session_id, in this process. The roadmap
specifies Redis for this (so state survives restarts and is shared
across multiple gateway workers) -- that swap is straightforward
later: replace _SESSION_STATE's get/set with Redis get/set calls,
nothing else here needs to change.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.llm.client import generate_reply
from services.conversation_client import create_appointment
from services.orchestrator.entity_extraction import extract_booking_fields
from services.orchestrator.templates import render_template

# session_id -> {"state": str, "slots": {"doctor_name": str|None, "appointment_date": str|None, "appointment_time": str|None}}
_SESSION_STATE: dict[str, dict] = {}

SLOT_ORDER = ["doctor_name", "appointment_date", "appointment_time"]
SLOT_TO_ASK_STATE = {
    "doctor_name": "ASK_DOCTOR",
    "appointment_date": "ASK_DATE",
    "appointment_time": "ASK_TIME",
}

# Placeholder patient identifier until real patient registration/login exists.
# TODO: replace with a real UHID once auth/registration is built.
PLACEHOLDER_PATIENT_UHID = "UHID-DEMO-0001"


def _first_missing_slot(slots: dict) -> str | None:
    for slot_name in SLOT_ORDER:
        if not slots.get(slot_name):
            return slot_name
    return None


def handle_turn(session_id: str, short_lang: str, user_text: str) -> str:
    """
    Route one turn either through the booking state machine or through
    normal free-form Q&A, depending on whether this session has an
    active booking in progress (or is starting one).

    Returns the reply text to speak back to the patient.
    """
    existing = _SESSION_STATE.get(session_id)

    extracted = extract_booking_fields(user_text)

    # No active booking, and this message isn't trying to start one ->
    # ordinary hospital-receptionist Q&A, untouched by the state machine.
    if existing is None and not extracted["wants_to_book"]:
        return generate_reply(user_text, short_lang)

    # Starting a fresh booking this turn.
    if existing is None:
        slots = {
            "doctor_name": extracted["doctor_name"],
            "appointment_date": extracted["appointment_date"],
            "appointment_time": extracted["appointment_time"],
        }
        missing = _first_missing_slot(slots)
        if missing is None:
            state = "CONFIRM"
        else:
            state = SLOT_TO_ASK_STATE[missing]
        _SESSION_STATE[session_id] = {"state": state, "slots": slots}
        return _render_current_state(session_id, short_lang)

    # Continuing an existing booking in progress.
    state = existing["state"]
    slots = existing["slots"]

    if state == "CONFIRM":
        if extracted["confirms_booking"] is True:
            return _complete_booking(session_id, short_lang)
        elif extracted["confirms_booking"] is False:
            del _SESSION_STATE[session_id]
            return render_template("CANCELLED", short_lang)
        else:
            # Unclear response -- re-ask the confirmation rather than guessing.
            return _render_current_state(session_id, short_lang)

    # state is one of ASK_DOCTOR / ASK_DATE / ASK_TIME -- merge any newly
    # extracted slot values (a patient might give more than one at once,
    # e.g. "dentist tomorrow at 10:30") and move to whichever slot is
    # still missing, or to CONFIRM once all three are filled.
    for slot_name in SLOT_ORDER:
        if extracted.get(slot_name):
            slots[slot_name] = extracted[slot_name]

    missing = _first_missing_slot(slots)
    existing["state"] = "CONFIRM" if missing is None else SLOT_TO_ASK_STATE[missing]
    return _render_current_state(session_id, short_lang)


def _render_current_state(session_id: str, short_lang: str) -> str:
    entry = _SESSION_STATE[session_id]
    state = entry["state"]
    slots = entry["slots"]

    if state == "CONFIRM":
        return render_template(
            "CONFIRM",
            short_lang,
            doctor=slots["doctor_name"],
            date=slots["appointment_date"],
            time=slots["appointment_time"],
        )
    return render_template(state, short_lang)


def _complete_booking(session_id: str, short_lang: str) -> str:
    slots = _SESSION_STATE[session_id]["slots"]

    appointment_datetime = f"{slots['appointment_date']}T{slots['appointment_time']}:00"

    try:
        create_appointment(
            session_id=session_id,
            patient_uhid=PLACEHOLDER_PATIENT_UHID,
            doctor_name=slots["doctor_name"],
            appointment_datetime=appointment_datetime,
            status="confirmed",
        )
    except Exception as e:
        print(f"[Orchestrator] Appointment creation failed: {e}")
        del _SESSION_STATE[session_id]
        return render_template("BOOKING_FAILED", short_lang)

    reply = render_template(
        "CONFIRMED",
        short_lang,
        doctor=slots["doctor_name"],
        date=slots["appointment_date"],
        time=slots["appointment_time"],
    )
    del _SESSION_STATE[session_id]  # booking complete, session free for new requests
    return reply