"""
Appointment booking state machine.

States:
ASK_DOCTOR -> ASK_DATE -> ASK_TIME -> CONFIRM -> COMPLETED

Cancel and reschedule (the patient's own appointments) share this machinery:

    cancel:      CANCEL_PICK -> CANCEL_CONFIRM -> done
    reschedule:  RESCHED_PICK -> ASK_DATE -> ASK_TIME -> CONFIRM -> done
                 (the second half is the booking flow, with the doctor fixed)
    either:      CHOOSE_ACTION first, if it is not clear which one was meant

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
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from services.config import REDIS_URL
from services.llm.client import generate_reply
from services.conversation_client import (
    ChangeRefused,
    NotLoggedIn,
    SlotUnavailableError,
    cancel_my_appointment,
    create_appointment,
    find_doctors,
    get_free_slots,
    get_session,
    hold_slot,
    list_my_appointments,
    release_slot,
    reschedule_my_appointment,
)
from services.orchestrator.entity_extraction import extract_booking_fields, hospital_today
from services.orchestrator.intent_rules import detect_change_intent, wants_to_stop
from services.orchestrator.slot_parsing import parse_date, parse_time
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
    "correct", "right", "proceed", "fine",
    "haan", "han", "haa", "ji", "theek", "thik", "sahi", "houdu", "howdu", "sari",
    "हाँ", "हां", "जी", "ठीक", "सही", "बुक",
    "ಹೌದು", "ಸರಿ", "ಆಯಿತು", "ಓಕೆ",
}
_YES_PHRASES = ("go ahead", "book it", "do it", "that's fine", "that works")
_CHANGE_WORDS = {"but", "instead", "change", "rather", "different", "another", "later", "earlier", "except"}
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
    # A time, a date or "but / instead / change" means they are changing the
    # booking, not agreeing to it: "make it 11 am please" is not a yes.
    if any(ch.isdigit() for ch in cleaned) or words & _CHANGE_WORDS:
        return None
    said_yes = bool(words & _YES_WORDS) or any(phrase in cleaned for phrase in _YES_PHRASES)
    said_no = bool(words & _NO_WORDS)
    if said_yes == said_no:
        return None
    return said_yes


def _normalize_time(value: str) -> str:
    """'9:30', '09:30' or '09:30:00' -> '09:30' (how Team C's slots are compared)."""
    hours, _, rest = str(value).strip().partition(":")
    return f"{int(hours):02d}:{rest[:2]}"


def _rescue_slots(extracted: dict, user_text: str, state: str | None) -> None:
    """Fill in a time or date the language model missed, by reading the words.

    The model is unreliable on short answers: "11 am" can come back empty one
    minute and as 11:00 the next, which left patients stuck in "what time?".
    Whatever the model did find is kept; rules only fill what is missing. A bare
    number ("11") only counts as a time right after we asked for the time.
    """
    if state == "CONFIRM":
        return

    if not extracted.get("appointment_time"):
        found = parse_time(user_text, bare_ok=(state == "ASK_TIME"))
        if found:
            print(f"[Orchestrator] Time read from the words: {found}")
            extracted["appointment_time"] = found

    if not extracted.get("appointment_date"):
        found = parse_date(user_text, hospital_today())
        if found:
            print(f"[Orchestrator] Date read from the words: {found}")
            extracted["appointment_date"] = found


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

    # People say "Speciality", the hospital may be written "Specialty".
    def same_spelling(value: str) -> str:
        return value.lower().replace("speciality", "specialty")

    text = same_spelling(text)

    # 1. Hospital or city: "the one at Metro Health Hospital".
    def mentions_place(c: dict) -> bool:
        if c["hospital_name"] and same_spelling(c["hospital_name"]) in text:
            return True
        spellings = CITY_SPELLINGS.get(c["city"], (c["city"],)) if c["city"] else ()
        return any(name.lower() in text for name in spellings)

    # 0. The doctor's own name: "Dr. Suresh Reddy, pediatrician". The language model
    #    sometimes keeps only the department ("Paediatrics") and drops the name, which
    #    lists every pediatrician; the name in the sentence still settles it.
    said = set(re.findall(r"[a-z]+", text))

    def named(c: dict) -> bool:
        parts = [w for w in re.findall(r"[a-z]+", c["name"].lower()) if w != "dr"]
        return bool(parts) and all(w in said for w in parts)

    by_name = [c for c in candidates if named(c)]
    if len(by_name) == 1:
        return by_name[0]
    pool = by_name or candidates      # two hospitals with the same name: the place decides

    by_place = [c for c in pool if mentions_place(c)]
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


# ---------------------------------------------------------------------------
# Cancel and reschedule the patient's own appointments
# ---------------------------------------------------------------------------

CHANGE_STATES = {"CHOOSE_ACTION", "CANCEL_PICK", "CANCEL_CONFIRM", "RESCHED_PICK"}


def _when(appointment: dict) -> str:
    """"Sat 10 Oct at 11:00" (hospital time) for an appointment from Team C."""
    local = datetime.fromisoformat(appointment["appointment_datetime"]).astimezone(IST)
    return f"{local.strftime('%a %d %b')} at {local.strftime('%H:%M')}"


def _appointment_options(items: list[dict]) -> str:
    return "; ".join(
        f"{i + 1}) {item['doctor_name']} on {_when(item)}" for i, item in enumerate(items)
    )


def _patient_id(session_id: str) -> str | None:
    """Who is chatting: the conversation's user (their auth_id once logged in)."""
    try:
        return get_session(session_id).get("user_id")
    except Exception as e:
        print(f"[Orchestrator] Could not read the session: {e}")
        return None


def _pick_appointment(user_text: str, items: list[dict]) -> dict | None:
    """Which appointment did the patient mean? A number, "the Friday one", a doctor, a time."""
    text = (user_text or "").lower().strip()
    if not text or not items:
        return None
    if len(items) == 1:
        return items[0]

    ordinals = {"1": 0, "first": 0, "2": 1, "second": 1, "3": 2, "third": 2, "4": 3, "fourth": 3, "5": 4, "fifth": 4}
    spelled = {"one": 0, "two": 1, "three": 2, "four": 3, "five": 4}
    words = text.replace(")", " ").replace(".", " ").replace(",", " ").split()
    for position, word in enumerate(words):
        index = ordinals.get(word)
        if index is None and word in spelled and (len(words) == 1 or (position and words[position - 1] in ("number", "option", "no"))):
            index = spelled[word]
        if word == "last":
            index = len(items) - 1
        if index is not None and index < len(items):
            return items[index]

    wanted_date = parse_date(user_text, hospital_today())
    wanted_time = parse_time(user_text)
    matches = []
    for item in items:
        local = datetime.fromisoformat(item["appointment_datetime"]).astimezone(IST)
        if wanted_date and local.date().isoformat() != wanted_date:
            continue
        if wanted_time and local.strftime("%H:%M") != wanted_time:
            continue
        if not wanted_date and not wanted_time:
            # "the one with Arjun" / "Rao": any word of the doctor's name
            name_words = [w for w in item["doctor_name"].lower().replace("dr.", " ").split() if len(w) >= 3]
            spoken = set(text.replace(",", " ").replace(".", " ").split())
            if not any(w in spoken for w in name_words):
                continue
        matches.append(item)
    return matches[0] if len(matches) == 1 else None


def _load_changeable(session_id: str, short_lang: str, want: str):
    """(auth_id, appointments the patient can act on, reply). `reply` is set when we must stop."""
    auth_id = _patient_id(session_id)
    if not auth_id:
        return None, None, render_template("CHANGE_NOT_LOGGED_IN", short_lang)
    try:
        items = list_my_appointments(auth_id)
    except NotLoggedIn:
        return None, None, render_template("CHANGE_NOT_LOGGED_IN", short_lang)
    except Exception as e:
        print(f"[Orchestrator] Could not list appointments: {e}")
        return None, None, render_template("CHANGE_FAILED", short_lang)

    if not items:
        return auth_id, None, render_template("CANCEL_NONE", short_lang)
    usable = [i for i in items if i.get("can_change") and (want == "cancel" or i.get("doctor_id"))]
    if not usable:
        return auth_id, None, render_template("CANNOT_CHANGE", short_lang)
    return auth_id, usable, None


def _start_change(session_id: str, short_lang: str, kind: str, extracted: dict) -> str:
    """The patient asked to cancel / move an appointment (or something unclear)."""
    print(f"[Orchestrator] Change request: {kind}")
    if kind == "ask":
        _set_session_state(session_id, {"flow": "choose", "state": "CHOOSE_ACTION", "slots": {}})
        return render_template("CHANGE_WHICH_ACTION", short_lang)
    return _begin_change(session_id, short_lang, kind, extracted)


def _begin_change(session_id: str, short_lang: str, kind: str, extracted: dict | None = None) -> str:
    auth_id, items, stop = _load_changeable(session_id, short_lang, kind)
    if stop is not None:
        _delete_session_state(session_id)
        return stop

    entry = {"flow": kind, "auth_id": auth_id, "choices": items, "slots": {}}
    if len(items) > 1:
        entry["state"] = "CANCEL_PICK" if kind == "cancel" else "RESCHED_PICK"
        _set_session_state(session_id, entry)
        return render_template(
            "CANCEL_WHICH" if kind == "cancel" else "RESCHED_WHICH",
            short_lang,
            options=_appointment_options(items),
        )
    return _target_chosen(session_id, short_lang, entry, items[0], extracted or {})


def _target_chosen(session_id: str, short_lang: str, entry: dict, target: dict, extracted: dict) -> str:
    entry.pop("choices", None)
    entry["target"] = {
        "appointment_id": target["appointment_id"],
        "doctor_id": target.get("doctor_id"),
        "doctor_name": target["doctor_name"],
        "hospital_name": target.get("hospital_name") or "",
        "appointment_datetime": target["appointment_datetime"],
        "when": _when(target),
    }

    if entry["flow"] == "cancel":
        entry["state"] = "CANCEL_CONFIRM"
        _set_session_state(session_id, entry)
        return render_template(
            "CANCEL_CONFIRM", short_lang, doctor=target["doctor_name"], when=entry["target"]["when"]
        )

    # Reschedule: now it is a booking with the doctor already fixed.
    entry["doctor"] = {
        "doctor_id": target["doctor_id"],
        "name": target["doctor_name"],
        "specialty": "",
        "hospital_name": entry["target"]["hospital_name"],
        "city": "",
    }
    entry["slots"] = {
        "doctor_name": target["doctor_name"],
        "appointment_date": extracted.get("appointment_date"),
        "appointment_time": extracted.get("appointment_time"),
    }
    missing = _first_missing_slot(entry["slots"])
    if missing is None:
        return _resolve_and_confirm(session_id, short_lang, entry)
    entry["state"] = SLOT_TO_ASK_STATE[missing]
    _set_session_state(session_id, entry)
    return _render_current_state(session_id, short_lang)


def _continue_change(session_id: str, short_lang: str, existing: dict, extracted: dict, user_text: str) -> str:
    """One more turn of a cancel / choose-action / pick-which-appointment conversation."""
    state = existing["state"]

    if state == "CHOOSE_ACTION":
        kind = detect_change_intent(user_text)
        if kind in ("cancel", "reschedule"):
            _delete_session_state(session_id)
            return _begin_change(session_id, short_lang, kind, extracted)
        if wants_to_stop(user_text):
            _delete_session_state(session_id)
            return render_template("RESCHED_KEPT", short_lang)
        return render_template("CHANGE_WHICH_ACTION", short_lang)

    if state in ("CANCEL_PICK", "RESCHED_PICK"):
        if wants_to_stop(user_text):
            _delete_session_state(session_id)
            return render_template("RESCHED_KEPT", short_lang)
        target = _pick_appointment(user_text, existing.get("choices") or [])
        if target is None:
            return render_template(
                "CANCEL_WHICH" if state == "CANCEL_PICK" else "RESCHED_WHICH",
                short_lang,
                options=_appointment_options(existing.get("choices") or []),
            )
        return _target_chosen(session_id, short_lang, existing, target, extracted)

    # CANCEL_CONFIRM
    target = existing["target"]
    answer = _cancel_answer(user_text)
    if answer is None:
        answer = extracted.get("confirms_booking")

    if answer is False:
        _delete_session_state(session_id)
        return render_template("CANCEL_KEPT", short_lang, doctor=target["doctor_name"], when=target["when"])
    if answer is not True:
        return render_template("CANCEL_CONFIRM", short_lang, doctor=target["doctor_name"], when=target["when"])

    try:
        cancel_my_appointment(target["appointment_id"], existing["auth_id"])
    except ChangeRefused as refused:
        print(f"[Orchestrator] Cancel refused: {refused.code}")
        _delete_session_state(session_id)
        return render_template("CANNOT_CHANGE", short_lang)
    except Exception as e:
        print(f"[Orchestrator] Cancel failed: {e}")
        _delete_session_state(session_id)
        return render_template("CHANGE_FAILED", short_lang)
    _delete_session_state(session_id)
    return render_template("CANCEL_DONE", short_lang, doctor=target["doctor_name"], when=target["when"])


def _cancel_answer(user_text: str) -> bool | None:
    """Yes/no to "Do you want to cancel...?". Here "cancel" itself means yes:
    "yes, cancel it" has both a yes word and a no word and must not read as unclear."""
    lowered = " ".join((user_text or "").lower().replace("'", "").split())
    without_cancel = re.sub(r"\bcancel\w*|रद्द|कैंसल|ರದ್ದು\w*", " ", lowered)
    plain = _plain_yes_no(without_cancel)
    if plain is not None:
        return plain
    # A date, a time or "instead" means they want something else, not a plain "cancel it".
    words = set(re.findall(r"\w+", lowered))
    changing = any(ch.isdigit() for ch in lowered) or bool(words & (_CHANGE_WORDS | {"tomorrow", "today", "reschedule", "move"}))
    if not changing and detect_change_intent(user_text) == "cancel":
        return True                      # "cancel it", "please cancel"
    if wants_to_stop(without_cancel):
        return False                     # "never mind", "stop", "leave it"
    return None


def _abort_flow(session_id: str, short_lang: str, existing: dict) -> str:
    """"Never mind" in the middle of a booking or a reschedule."""
    held = existing.get("slot")
    if held:
        try:
            release_slot(held["slot_id"], session_id)
        except Exception as e:
            print(f"[Orchestrator] Could not release slot: {e}")
    _delete_session_state(session_id)
    return render_template("RESCHED_KEPT" if existing.get("flow") == "reschedule" else "CANCELLED", short_lang)


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

    # The model can miss a short "11 am" or "tomorrow": read those by rules.
    _rescue_slots(extracted, user_text, existing["state"] if existing else None)

    # Debug logging
    print(f"[Orchestrator] INPUT: {user_text}")
    print(f"[Orchestrator] EXTRACTED: {extracted}")

    # ---------------------------------------------------------
    # Cancel / reschedule: one already running, or a new request
    # ---------------------------------------------------------

    if existing is not None and existing.get("state") in CHANGE_STATES:
        return _continue_change(session_id, short_lang, existing, extracted, user_text)

    if existing is None:
        kind = detect_change_intent(user_text)
        if kind is None and extracted.get("intent") == "Cancel/Reschedule":
            kind = "ask"
        if kind:
            return _start_change(session_id, short_lang, kind, extracted)

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

    if state in ("ASK_DOCTOR", "ASK_DATE", "ASK_TIME") and wants_to_stop(user_text):
        return _abort_flow(session_id, short_lang, existing)

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
                "RESCHED_KEPT" if existing.get("flow") == "reschedule" else "CANCELLED",
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

    # A reschedule stays with the same doctor: a doctor named now is ignored.
    if existing.get("flow") == "reschedule":
        extracted["doctor_name"] = None

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

    # Rescheduling: the same questions, worded for moving an appointment.
    if entry.get("flow") == "reschedule":

        target = entry["target"]

        if state == "ASK_DATE":

            return render_template(
                "RESCHED_ASK_DATE",
                short_lang,
                doctor=target["doctor_name"],
                when=target["when"],
            )

        if state == "CONFIRM":

            return render_template(
                "RESCHED_CONFIRM",
                short_lang,
                doctor=target["doctor_name"],
                old=target["when"],
                date=slots["appointment_date"],
                time=slots["appointment_time"],
            )

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

        if entry.get("flow") == "reschedule":

            # Book the new slot and cancel the old appointment in ONE step.
            reschedule_my_appointment(
                entry["target"]["appointment_id"],
                held["slot_id"],
                entry["auth_id"],
                session_id,
            )

        else:

            # The doctor and time come from the slot itself; Team C locks it.
            create_appointment(
                session_id=session_id,
                patient_uhid=PLACEHOLDER_PATIENT_UHID,
                slot_id=held["slot_id"],
                status="confirmed",
            )

    except ChangeRefused as refused:

        print(f"[Orchestrator] Reschedule refused: {refused.code}")

        try:
            release_slot(held["slot_id"], session_id)
        except Exception:
            pass

        _delete_session_state(session_id)

        return render_template(
            "CANNOT_CHANGE",
            short_lang,
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
            "CHANGE_FAILED" if entry.get("flow") == "reschedule" else "BOOKING_FAILED",
            short_lang,
        )

    # Appointment was successfully created (or moved).
    reply = render_template(
        "RESCHED_DONE" if entry.get("flow") == "reschedule" else "CONFIRMED",
        short_lang,
        doctor=doctor["name"],
        date=slots["appointment_date"],
        time=slots["appointment_time"],
    )

    # Booking is finished, so remove temporary Redis state.
    _delete_session_state(session_id)

    print("[Orchestrator] Appointment successfully created")

    return reply
