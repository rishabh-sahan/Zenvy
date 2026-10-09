"""Reminders and notices about appointments.

Every message goes through one table (``reminders``) so there is a single record
of who was told what and when:

* ``booked`` / ``follow_up_booked``   - something new exists
* ``reminder_24h`` / ``reminder_2h``  - before the appointment, to patient and doctor
* ``cancelled`` / ``rescheduled``     - something changed

Rows are created when the appointment is booked, changed or cancelled. A
background scheduler (scheduler.py) sends the ones that are due. Each row is
claimed with a single conditional UPDATE, so even if two copies of the service
run, a message is sent once.

By default nothing is really sent ("mock" mode): the message is written to the
row and the log. Switch to "live" (REMINDER_MODE=live) only after the WhatsApp
templates in WHATSAPP_TEMPLATES.md are approved in your Meta account.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.authentication import Authentication
from app.models.doctor import Doctor
from app.models.reminder import Reminder, ReminderKind, ReminderStatus
from app.services import whatsapp_service
from app.services.slot_service import as_ist, as_utc, utcnow

log = logging.getLogger("zenvy.reminders")

MAX_ATTEMPTS = 2  # the first try plus one retry

# Notices about a change are still worth sending after the appointment time was
# set; everything else is pointless once the appointment has started.
CHANGE_NOTICES = {ReminderKind.cancelled.value, ReminderKind.rescheduled.value}


# ---------------------------------------------------------------------------
# who and what
# ---------------------------------------------------------------------------

def _patient(db: Session, appointment: AIAppointment) -> Authentication | None:
    if not appointment.patient_phone_no:
        return None
    return db.query(Authentication).filter(Authentication.phone_no == appointment.patient_phone_no).first()


def _doctor_account(db: Session, appointment: AIAppointment) -> Authentication | None:
    if not appointment.doctor_id:
        return None
    doctor = db.get(Doctor, appointment.doctor_id)
    if doctor is None or not doctor.auth_id:
        return None
    return db.get(Authentication, doctor.auth_id)


def patient_label(appointment: AIAppointment) -> str:
    digits = "".join(ch for ch in (appointment.patient_phone_no or "") if ch.isdigit())
    return f"Patient ••••{digits[-4:]}" if len(digits) >= 4 else "Patient"


def _location(appointment: AIAppointment) -> str:
    info = appointment.booking_info or {}
    return info.get("location") or info.get("clinic") or "the hospital"


def _day(dt: datetime) -> str:
    return as_ist(dt).strftime("%a %d %b")


def _clock(dt: datetime) -> str:
    return as_ist(dt).strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------------------
# creating rows
# ---------------------------------------------------------------------------

def _ensure(
    db: Session,
    appointment_id: str,
    recipient_type: str,
    recipient_auth_id: str | None,
    kind: str,
    send_at: datetime,
    details: dict | None = None,
) -> Reminder | None:
    """Create the row unless this exact message already exists. Does NOT commit."""
    existing = (
        db.query(Reminder)
        .filter(
            Reminder.appointment_id == appointment_id,
            Reminder.recipient_type == recipient_type,
            Reminder.kind == kind,
        )
        .first()
    )
    if existing is not None:
        return None
    row = Reminder(
        reminder_id=str(uuid.uuid4()),
        appointment_id=appointment_id,
        recipient_type=recipient_type,
        recipient_auth_id=recipient_auth_id,
        kind=kind,
        send_at=as_utc(send_at),
        status=ReminderStatus.pending.value,
        attempts=0,
        details=details,
    )
    db.add(row)
    return row


def schedule_for_appointment(
    db: Session, appointment: AIAppointment, follow_up: bool = False, now: datetime | None = None
) -> list[Reminder]:
    """Create the reminders for a newly booked appointment, for patient AND doctor.

    * right away:  the doctor is told a new appointment exists (the patient already
                   gets the existing booking confirmation; a follow-up has its own notice)
    * 24 h and 2 h before: both people - unless that moment has already passed
    """
    now = now or utcnow()
    start = as_utc(appointment.appointment_datetime)
    patient = _patient(db, appointment)
    doctor = _doctor_account(db, appointment)
    created: list[Reminder] = []

    def add(recipient_type, auth, kind, send_at, details=None):
        if auth is None:
            return
        row = _ensure(db, appointment.appointment_id, recipient_type, auth.auth_id, kind, send_at, details)
        if row is not None:
            created.append(row)

    if follow_up:
        add("patient", patient, ReminderKind.follow_up_booked.value, now)
    add("doctor", doctor, ReminderKind.booked.value, now, {"follow_up": follow_up})

    for kind, before in ((ReminderKind.reminder_24h.value, timedelta(hours=24)),
                         (ReminderKind.reminder_2h.value, timedelta(hours=2))):
        send_at = start - before
        if send_at <= now:
            continue  # e.g. booked 5 hours ahead: there is no "24 hours before" any more
        add("patient", patient, kind, send_at)
        add("doctor", doctor, kind, send_at)

    db.commit()
    return created


def record_booking_confirmation(db: Session, appointment: AIAppointment, ok: bool, error: str | None = None) -> None:
    """Record the patient's booking confirmation (sent by the existing booking route)."""
    patient = _patient(db, appointment)
    if patient is None:
        return
    row = _ensure(db, appointment.appointment_id, "patient", patient.auth_id, ReminderKind.booked.value, utcnow())
    if row is None:
        return
    row.status = ReminderStatus.sent.value if ok else ReminderStatus.failed.value
    row.attempts = 1
    row.mode = "live"
    row.sent_at = utcnow() if ok else None
    row.last_error = None if ok else (error or "WhatsApp confirmation failed")[:200]
    row.message_text = "Booking confirmation (sent when the appointment was booked)"
    db.commit()


