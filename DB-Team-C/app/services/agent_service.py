"""The coordinator: it passes what happens on one side (patient / doctor) to the other, and does the
mechanical follow-through. It never makes a medical decision.

How it works
* The booking, cancel, reschedule, follow-up and prescription flows call ``emit`` after they have
  committed. That queues an ``agent_events`` row (the same fact is never queued twice).
* The scheduler calls ``process_events``. Each event is claimed with one conditional UPDATE, so even
  with several schedulers it is handled once. A failed event is retried, then marked failed.
* Handling an event writes short messages for the doctor agent / patient agent (``agent_messages``),
  and for a signed prescription it schedules the doses and their reminders.
* Everything the agents do is recorded in ``agent_actions`` (ids and short summaries, no phone numbers).
"""

import logging
import uuid
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.agent import AgentAction, AgentEvent, AgentMessage, EventStatus
from app.models.ai_appointment import AIAppointment
from app.models.consultation import Consultation, ConsultationNote, NoteStatus
from app.models.doctor import Doctor
from app.models.prescription import DoseStatus, MedicationDose, Prescription, PrescriptionStatus
from app.services.slot_service import as_ist, as_utc, utcnow

log = logging.getLogger("zenvy.agents")

MAX_ATTEMPTS = 3
ADHERENCE_ALERT_AT = 2     # tell the doctor when a patient missed this many doses within a day


# ---------------------------------------------------------------------------
# queueing and recording (these never raise: a problem here must not break a booking)
# ---------------------------------------------------------------------------

def emit(
    db: Session, kind: str, appointment_id: str | None = None, payload: dict | None = None, dedupe_key: str | None = None
) -> AgentEvent | None:
    try:
        if dedupe_key and db.query(AgentEvent.event_id).filter(AgentEvent.dedupe_key == dedupe_key).first():
            return None
        event = AgentEvent(
            event_id=str(uuid.uuid4()), kind=kind, appointment_id=appointment_id, payload=payload,
            status=EventStatus.pending.value, attempts=0, dedupe_key=dedupe_key,
        )
        db.add(event)
        db.commit()
        return event
    except Exception:  # noqa: BLE001
        log.exception("Could not queue agent event %s", kind)
        db.rollback()
        return None


