"""Follow-up visits: suggested from the note, booked when the doctor approves it.

The doctor says "continue the medicine and come back next week". The note's plan
holds that sentence; this module

1. spots a time-based follow-up in the plan (rules - only a clear time counts),
2. keeps one suggestion per consultation up to date as the note is edited,
3. on approval, books the same doctor at the same time of day, found with the
   same slot locking as any booking, linked to the original visit.

"Come back if it gets worse" has no time, so it suggests nothing.
"""
import logging
import re
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy.orm import Session

from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.consultation import Consultation, ConsultationNote
from app.models.doctor import Doctor
from app.models.follow_up import FollowUp, FollowUpStatus
from app.services import reminder_service
from app.services.audit_service import write_audit_log
from app.services.slot_service import (
    IST,
    SlotNotFoundError,
    SlotUnavailableError,
    as_ist,
    claim_slot_for_booking,
    list_free_slots,
    utcnow,
)

log = logging.getLogger("zenvy.followups")

MAX_INTERVAL_DAYS = 180
# How many days after the wanted day we look for a free slot (a Sunday, a full day...).
SEARCH_DAYS = 7


class FollowUpUnavailable(Exception):
    """No free slot could be found for the follow-up."""


class FollowUpNotAllowed(Exception):
    """The follow-up cannot be changed in its current state. ``code`` says why."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# reading the plan
# ---------------------------------------------------------------------------

_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}
_UNIT_DAYS = {"day": 1, "week": 7, "fortnight": 14, "month": 30}
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}

_NUMBER = r"(a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|\d{1,3})"
_UNIT = r"(day|week|fortnight|month)s?"

# a sentence has to be about coming back, reviewing or following up
_CONTEXT = re.compile(
    r"\b(come back|comes back|return|review|follow[- ]?up|revisit|re-?visit|see (?:me|you|the doctor)|"
    r"visit again|check[- ]?up|recheck|re-?check|reassess|back to (?:see|the clinic))\b"
)
# "no follow-up needed", "no need to come back"
_NEGATED = re.compile(
    r"\b(no|not|without)\s+(?:need\s+(?:for|to|of)\s+)?(?:a\s+)?(?:further\s+)?"
    r"(follow[- ]?up|review|visit|return|come back|check[- ]?up)\b|\bno need to (?:come back|return|follow)"
)


def _number(token: str) -> int:
    return _NUMBERS[token] if token in _NUMBERS else int(token)


def detect_follow_up(plan: str, base_date: date) -> dict | None:
    """The first clear time-based follow-up in a plan, or None.

    Returns {"interval_days": int, "source_text": str}. ``base_date`` is the day of
    the visit (for "next Monday"). Only a sentence that is about coming back /
    reviewing counts, and only if it gives a time.
    """
    for sentence in re.split(r"(?<=[.;!?])\s+|\n+", (plan or "").strip()):
        text = " ".join(sentence.lower().split())
        if not text or not _CONTEXT.search(text) or _NEGATED.search(text):
            continue

        days = None
        # "in/after/within 2 weeks", "after a week", "in 10 days"
        match = re.search(rf"\b(?:in|after|within)\s+(?:about\s+|around\s+)?{_NUMBER}\s*{_UNIT}\b", text)
        if match:
            days = _number(match.group(1)) * _UNIT_DAYS[match.group(2)]
        if days is None:
            # "next week", "next month", "next fortnight"
            match = re.search(r"\bnext\s+(week|fortnight|month)\b", text)
            if match:
                days = _UNIT_DAYS[match.group(1)]
        if days is None:
            # "a week later", "one month from now"
            match = re.search(rf"\b{_NUMBER}\s*{_UNIT}\s+(?:later|from now)\b", text)
            if match:
                days = _number(match.group(1)) * _UNIT_DAYS[match.group(2)]
        if days is None:
            # "next monday", "on friday"
            match = re.search(r"\b(?:next|on|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
            if match:
                days = (_WEEKDAYS[match.group(1)] - base_date.weekday()) % 7 or 7
        if days is None and re.search(r"\btomorrow\b", text):
            days = 1
        if days is None:
            # "review 10 days" - but never "for 5 days" (that is how long to take a medicine)
            match = re.search(rf"(?<!for )\b{_NUMBER}\s*{_UNIT}\b", text)
            if match and not re.search(rf"\bfor\s+(?:about\s+)?{_NUMBER}\s*{_UNIT}\b", text):
                days = _number(match.group(1)) * _UNIT_DAYS[match.group(2)]

        if days is not None and 1 <= days <= MAX_INTERVAL_DAYS:
            return {"interval_days": days, "source_text": sentence.strip()[:300]}
    return None


# ---------------------------------------------------------------------------
# slots
# ---------------------------------------------------------------------------

def _visit_date_and_time(appointment: AIAppointment) -> tuple[date, time]:
    local = as_ist(appointment.appointment_datetime)
    return local.date(), local.time().replace(second=0, microsecond=0)


def find_slot(db: Session, doctor: Doctor, wanted_date: date, wanted_time: time | None):
    """The free slot of ``doctor`` closest to the wanted day and time.

    Looks at the wanted day first, then up to SEARCH_DAYS days after it (a Sunday
    or a fully booked day moves to the next day with a free slot). Never in the past.
    """
    today = utcnow().astimezone(IST).date()
    first_day = max(wanted_date, today)
    wanted_minutes = (wanted_time.hour * 60 + wanted_time.minute) if wanted_time else 9 * 60
    for offset in range(SEARCH_DAYS + 1):
        day = first_day + timedelta(days=offset)
        free = list_free_slots(db, doctor, day)
        if free:
            def distance(slot):
                local = as_ist(slot.slot_start)
                return abs(local.hour * 60 + local.minute - wanted_minutes)

            return min(free, key=distance)
    return None


# ---------------------------------------------------------------------------
# the suggestion
# ---------------------------------------------------------------------------

def get_follow_up(db: Session, consultation: Consultation) -> FollowUp | None:
    return db.query(FollowUp).filter(FollowUp.consultation_id == consultation.consultation_id).first()


def _original(db: Session, consultation: Consultation) -> AIAppointment:
    return db.get(AIAppointment, consultation.appointment_id)


def refresh_suggestion(db: Session, consultation: Consultation, note: ConsultationNote) -> FollowUp | None:
    """Keep the suggestion in step with the newest note.

    * nothing yet and the plan has a follow-up   -> a new suggestion
    * suggested, doctor has not touched it       -> follows the plan (or goes away)
    * the doctor chose a date themselves         -> left alone
    * booked                                     -> never changed
    * declined                                   -> only comes back if the plan now says something different
    """
    original = _original(db, consultation)
    if original is None:
        return None
    visit_date, visit_time = _visit_date_and_time(original)
    found = detect_follow_up(note.plan, visit_date)
    existing = get_follow_up(db, consultation)

    if existing is None:
        if found is None:
            return None
        row = FollowUp(
            follow_up_id=str(uuid.uuid4()),
            consultation_id=consultation.consultation_id,
            appointment_id=original.appointment_id,
            doctor_id=consultation.doctor_id,
            status=FollowUpStatus.suggested.value,
            interval_days=found["interval_days"],
            source_text=found["source_text"],
            suggested_date=visit_date + timedelta(days=found["interval_days"]),
            suggested_time=visit_time,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    if existing.status == FollowUpStatus.booked.value or existing.edited_by_doctor and existing.status != FollowUpStatus.declined.value:
        return existing
    if existing.status == FollowUpStatus.declined.value:
        if found is None or found["interval_days"] == existing.interval_days:
            return existing
    if found is None:
        if existing.status in (FollowUpStatus.suggested.value, FollowUpStatus.failed.value):
            db.delete(existing)
            db.commit()
            return None
        return existing

    existing.status = FollowUpStatus.suggested.value
    existing.interval_days = found["interval_days"]
    existing.source_text = found["source_text"]
    existing.suggested_date = visit_date + timedelta(days=found["interval_days"])
    existing.suggested_time = visit_time
    existing.failure_reason = None
    existing.edited_by_doctor = False
    db.commit()
    db.refresh(existing)
    return existing


def set_follow_up(
    db: Session, consultation: Consultation, day: date, at: time | None, doctor_auth_id: str
) -> FollowUp:
    """The doctor chooses (or changes) the follow-up date and time."""
    original = _original(db, consultation)
    visit_date, visit_time = _visit_date_and_time(original)
    if day <= visit_date:
        raise FollowUpNotAllowed("must_be_after_the_visit")
    existing = get_follow_up(db, consultation)
    if existing is not None and existing.status == FollowUpStatus.booked.value:
        raise FollowUpNotAllowed("already_booked")
    if existing is None:
        existing = FollowUp(
            follow_up_id=str(uuid.uuid4()),
            consultation_id=consultation.consultation_id,
            appointment_id=original.appointment_id,
            doctor_id=consultation.doctor_id,
        )
        db.add(existing)
    existing.status = FollowUpStatus.suggested.value
    existing.suggested_date = day
    existing.suggested_time = at or visit_time
    existing.interval_days = (day - visit_date).days
    existing.edited_by_doctor = True
    existing.failure_reason = None
    existing.decided_by_auth_id = doctor_auth_id
    db.commit()
    db.refresh(existing)
    return existing


def decline_follow_up(db: Session, consultation: Consultation, doctor_auth_id: str) -> FollowUp | None:
    """The doctor says: no follow-up."""
    existing = get_follow_up(db, consultation)
    if existing is None:
        return None
    if existing.status == FollowUpStatus.booked.value:
        raise FollowUpNotAllowed("already_booked")
    existing.status = FollowUpStatus.declined.value
    existing.decided_by_auth_id = doctor_auth_id
    existing.failure_reason = None
    db.commit()
    db.refresh(existing)
    return existing


def preview_slot(db: Session, consultation: Consultation, follow_up: FollowUp):
    """The slot a booking would take right now (None if there is none)."""
    if follow_up.status not in (FollowUpStatus.suggested.value, FollowUpStatus.failed.value) or not follow_up.suggested_date:
        return None
    doctor = db.get(Doctor, consultation.doctor_id)
    return find_slot(db, doctor, follow_up.suggested_date, follow_up.suggested_time)


# ---------------------------------------------------------------------------
# booking
# ---------------------------------------------------------------------------

def book_follow_up(db: Session, consultation: Consultation, doctor_auth_id: str) -> AIAppointment:
    """Book the follow-up. Raises FollowUpUnavailable if no slot is free,
    FollowUpNotAllowed if there is nothing to book."""
    follow_up = get_follow_up(db, consultation)
    if follow_up is None or follow_up.status not in (FollowUpStatus.suggested.value, FollowUpStatus.failed.value):
        raise FollowUpNotAllowed("nothing_to_book")
    original = _original(db, consultation)
    doctor = db.get(Doctor, consultation.doctor_id)

    # Another patient may take the slot between looking and booking: try a few times.
    for _ in range(3):
        slot = find_slot(db, doctor, follow_up.suggested_date, follow_up.suggested_time)
        if slot is None:
            follow_up.status = FollowUpStatus.failed.value
            follow_up.failure_reason = "no_free_slot"
            db.commit()
            raise FollowUpUnavailable(follow_up.follow_up_id)

        new_id = str(uuid.uuid4())
        appointment = AIAppointment(
            appointment_id=new_id,
            session_id=original.session_id,
            patient_phone_no=original.patient_phone_no,
            patient_uhid=original.patient_uhid,
            doctor_name=doctor.name,
            appointment_datetime=slot.slot_start,
            status=AppointmentStatus.confirmed,
            booking_info={"location": doctor.hospital.name},
            doctor_id=doctor.doctor_id,
            slot_id=slot.slot_id,
            appointment_type="follow_up",
            parent_appointment_id=original.appointment_id,
        )
        try:
            claim_slot_for_booking(db, slot.slot_id, original.session_id, new_id)
            db.add(appointment)
            follow_up.status = FollowUpStatus.booked.value
            follow_up.new_appointment_id = new_id
            follow_up.failure_reason = None
            follow_up.decided_by_auth_id = doctor_auth_id
            db.commit()
        except (SlotUnavailableError, SlotNotFoundError):
            db.rollback()
            follow_up = get_follow_up(db, consultation)
            continue
        db.refresh(appointment)
        break
    else:
        follow_up = get_follow_up(db, consultation)
        follow_up.status = FollowUpStatus.failed.value
        follow_up.failure_reason = "no_free_slot"
        db.commit()
        raise FollowUpUnavailable(follow_up.follow_up_id)

    try:
        reminder_service.schedule_for_appointment(db, appointment, follow_up=True)
    except Exception:  # noqa: BLE001 - the booking itself is done
        log.exception("Could not queue follow-up reminders")
        db.rollback()
    write_audit_log(
        db,
        action="follow_up_booked",
        actor=f"doctor:{doctor_auth_id}",
        session_id=original.session_id,
        user_id=consultation.patient_auth_id,
        after_value={
            "follow_up_id": follow_up.follow_up_id,
            "original_appointment_id": original.appointment_id,
            "new_appointment_id": appointment.appointment_id,
            "interval_days": follow_up.interval_days,
        },
    )
    return appointment


def book_on_approval(db: Session, consultation: Consultation, doctor_auth_id: str) -> tuple[FollowUp | None, AIAppointment | None]:
    """Called right after the doctor approves a note: book a pending suggestion.

    Never raises: a failed follow-up must not undo the approval. Returns the
    follow-up record (its status says what happened) and the new appointment.
    """
    follow_up = get_follow_up(db, consultation)
    if follow_up is None or follow_up.status != FollowUpStatus.suggested.value:
        return follow_up, None
    try:
        return get_follow_up(db, consultation), book_follow_up(db, consultation, doctor_auth_id)
    except (FollowUpUnavailable, FollowUpNotAllowed):
        return get_follow_up(db, consultation), None
    except Exception:  # noqa: BLE001
        log.exception("Follow-up booking failed")
        db.rollback()
        return get_follow_up(db, consultation), None
