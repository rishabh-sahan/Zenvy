"""
Appointment booking state machine.

States:
ASK_DOCTOR -> ASK_DATE -> ASK_TIME -> CONFIRM -> COMPLETED

Appointment conversation state is stored in Redis per session_id so that
it survives application/server restarts and can be shared across
multiple gateway workers.

Slot locking
------------
Once doctor, date and time are known, the doctor is looked up in Team C's
doctors table and the exact time slot is HELD for this conversation
(CONFIRM state). Nobody else is offered that slot while it is held. Saying
"yes" books it; saying "no" releases it. If the time is already taken the
patient is offered the nearest free times instead. See DB-Team-C
app/services/slot_service.py for how the lock itself works.
"""

import json
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.config import REDIS_URL
from services.llm.client import generate_reply
from services.conversation_client import (
    SlotUnavailableError,
    create_appointment,
    find_doctors,
    get_free_slots,
    hold_slot,
    release_slot,
)
from services.orchestrator.entity_extraction import extract_booking_fields
from services.orchestrator.templates import render_template


# Redis connection
redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

# Keep an unfinished appointment conversation for 1 hour.
SESSION_TTL = 3600

# The hospital runs on IST. Appointment times a patient gives are in IST.
IST = timezone(timedelta(hours=5, minutes=30))

# Patients say "Mysuru"/"Bengaluru"; the database stores "mysore"/"bangalore".
# Maps a stored city to every spelling that should match it, and the spelling
# we read back to the patient.
CITY_SPELLINGS = {
    "mysore": ("mysore", "mysuru"),
    "bangalore": ("bangalore", "bengaluru", "bengalooru"),
}
CITY_DISPLAY = {"mysore": "Mysuru", "bangalore": "Bengaluru"}

# How many alternative times / doctors to read out to the patient.
MAX_ALTERNATIVES = 3
MAX_DOCTOR_OPTIONS = 5


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


# Plain answers to "Shall I book this?". The language model sometimes returns
# nothing for a bare "Yes", so the confirmation step also understands these.
_YES_WORDS = {
    "yes", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm", "confirmed",
    "correct", "right", "proceed", "please", "fine",
    "haan", "han", "haa", "ji", "theek", "thik", "sahi", "houdu", "howdu", "sari",
    "हाँ", "हां", "जी", "ठीक", "सही", "बुक",
    "ಹೌದು", "ಸರಿ", "ಆಯಿತು", "ಓಕೆ",
}
_YES_PHRASES = ("go ahead", "book it", "do it", "that's fine", "that works")
_NO_WORDS = {
    "no", "nope", "nah", "cancel", "stop", "dont", "don't", "nahi", "nahin", "mat",
    "नहीं", "नही", "मत", "रद्द",
    "ಇಲ್ಲ", "ಬೇಡ",
}


def _plain_yes_no(text: str) -> bool | None:
    """True for a plain yes, False for a plain no, None if unclear or mixed."""
    # Keep letters, digits AND combining marks: Hindi/Kannada vowel signs are
    # marks, and dropping them would cut words like "हाँ" into pieces.
    cleaned = "".join(
        ch if (unicodedata.category(ch)[0] in "LMN" or ch in "' ") else " "
        for ch in (text or "").lower()
    )
    words = set(cleaned.split())
    said_yes = bool(words & _YES_WORDS) or any(phrase in cleaned for phrase in _YES_PHRASES)
    said_no = bool(words & _NO_WORDS)
    if said_yes == said_no:
        return None
    return said_yes


def _normalize_time(value: str) -> str:
    """'9:30', '09:30' or '09:30:00' -> '09:30' (how Team C's slots are compared)."""
    hours, _, rest = str(value).strip().partition(":")
    return f"{int(hours):02d}:{rest[:2]}"


def _hhmm(slot: dict) -> str:
    """Local (IST) HH:MM of a slot returned by Team C."""
    return datetime.fromisoformat(slot["slot_start"]).astimezone(IST).strftime("%H:%M")


def _doctor_summary(doctor: dict) -> dict:
    """The few doctor fields we keep in Redis."""
    return {
        "doctor_id": doctor["doctor_id"],
        "name": doctor["name"],
        "specialty": doctor.get("specialty", ""),
        "hospital_name": doctor.get("hospital_name", ""),
        "city": doctor.get("city", ""),
    }


