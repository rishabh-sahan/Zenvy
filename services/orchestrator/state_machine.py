"""
Appointment booking state machine.

States:
ASK_DOCTOR -> ASK_DATE -> ASK_TIME -> CONFIRM -> COMPLETED

Appointment conversation state is stored in Redis per session_id so that
it survives application/server restarts and can be shared across
multiple gateway workers.
"""

import json
import sys
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.config import REDIS_URL
from services.llm.client import generate_reply
from services.conversation_client import create_appointment
from services.orchestrator.entity_extraction import extract_booking_fields
from services.orchestrator.templates import render_template


# Redis connection
redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

# Keep an unfinished appointment conversation for 1 hour.
SESSION_TTL = 3600


SLOT_ORDER = [
    "doctor_name",
    "appointment_date",
    "appointment_time",
]

SLOT_TO_ASK_STATE = {
    "doctor_name": "ASK_DOCTOR",
    "appointment_date": "ASK_DATE",
    "appointment_time": "ASK_TIME",
}


# Placeholder patient identifier until real patient registration/login exists.
# TODO: replace with a real UHID once auth/registration is built.
PLACEHOLDER_PATIENT_UHID = "UHID-DEMO-0001"


def _session_key(session_id: str) -> str:
    """
    Generate the Redis key for an appointment conversation.
    """
    return f"appointment_session:{session_id}"


def _get_session_state(session_id: str) -> dict | None:
    """
    Get appointment state from Redis.

    Returns None if there is no active appointment conversation.
    """
    data = redis_client.get(_session_key(session_id))

    if data is None:
        return None

    return json.loads(data)


def _set_session_state(session_id: str, state: dict) -> None:
    """
    Save appointment state to Redis.

    The state automatically expires after SESSION_TTL seconds.
    """
    redis_client.setex(
        _session_key(session_id),
        SESSION_TTL,
        json.dumps(state),
    )


def _delete_session_state(session_id: str) -> None:
    """
    Delete appointment state from Redis.
    """
    redis_client.delete(_session_key(session_id))


def _first_missing_slot(slots: dict) -> str | None:
    """
    Return the first appointment field that is still missing.
    """
    for slot_name in SLOT_ORDER:
        if not slots.get(slot_name):
            return slot_name

    return None