def record_action(
    db: Session, agent: str, tool: str, summary: str | None = None, actor_auth_id: str | None = None,
    appointment_id: str | None = None, result: str = "ok",
) -> None:
    try:
        db.add(AgentAction(
            action_id=str(uuid.uuid4()), agent=agent, actor_auth_id=actor_auth_id, tool=tool,
            summary=(summary or "")[:200] or None, result=result, appointment_id=appointment_id,
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Could not record agent action %s", tool)
        db.rollback()


def add_message(
    db: Session, recipient_type: str, auth_id: str | None, kind: str, text: str, appointment_id: str | None = None
) -> None:
    """Does NOT commit: the caller's handler commits with the event's status."""
    if not auth_id:
        return
    db.add(AgentMessage(
        message_id=str(uuid.uuid4()), recipient_type=recipient_type, recipient_auth_id=auth_id,
        appointment_id=appointment_id, kind=kind, text=text,
    ))


def messages_for(db: Session, auth_id: str, unread_only: bool = True, limit: int = 30) -> list[AgentMessage]:
    query = db.query(AgentMessage).filter(AgentMessage.recipient_auth_id == auth_id)
    if unread_only:
        query = query.filter(AgentMessage.read_at.is_(None))
    return query.order_by(AgentMessage.created_at.desc()).limit(limit).all()


def mark_read(db: Session, auth_id: str, message_ids: list[str] | None = None) -> int:
    query = update(AgentMessage).where(AgentMessage.recipient_auth_id == auth_id, AgentMessage.read_at.is_(None))
    if message_ids is not None:
        query = query.where(AgentMessage.message_id.in_(message_ids))
    result = db.execute(query.values(read_at=utcnow()).execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount or 0


# ---------------------------------------------------------------------------
# handling events
# ---------------------------------------------------------------------------

def _who(db: Session, appointment: AIAppointment):
    from app.services import reminder_service

    patient = reminder_service._patient(db, appointment)
    doctor = reminder_service._doctor_account(db, appointment)
    return patient, doctor


def _when(appointment: AIAppointment) -> str:
    from app.services import reminder_service

    return f"{reminder_service._day(appointment.appointment_datetime)} at {reminder_service._clock(appointment.appointment_datetime)}"


def _label(appointment: AIAppointment) -> str:
    from app.services import reminder_service

    return reminder_service.patient_label(appointment)


def _appointment_event(text_for_doctor):
    """A handler that tells the treating doctor's agent."""
    def handle(db: Session, event: AgentEvent, now: datetime) -> str:
        appointment = db.get(AIAppointment, event.appointment_id) if event.appointment_id else None
        if appointment is None:
            return "appointment not found"
        _, doctor = _who(db, appointment)
        add_message(db, "doctor", doctor.auth_id if doctor else None, event.kind,
                    text_for_doctor(appointment, event.payload or {}), appointment.appointment_id)
        return "told the doctor agent"
    return handle


def _rescheduled_text(appointment: AIAppointment, payload: dict) -> str:
    old = payload.get("old_datetime")
    was = ""
    if old:
        from app.services import reminder_service

        before = datetime.fromisoformat(old)
        was = f" (was {reminder_service._day(before)} at {reminder_service._clock(before)})"
    return f"Rescheduled: {_label(appointment)} is now on {_when(appointment)}{was}."


def _follow_up_booked(db: Session, event: AgentEvent, now: datetime) -> str:
    appointment = db.get(AIAppointment, event.appointment_id) if event.appointment_id else None
    if appointment is None:
        return "appointment not found"
    patient, doctor = _who(db, appointment)
    add_message(db, "doctor", doctor.auth_id if doctor else None, event.kind,
                f"Follow-up booked: {_label(appointment)} on {_when(appointment)}.", appointment.appointment_id)
    add_message(db, "patient", patient.auth_id if patient else None, event.kind,
                f"Your follow-up with {appointment.doctor_name} is booked for {_when(appointment)}.",
                appointment.appointment_id)
    return "told both agents"


def _prescription_signed(db: Session, event: AgentEvent, now: datetime) -> str:
    from app.services import prescription_service

    payload = event.payload or {}
    prescription = db.get(Prescription, payload.get("prescription_id"))
    if prescription is None or prescription.status != PrescriptionStatus.signed.value:
        return "prescription is not signed any more"
    cancelled = 0
    if payload.get("superseded_id"):
        cancelled = prescription_service.cancel_doses(db, payload["superseded_id"])
    scheduled = prescription_service.schedule_doses(db, prescription, now)

    appointment = db.get(AIAppointment, prescription.appointment_id)
    patient, doctor = _who(db, appointment)
    medicines = len(prescription.items)
    add_message(db, "doctor", doctor.auth_id if doctor else None, event.kind,
                f"Prescription signed for {_label(appointment)}: {medicines} medicine(s), "
                f"{scheduled} dose reminder(s) scheduled" + (f", {cancelled} older dose(s) cancelled." if cancelled else "."),
                appointment.appointment_id)
    add_message(db, "patient", prescription.patient_auth_id, event.kind,
                f"{appointment.doctor_name} has prescribed {medicines} medicine(s). Reminders are set - "
                "see Your medicines.", appointment.appointment_id)
    return f"{scheduled} doses scheduled, {cancelled} cancelled"


def _adherence_alert(db: Session, event: AgentEvent, now: datetime) -> str:
    payload = event.payload or {}
    prescription = db.get(Prescription, payload.get("prescription_id"))
    if prescription is None:
        return "prescription not found"
    appointment = db.get(AIAppointment, prescription.appointment_id)
    _, doctor = _who(db, appointment)
    names = ", ".join(i.drug_name for i in prescription.items[:3])
    add_message(db, "doctor", doctor.auth_id if doctor else None, event.kind,
                f"{_label(appointment)} has missed {payload.get('missed', 'several')} doses in the last day ({names}).",
                appointment.appointment_id)
    return "told the doctor agent"


HANDLERS = {
    "appointment_booked": _appointment_event(lambda a, p: f"New appointment: {_label(a)} on {_when(a)}."),
    "appointment_cancelled": _appointment_event(lambda a, p: f"Cancelled: {_label(a)} on {_when(a)}."),
    "appointment_rescheduled": _appointment_event(_rescheduled_text),
    "follow_up_booked": _follow_up_booked,
    "prescription_signed": _prescription_signed,
    "adherence_alert": _adherence_alert,
}


def process_events(db: Session, now: datetime | None = None, limit: int = 50) -> dict:
    """Handle every pending event once. Safe to call from several processes at the same time."""
    now = now or utcnow()
    ids = [
        row[0]
        for row in db.query(AgentEvent.event_id)
        .filter(AgentEvent.status == EventStatus.pending.value)
        .order_by(AgentEvent.created_at)
        .limit(limit)
        .all()
    ]
    counts: Counter = Counter()
    for event_id in ids:
        claimed = db.execute(
            update(AgentEvent)
            .where(AgentEvent.event_id == event_id, AgentEvent.status == EventStatus.pending.value)
            .values(status=EventStatus.processing.value, attempts=AgentEvent.attempts + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if claimed != 1:
            continue  # another scheduler took it
        event = db.get(AgentEvent, event_id)
        db.refresh(event)
        handler = HANDLERS.get(event.kind)
        try:
            outcome = handler(db, event, now) if handler else "nothing to do"
            event.status = EventStatus.done.value
            event.processed_at = now
            event.last_error = None
            db.commit()
            record_action(db, "coordinator", f"route:{event.kind}", outcome, appointment_id=event.appointment_id)
            counts["done"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the others
            db.rollback()
            event = db.get(AgentEvent, event_id)
            event.last_error = str(exc)[:200]
            event.status = EventStatus.pending.value if event.attempts < MAX_ATTEMPTS else EventStatus.failed.value
            db.commit()
            log.exception("Agent event %s failed", event_id)
            counts["retry" if event.status == EventStatus.pending.value else "failed"] += 1
    return dict(counts)


def process_all(db: Session, now: datetime | None = None, batch: int = 50, max_batches: int = 20) -> dict:
    """Keep handling batches until nothing is pending, so a burst of bookings never delays a prescription."""
    total: Counter = Counter()
    for _ in range(max_batches):
        counts = process_events(db, now, limit=batch)
        if not counts:
            break
        total.update(counts)
        if sum(counts.values()) < batch:
            break
    return dict(total)


def check_adherence(db: Session, newly_missed: Counter, now: datetime | None = None) -> int:
    """After a sweep: a patient who missed several doses today is flagged to the doctor (once a day)."""
    now = now or utcnow()
    queued = 0
    for prescription_id in newly_missed:
        missed = (
            db.query(MedicationDose)
            .filter(
                MedicationDose.prescription_id == prescription_id,
                MedicationDose.status == DoseStatus.missed.value,
                MedicationDose.due_at >= now - timedelta(hours=24),
            )
            .count()
        )
        if missed >= ADHERENCE_ALERT_AT:
            prescription = db.get(Prescription, prescription_id)
            event = emit(
                db, "adherence_alert", appointment_id=prescription.appointment_id if prescription else None,
                payload={"prescription_id": prescription_id, "missed": missed},
                dedupe_key=f"alert:{prescription_id}:{as_ist(now).date().isoformat()}",
            )
            queued += 1 if event else 0
    return queued


# ---------------------------------------------------------------------------
# what the doctor agent may look up
# ---------------------------------------------------------------------------

def patient_history(db: Session, appointment: AIAppointment, doctor: Doctor, limit: int = 5) -> list[dict]:
    """This doctor's earlier visits with the same patient (never another doctor's)."""
    if not appointment.patient_phone_no:
        return []
    earlier = (
        db.query(AIAppointment)
        .filter(
            AIAppointment.patient_phone_no == appointment.patient_phone_no,
            AIAppointment.doctor_id == doctor.doctor_id,
            AIAppointment.appointment_id != appointment.appointment_id,
            AIAppointment.appointment_datetime < appointment.appointment_datetime,
        )
        .order_by(AIAppointment.appointment_datetime.desc())
        .limit(limit)
        .all()
    )
    visits = []
    for visit in earlier:
        consultation = db.query(Consultation).filter(Consultation.appointment_id == visit.appointment_id).first()
        if consultation is None:
            continue
        note = (
            db.query(ConsultationNote)
            .filter(ConsultationNote.consultation_id == consultation.consultation_id,
                    ConsultationNote.status == NoteStatus.approved.value)
            .order_by(ConsultationNote.version.desc())
            .first()
        )
        signed = (
            db.query(Prescription)
            .filter(Prescription.consultation_id == consultation.consultation_id,
                    Prescription.status == PrescriptionStatus.signed.value)
            .order_by(Prescription.version.desc())
            .first()
        )
        if note is None and signed is None:
            continue
        visits.append({
            "appointment_id": visit.appointment_id,
            "date": as_ist(visit.appointment_datetime).date().isoformat(),
            "chief_complaint": note.chief_complaint if note else "",
            "assessment": note.assessment if note else "",
            "plan": note.plan if note else "",
            "medicines": [
                " ".join(p for p in (i.drug_name, i.strength) if p) for i in (signed.items if signed else [])
            ],
        })
    return visits