def _doctor_option(number: int, doctor: dict) -> str:
    return (
        f"{number}) {doctor['name']} "
        f"({doctor['specialty']}, {doctor['hospital_name']}, "
        f"{CITY_DISPLAY.get(doctor['city'], doctor['city'].title())})"
    )


def _pick_candidate(
    user_text: str, candidates: list[dict], allow_number: bool = True
) -> dict | None:
    """
    Which of the doctors we just listed did the patient choose?

    They may name the hospital or city, or give the number ("2", "second",
    "number two"). Returns the doctor only if the answer points at exactly one.

    allow_number=False is for a sentence that was NOT an answer to our list
    ("book Dr Priya Sharma in Bengaluru"): there only a hospital or city counts,
    because a stray "1" or "2" in it would otherwise pick a doctor by accident.
    """
    text = (user_text or "").lower().strip()
    if not text:
        return None

    # 1. Hospital or city: "the one at Metro Health Hospital".
    def mentions_place(c: dict) -> bool:
        if c["hospital_name"] and c["hospital_name"].lower() in text:
            return True
        spellings = CITY_SPELLINGS.get(c["city"], (c["city"],)) if c["city"] else ()
        return any(name.lower() in text for name in spellings)

    by_place = [c for c in candidates if mentions_place(c)]
    if len(by_place) == 1:
        return by_place[0]

    if not allow_number:
        return None

    # 2. A number. Digits and "first".."fifth" are clear on their own. Number
    #    words ("one") are only trusted when they are the whole answer or follow
    #    "number"/"option", so "the one at ..." is not mistaken for "1".
    words = text.replace(")", " ").replace(".", " ").replace(",", " ").split()
    clear = {
        "1": 0, "first": 0, "2": 1, "second": 1, "3": 2, "third": 2,
        "4": 3, "fourth": 3, "5": 4, "fifth": 4,
    }
    spelled = {"one": 0, "two": 1, "three": 2, "four": 3, "five": 4}

    for position, word in enumerate(words):
        index = clear.get(word)
        if index is None and word in spelled:
            alone = len(words) == 1
            after_label = position > 0 and words[position - 1] in ("number", "option", "no")
            if alone or after_label:
                index = spelled[word]
        if index is not None and index < len(candidates):
            return candidates[index]

    return None


def _alternatives_text(slots: list[dict]) -> str:
    return ", ".join(_hhmm(s) for s in slots)


def _offer_other_times(
    session_id: str,
    short_lang: str,
    entry: dict,
    nearby: list[dict] | None = None,
) -> str:
    """
    The wanted time is not available. Tell the patient and offer the closest
    free times that day, or ask for another date if the day is full.

    Clears whichever slot the patient has to answer again (time, or date).
    """
    slots = entry["slots"]
    doctor = entry["doctor"]

    if nearby is None:
        nearby = get_free_slots(
            doctor["doctor_id"],
            slots["appointment_date"],
            near=_normalize_time(slots["appointment_time"]),
            limit=MAX_ALTERNATIVES,
        )

    # Any slot we were holding is no longer what the patient wants.
    held = entry.pop("slot", None)
    if held:
        try:
            release_slot(held["slot_id"], session_id)
        except Exception as e:
            print(f"[Orchestrator] Could not release slot: {e}")

    if not nearby:
        reply = render_template(
            "SLOT_DAY_FULL",
            short_lang,
            doctor=doctor["name"],
            date=slots["appointment_date"],
        )
        slots["appointment_date"] = None
        slots["appointment_time"] = None
        entry["state"] = "ASK_DATE"
    else:
        reply = render_template(
            "SLOT_TAKEN",
            short_lang,
            doctor=doctor["name"],
            date=slots["appointment_date"],
            time=slots["appointment_time"],
            alternatives=_alternatives_text(nearby),
        )
        slots["appointment_time"] = None
        entry["state"] = "ASK_TIME"

    _set_session_state(session_id, entry)
    return reply