def handle_turn(session_id: str, short_lang: str, user_text: str) -> str:
    """
    Route one conversation turn through the appointment state machine
    or normal hospital-receptionist Q&A.

    Appointment state is persisted in Redis using session_id.
    """

    # Get the current appointment state from Redis.
    existing = _get_session_state(session_id)

    # Extract appointment information from the current message.
    extracted = extract_booking_fields(user_text)

    # Debug logging
    print(f"[Orchestrator] INPUT: {user_text}")
    print(f"[Orchestrator] EXTRACTED: {extracted}")

    # ---------------------------------------------------------
    # Normal hospital Q&A
    # ---------------------------------------------------------

    # If there is no active booking and the user isn't trying
    # to book anything, send the question to the normal LLM.
    if existing is None and not extracted["wants_to_book"]:
        print("[Orchestrator] Routing to normal LLM")
        return generate_reply(user_text, short_lang)

    # ---------------------------------------------------------
    # Start a new appointment booking
    # ---------------------------------------------------------

    if existing is None:

        slots = {
            "doctor_name": extracted["doctor_name"],
            "appointment_date": extracted["appointment_date"],
            "appointment_time": extracted["appointment_time"],
        }

        print(f"[Orchestrator] New booking slots: {slots}")

        missing = _first_missing_slot(slots)

        if missing is None:
            state = "CONFIRM"
        else:
            state = SLOT_TO_ASK_STATE[missing]

        print(f"[Orchestrator] New state: {state}")

        # Save the new booking state in Redis.
        _set_session_state(
            session_id,
            {
                "state": state,
                "slots": slots,
            },
        )

        return _render_current_state(
            session_id,
            short_lang,
        )

    # ---------------------------------------------------------
    # Continue existing appointment booking
    # ---------------------------------------------------------

    state = existing["state"]
    slots = existing["slots"]

    print(f"[Orchestrator] Existing state: {state}")
    print(f"[Orchestrator] Existing slots: {slots}")

    # ---------------------------------------------------------
    # Confirmation state
    # ---------------------------------------------------------

    if state == "CONFIRM":

        if extracted["confirms_booking"] is True:

            print("[Orchestrator] Booking confirmed")

            return _complete_booking(
                session_id,
                short_lang,
            )

        elif extracted["confirms_booking"] is False:

            print("[Orchestrator] Booking cancelled")

            _delete_session_state(session_id)

            return render_template(
                "CANCELLED",
                short_lang,
            )

        else:

            # User's answer wasn't clearly yes/no.
            return _render_current_state(
                session_id,
                short_lang,
            )

    # ---------------------------------------------------------
    # ASK_DOCTOR / ASK_DATE / ASK_TIME
    # ---------------------------------------------------------

    # Merge newly extracted information into existing slots.
    #
    # Example:
    # User previously gave doctor name.
    # Next turn:
    # "Tomorrow at 10:30"
    #
    # Redis keeps the doctor name and we add date/time.

    for slot_name in SLOT_ORDER:

        if extracted.get(slot_name):
            slots[slot_name] = extracted[slot_name]

    print(f"[Orchestrator] Updated slots: {slots}")

    # Determine what is still missing.
    missing = _first_missing_slot(slots)

    if missing is None:

        existing["state"] = "CONFIRM"

    else:

        existing["state"] = SLOT_TO_ASK_STATE[missing]

    print(
        f"[Orchestrator] Updated state: "
        f"{existing['state']}"
    )

    # Save updated slots/state back to Redis.
    _set_session_state(
        session_id,
        existing,
    )

    return _render_current_state(
        session_id,
        short_lang,
    )


def _render_current_state(
    session_id: str,
    short_lang: str,
) -> str:
    """
    Read the current state from Redis and generate the appropriate reply.
    """

    entry = _get_session_state(session_id)

    if entry is None:

        return render_template(
            "BOOKING_FAILED",
            short_lang,
        )

    state = entry["state"]
    slots = entry["slots"]

    print(f"[Orchestrator] Rendering state: {state}")
    print(f"[Orchestrator] Rendering slots: {slots}")

    # All appointment information has been collected.
    if state == "CONFIRM":

        return render_template(
            "CONFIRM",
            short_lang,
            doctor=slots["doctor_name"],
            date=slots["appointment_date"],
            time=slots["appointment_time"],
        )

    # Still collecting information.
    return render_template(
        state,
        short_lang,
    )


def _complete_booking(
    session_id: str,
    short_lang: str,
) -> str:
    """
    Create the appointment in Supabase and clear the temporary
    Redis conversation state.
    """

    entry = _get_session_state(session_id)

    if entry is None:

        return render_template(
            "BOOKING_FAILED",
            short_lang,
        )

    slots = entry["slots"]

    appointment_datetime = (
        f"{slots['appointment_date']}"
        f"T{slots['appointment_time']}:00"
    )

    print(
        f"[Orchestrator] Creating appointment: "
        f"{appointment_datetime}"
    )

    try:

        create_appointment(
            session_id=session_id,
            patient_uhid=PLACEHOLDER_PATIENT_UHID,
            doctor_name=slots["doctor_name"],
            appointment_datetime=appointment_datetime,
            status="confirmed",
        )

    except Exception as e:

        print(
            f"[Orchestrator] Appointment creation failed: {e}"
        )

        # Don't leave a broken appointment session around.
        _delete_session_state(session_id)

        return render_template(
            "BOOKING_FAILED",
            short_lang,
        )

    # Appointment was successfully created.
    reply = render_template(
        "CONFIRMED",
        short_lang,
        doctor=slots["doctor_name"],
        date=slots["appointment_date"],
        time=slots["appointment_time"],
    )

    # Booking is finished, so remove temporary Redis state.
    _delete_session_state(session_id)

    print("[Orchestrator] Appointment successfully created")

    return reply