def notify_change(
    db: Session, appointment: AIAppointment, kind: str, details: dict | None = None, now: datetime | None = None
) -> list[Reminder]:
    """Queue a 'cancelled' or 'rescheduled' notice for the patient and the doctor."""
    now = now or utcnow()
    created = []
    for recipient_type, auth in (("patient", _patient(db, appointment)), ("doctor", _doctor_account(db, appointment))):
        if auth is None:
            continue
        row = _ensure(db, appointment.appointment_id, recipient_type, auth.auth_id, kind, now, details)
        if row is not None:
            created.append(row)
    db.commit()
    return created


def cancel_pending(db: Session, appointment_id: str) -> int:
    """Stop every message that has not been sent yet for this appointment."""
    result = db.execute(
        update(Reminder)
        .where(Reminder.appointment_id == appointment_id, Reminder.status == ReminderStatus.pending.value)
        .values(status=ReminderStatus.cancelled.value)
    )
    db.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# the message itself
# ---------------------------------------------------------------------------

def build_message(db: Session, reminder: Reminder, appointment: AIAppointment) -> tuple[str, str, list[tuple[str, str]]]:
    """(text, Meta template name, template parameters) for one reminder."""
    when = appointment.appointment_datetime
    doctor = appointment.doctor_name
    day, clock, where = _day(when), _clock(when), _location(appointment)
    kind = reminder.kind

    if reminder.recipient_type == "patient":
        auth = db.get(Authentication, reminder.recipient_auth_id) if reminder.recipient_auth_id else None
        name = (auth.name if auth and auth.name else "there")
        if kind in (ReminderKind.reminder_24h.value, ReminderKind.reminder_2h.value):
            soon = "tomorrow" if kind == ReminderKind.reminder_24h.value else "in 2 hours"
            text = f"Reminder: your appointment with {doctor} is {soon} - {day} at {clock}, {where}."
            return text, settings.META_WHATSAPP_REMINDER_TEMPLATE_NAME, [
                ("name", name), ("doctor", doctor), ("date", day), ("time", clock), ("location", where), ("when", soon)]
        if kind == ReminderKind.follow_up_booked.value:
            text = f"Your follow-up with {doctor} is booked for {day} at {clock}, {where}."
            return text, settings.META_WHATSAPP_FOLLOWUP_TEMPLATE_NAME, [
                ("name", name), ("doctor", doctor), ("date", day), ("time", clock), ("location", where)]
        if kind == ReminderKind.cancelled.value:
            text = f"Your appointment with {doctor} on {day} at {clock} has been cancelled."
            return text, settings.META_WHATSAPP_CANCELLED_TEMPLATE_NAME, [
                ("name", name), ("doctor", doctor), ("date", day), ("time", clock)]
        if kind == ReminderKind.rescheduled.value:
            text = f"Your appointment with {doctor} has been moved to {day} at {clock}, {where}."
            return text, settings.META_WHATSAPP_RESCHEDULED_TEMPLATE_NAME, [
                ("name", name), ("doctor", doctor), ("date", day), ("time", clock), ("location", where)]
        text = f"Your appointment with {doctor} is on {day} at {clock}, {where}."
        return text, settings.META_WHATSAPP_REMINDER_TEMPLATE_NAME, [
            ("name", name), ("doctor", doctor), ("date", day), ("time", clock), ("location", where), ("when", day)]

    # the doctor: always one short notice
    who = patient_label(appointment)
    details = reminder.details or {}
    if kind == ReminderKind.booked.value:
        what = "New follow-up" if details.get("follow_up") else "New appointment"
        text = f"{what}: {who} on {day} at {clock}."
    elif kind == ReminderKind.reminder_24h.value:
        text = f"Reminder: appointment tomorrow - {who}, {day} at {clock}."
    elif kind == ReminderKind.reminder_2h.value:
        text = f"Reminder: appointment in 2 hours - {who}, {day} at {clock}."
    elif kind == ReminderKind.cancelled.value:
        text = f"Cancelled: {who} on {day} at {clock}."
    elif kind == ReminderKind.rescheduled.value:
        old = details.get("old_datetime")
        before = f" (was {_day(datetime.fromisoformat(old))} at {_clock(datetime.fromisoformat(old))})" if old else ""
        text = f"Rescheduled: {who} is now on {day} at {clock}{before}."
    else:
        text = f"Appointment update: {who} on {day} at {clock}."
    return text, settings.META_WHATSAPP_DOCTOR_NOTICE_TEMPLATE_NAME, [("message", text)]