def _resolve_doctor(
    session_id: str, short_lang: str, entry: dict, user_text: str = ""
) -> str | None:
    """
    Turn the doctor name or department the patient said into a real doctor,
    as soon as they say it (not after the date and time).

    Returns None when the doctor is settled (stored in entry["doctor"]; the
    caller saves the state). Returns a reply when the conversation has to stop
    and ask: no such doctor, several matches, or Team C unreachable.
    """
    slots = entry["slots"]

    if entry.get("doctor") or entry.get("candidates") or not slots.get("doctor_name"):
        return None

    try:
        matches = find_doctors(slots["doctor_name"])
    except Exception as e:
        print(f"[Orchestrator] Could not look up doctors: {e}")
        _delete_session_state(session_id)
        return render_template("BOOKING_FAILED", short_lang)

    print(f"[Orchestrator] Doctor matches for {slots['doctor_name']!r}: {len(matches)}")

    if not matches:
        slots["doctor_name"] = None
        entry["state"] = "ASK_DOCTOR"
        _set_session_state(session_id, entry)
        return render_template("DOCTOR_NOT_FOUND", short_lang)

    if len(matches) > 1:
        shown = [_doctor_summary(d) for d in matches[:MAX_DOCTOR_OPTIONS]]

        # "Dr Priya Sharma in Bengaluru": the place was said in the same breath.
        said_place = _pick_candidate(user_text, shown, allow_number=False)
        if said_place is not None:
            entry["doctor"] = said_place
            slots["doctor_name"] = said_place["name"]
            return None

        entry["candidates"] = shown
        entry["state"] = "ASK_DOCTOR"
        _set_session_state(session_id, entry)
        return _ask_which_doctor(short_lang, entry["candidates"])

    entry["doctor"] = _doctor_summary(matches[0])
    return None


def _ask_which_doctor(short_lang: str, candidates: list[dict]) -> str:
    return render_template(
        "DOCTOR_AMBIGUOUS",
        short_lang,
        options="; ".join(_doctor_option(i + 1, d) for i, d in enumerate(candidates)),
    )


def _resolve_and_confirm(session_id: str, short_lang: str, entry: dict) -> str:
    """
    Doctor, date and time are all filled in. Find the real doctor, hold the
    slot, and ask the patient to confirm -- or ask again if something is wrong.
    """
    slots = entry["slots"]

    try:
        # 1. Which doctor? (Normally settled earlier, when it was first said.)
        stop = _resolve_doctor(session_id, short_lang, entry)
        if stop is not None:
            return stop

        doctor = entry["doctor"]

        # 2. Is that exact time free?
        nearby = get_free_slots(
            doctor["doctor_id"],
            slots["appointment_date"],
            near=_normalize_time(slots["appointment_time"]),
            limit=MAX_ALTERNATIVES,
        )
        wanted = next(
            (s for s in nearby if _hhmm(s) == _normalize_time(slots["appointment_time"])),
            None,
        )

        if wanted is None:
            return _offer_other_times(session_id, short_lang, entry, nearby)

        # 3. Hold it while the patient confirms.
        try:
            hold_slot(wanted["slot_id"], session_id)
        except SlotUnavailableError:
            # Someone grabbed it a moment ago: look again.
            print("[Orchestrator] Slot was taken while holding")
            return _offer_other_times(session_id, short_lang, entry)

        entry["slot"] = {"slot_id": wanted["slot_id"], "slot_start": wanted["slot_start"]}
        entry["state"] = "CONFIRM"
        _set_session_state(session_id, entry)

        return _render_current_state(session_id, short_lang)

    except Exception as e:
        print(f"[Orchestrator] Could not check availability: {e}")
        _delete_session_state(session_id)
        return render_template("BOOKING_FAILED", short_lang)


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

        entry = {"state": "ASK_DOCTOR", "slots": slots}

        # A doctor named in the very first sentence is looked up right away.
        stop = _resolve_doctor(session_id, short_lang, entry, user_text)
        if stop is not None:
            return stop

        missing = _first_missing_slot(slots)

        if missing is None:
            # Everything given in one go: hold the slot.
            return _resolve_and_confirm(session_id, short_lang, entry)

        entry["state"] = SLOT_TO_ASK_STATE[missing]

        print(f"[Orchestrator] New state: {entry['state']}")

        # Save the new booking state in Redis.
        _set_session_state(session_id, entry)

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

        answer = extracted["confirms_booking"]

        if answer is None:
            # The language model gave no clear answer: accept a plain yes/no.
            answer = _plain_yes_no(user_text)

        if answer is True:

            print("[Orchestrator] Booking confirmed")

            return _complete_booking(
                session_id,
                short_lang,
            )

        elif answer is False:

            print("[Orchestrator] Booking cancelled")

            # Give the held slot back to other patients.
            held = existing.get("slot")
            if held:
                try:
                    release_slot(held["slot_id"], session_id)
                except Exception as e:
                    print(f"[Orchestrator] Could not release slot: {e}")

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

    # We listed several matching doctors: see which one they picked.
    candidates = existing.get("candidates")
    still_choosing = False

    if candidates:

        picked = _pick_candidate(user_text, candidates)

        if picked is not None:
            existing["doctor"] = picked
            slots["doctor_name"] = picked["name"]
            existing.pop("candidates", None)

        elif extracted.get("doctor_name") and extracted["doctor_name"] != slots.get("doctor_name"):
            # They named somebody else: search again from scratch.
            existing.pop("candidates", None)
            existing.pop("doctor", None)

        else:
            # Still unclear. Keep anything else they said (a date, a time)
            # and ask about the doctor again below.
            still_choosing = True

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

            # A different doctor name means the old match no longer applies.
            if slot_name == "doctor_name" and extracted[slot_name] != slots.get(slot_name):
                existing.pop("doctor", None)
                existing.pop("candidates", None)

            slots[slot_name] = extracted[slot_name]

    print(f"[Orchestrator] Updated slots: {slots}")

    if still_choosing:
        _set_session_state(session_id, existing)
        return _ask_which_doctor(short_lang, candidates)

    # A doctor named now (or changed) is looked up right away.
    stop = _resolve_doctor(session_id, short_lang, existing, user_text)
    if stop is not None:
        return stop

    # Determine what is still missing.
    missing = _first_missing_slot(slots)

    if missing is None:

        # Everything collected: check the doctor and hold the slot.
        return _resolve_and_confirm(
            session_id,
            short_lang,
            existing,
        )

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

        doctor = entry.get("doctor")

        return render_template(
            "CONFIRM",
            short_lang,
            doctor=doctor["name"] if doctor else slots["doctor_name"],
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
    Book the held slot in Team C and clear the temporary Redis
    conversation state.
    """

    entry = _get_session_state(session_id)

    if entry is None:

        return render_template(
            "BOOKING_FAILED",
            short_lang,
        )

    slots = entry["slots"]
    doctor = entry.get("doctor")
    held = entry.get("slot")

    if doctor is None or held is None:

        # We never held a slot for this conversation; start over.
        _delete_session_state(session_id)

        return render_template(
            "BOOKING_FAILED",
            short_lang,
        )

    print(f"[Orchestrator] Booking slot {held['slot_id']}")

    try:

        # The hold may have run out while the patient thought about it.
        # Holding again succeeds if nobody else took the slot meanwhile.
        hold_slot(held["slot_id"], session_id)

        # The doctor and time come from the slot itself; Team C locks it.
        create_appointment(
            session_id=session_id,
            patient_uhid=PLACEHOLDER_PATIENT_UHID,
            slot_id=held["slot_id"],
            status="confirmed",
        )

    except SlotUnavailableError:

        # Taken by someone else in the meantime: offer other times.
        print("[Orchestrator] Slot no longer available at confirmation")

        try:
            return _offer_other_times(session_id, short_lang, entry)
        except Exception as e:
            print(f"[Orchestrator] Could not offer other times: {e}")
            _delete_session_state(session_id)
            return render_template(
                "BOOKING_FAILED",
                short_lang,
            )

    except Exception as e:

        print(
            f"[Orchestrator] Appointment creation failed: {e}"
        )

        # Don't hold the slot for a booking that failed.
        try:
            release_slot(held["slot_id"], session_id)
        except Exception:
            pass

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
        doctor=doctor["name"],
        date=slots["appointment_date"],
        time=slots["appointment_time"],
    )

    # Booking is finished, so remove temporary Redis state.
    _delete_session_state(session_id)

    print("[Orchestrator] Appointment successfully created")

    return reply