# ---------------------------------------------------------------------------
# sending
# ---------------------------------------------------------------------------

def _say(line: str) -> None:
    """print() that cannot crash on a console that cannot show the masking dots."""
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))


def _claim(db: Session, reminder_id: str) -> bool:
    """Take a pending reminder for sending. Exactly one caller can succeed."""
    result = db.execute(
        update(Reminder)
        .where(Reminder.reminder_id == reminder_id, Reminder.status == ReminderStatus.pending.value)
        .values(status=ReminderStatus.sending.value, attempts=Reminder.attempts + 1)
    )
    db.commit()
    return result.rowcount == 1


def _finish(db: Session, reminder: Reminder, status: str, **fields) -> None:
    reminder.status = status
    for key, value in fields.items():
        setattr(reminder, key, value)
    db.commit()


def _deliver(db: Session, reminder: Reminder, now: datetime) -> str:
    """Send (or mock-send) one claimed reminder. Returns the final status."""
    appointment = db.get(AIAppointment, reminder.appointment_id)
    if appointment is None:
        _finish(db, reminder, ReminderStatus.skipped.value, last_error="appointment not found")
        return ReminderStatus.skipped.value

    is_change_notice = reminder.kind in CHANGE_NOTICES
    cancelled = appointment.status == AppointmentStatus.cancelled
    if cancelled and not is_change_notice:
        _finish(db, reminder, ReminderStatus.cancelled.value, last_error="appointment was cancelled")
        return ReminderStatus.cancelled.value
    if not is_change_notice and as_utc(appointment.appointment_datetime) <= now:
        _finish(db, reminder, ReminderStatus.skipped.value, last_error="appointment already started")
        return ReminderStatus.skipped.value

    text, template, parameters = build_message(db, reminder, appointment)
    reminder.message_text = text

    recipient = (
        _patient(db, appointment) if reminder.recipient_type == "patient" else _doctor_account(db, appointment)
    )
    if recipient is None or not recipient.phone_no:
        _finish(db, reminder, ReminderStatus.skipped.value, last_error="no phone number for the recipient")
        return ReminderStatus.skipped.value

    if settings.REMINDER_MODE != "live":
        log.info("[reminder:mock] %s -> %s: %s", reminder.kind, reminder.recipient_type, text)
        _say(f"[Reminder:mock] {reminder.kind} -> {reminder.recipient_type}: {text}")
        _finish(
            db, reminder, ReminderStatus.sent.value, mode="mock", sent_at=now,
            provider_message_id=f"mock-{uuid.uuid4().hex[:12]}", last_error=None,
        )
        return ReminderStatus.sent.value

    try:
        message_id = whatsapp_service.send_template(recipient.phone_no, template, parameters)
    except Exception as exc:  # noqa: BLE001 - any send problem is retried or recorded, never raised
        error = str(exc)[:200]
        if reminder.attempts < MAX_ATTEMPTS:
            # one retry, a few minutes later
            _finish(
                db, reminder, ReminderStatus.pending.value, mode="live", last_error=error,
                send_at=now + timedelta(minutes=settings.REMINDER_RETRY_MINUTES),
            )
            log.warning("[reminder] attempt %s failed, will retry: %s", reminder.attempts, error)
            return ReminderStatus.pending.value
        _finish(db, reminder, ReminderStatus.failed.value, mode="live", last_error=error)
        log.error("[reminder] gave up on %s: %s", reminder.reminder_id, error)
        return ReminderStatus.failed.value

    _finish(db, reminder, ReminderStatus.sent.value, mode="live", sent_at=now, provider_message_id=message_id, last_error=None)
    return ReminderStatus.sent.value


def process_due(
    db: Session, now: datetime | None = None, limit: int = 100, appointment_id: str | None = None
) -> dict:
    """Send every reminder that is due. Safe to call from several processes at once.

    ``appointment_id`` limits the run to one appointment (for tooling and tests).
    """
    now = now or utcnow()
    query = db.query(Reminder.reminder_id).filter(
        Reminder.status == ReminderStatus.pending.value, Reminder.send_at <= now
    )
    if appointment_id:
        query = query.filter(Reminder.appointment_id == appointment_id)
    due_ids = [row[0] for row in query.order_by(Reminder.send_at).limit(limit).all()]
    counts: dict[str, int] = {}
    for reminder_id in due_ids:
        if not _claim(db, reminder_id):
            continue  # another scheduler took it
        reminder = db.get(Reminder, reminder_id)
        db.refresh(reminder)
        outcome = _deliver(db, reminder, now)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
