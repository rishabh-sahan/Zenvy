import logging
import uuid

from sqlalchemy.orm import Session

from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.consultation import Consultation
from app.models.doctor_slot import DoctorSlot
from app.schemas.ai_appointment import AIAppointmentCreate
from app.services import reminder_service
from app.services.slot_service import (
    SlotNotFoundError,
    as_utc,
    claim_held_slot_for_booking,
    claim_slot_for_booking,
    free_booked_slot,
    utcnow,
)

log = logging.getLogger("zenvy.appointments")


class CannotChange(Exception):
    """An appointment cannot be cancelled or moved. ``code`` says why:

    cancelled         it is already cancelled
    already_started   its time has passed (or it was completed)
    has_consultation  a consultation was already started for it
    not_reschedulable it was not booked through a doctor's slot
    different_doctor  a reschedule must stay with the same doctor
    same_slot         that is the time it already has
    """

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def create_appointment(db: Session, payload: AIAppointmentCreate, patient_phone_no: str | None = None):
    """Create an appointment.

    With ``payload.slot_id`` the slot must be held by ``payload.session_id``;
    the slot is marked booked and the appointment inserted in ONE transaction,
    so either both happen or neither does. Raises SlotUnavailableError /
    SlotNotFoundError (from slot_service) if the slot cannot be taken.
    """
    status = payload.status if isinstance(payload.status, AppointmentStatus) else AppointmentStatus(payload.status)
    appointment_id = str(uuid.uuid4())
    doctor_id = None
    doctor_name = (payload.doctor_name or "").strip()
    appointment_datetime = payload.appointment_datetime
    booking_info = payload.booking_info

    if payload.slot_id:
        slot = db.get(DoctorSlot, payload.slot_id)
        if slot is None:
            raise SlotNotFoundError(payload.slot_id)
        # The slot is the source of truth for who and when.
        doctor_id = slot.doctor_id
        doctor_name = slot.doctor.name
        appointment_datetime = slot.slot_start
        # Tell the patient where to go (shown as "location" in the WhatsApp confirmation).
        booking_info = {"location": slot.doctor.hospital.name, **(payload.booking_info or {})}

    appointment = AIAppointment(
        appointment_id=appointment_id,
        session_id=payload.session_id,
        patient_phone_no=patient_phone_no,
        patient_uhid=payload.patient_uhid.strip(),
        doctor_name=doctor_name,
        appointment_datetime=appointment_datetime,
        status=status,
        booking_info=booking_info,
        appointment_metadata=payload.appointment_metadata,
        doctor_id=doctor_id,
        slot_id=payload.slot_id,
        appointment_type=payload.appointment_type,
        parent_appointment_id=payload.parent_appointment_id,
    )
    try:
        if payload.slot_id:
            claim_held_slot_for_booking(db, payload.slot_id, payload.session_id, appointment_id)
        db.add(appointment)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(appointment)
    return appointment


def get_session_appointments(db: Session, session_id: str):
    return db.query(AIAppointment).filter(AIAppointment.session_id == session_id).all()


# ---------------------------------------------------------------------------
# changing an appointment
# ---------------------------------------------------------------------------

def has_consultation(db: Session, appointment_id: str) -> bool:
    return db.query(Consultation.consultation_id).filter(Consultation.appointment_id == appointment_id).first() is not None


def change_blocker(db: Session, appointment: AIAppointment, now=None) -> str | None:
    """Why this appointment cannot be cancelled or moved, or None if it can."""
    now = now or utcnow()
    if appointment.status == AppointmentStatus.cancelled:
        return "cancelled"
    if appointment.status == AppointmentStatus.completed or as_utc(appointment.appointment_datetime) <= now:
        return "already_started"
    if has_consultation(db, appointment.appointment_id):
        return "has_consultation"
    return None


def _mark_cancelled(appointment: AIAppointment, cancelled_by: str, reason: str) -> None:
    appointment.status = AppointmentStatus.cancelled
    appointment.cancelled_at = utcnow()
    appointment.cancelled_by = cancelled_by
    appointment.cancel_reason = reason


def cancel_appointment(
    db: Session, appointment: AIAppointment, cancelled_by: str = "system", reason: str = "cancelled"
) -> AIAppointment:
    """Cancel an appointment and put its slot back on offer.

    Idempotent for an appointment that is already cancelled. Refused (CannotChange)
    once the appointment has started or has a consultation. Pending reminders are
    stopped and both people are sent a cancellation notice.
    """
    if appointment.status == AppointmentStatus.cancelled:
        return appointment
    blocker = change_blocker(db, appointment)
    if blocker:
        raise CannotChange(blocker)

    _mark_cancelled(appointment, cancelled_by, reason)
    if appointment.slot_id:
        free_booked_slot(db, appointment.slot_id, appointment.appointment_id)
    db.commit()
    db.refresh(appointment)

    try:
        reminder_service.cancel_pending(db, appointment.appointment_id)
        reminder_service.notify_change(db, appointment, "cancelled")
    except Exception:  # noqa: BLE001 - the cancellation itself is already done
        log.exception("Could not queue cancellation notices")
        db.rollback()
    return appointment


def reschedule_appointment(
    db: Session,
    appointment: AIAppointment,
    new_slot_id: str,
    session_id: str | None = None,
    cancelled_by: str = "patient",
) -> AIAppointment:
    """Move an appointment to another slot of the SAME doctor, in one transaction.

    The new slot is claimed, the new appointment created, and the old appointment
    cancelled (its slot freed) together: either all of it happens or none, so the
    patient is never left without an appointment. Returns the NEW appointment.
    Raises CannotChange, SlotNotFoundError or SlotUnavailableError.
    """
    blocker = change_blocker(db, appointment)
    if blocker:
        raise CannotChange(blocker)
    if not appointment.doctor_id or not appointment.slot_id:
        raise CannotChange("not_reschedulable")

    new_slot = db.get(DoctorSlot, new_slot_id)
    if new_slot is None:
        raise SlotNotFoundError(new_slot_id)
    if new_slot.doctor_id != appointment.doctor_id:
        raise CannotChange("different_doctor")
    if new_slot.slot_id == appointment.slot_id:
        raise CannotChange("same_slot")

    claim_session = session_id or appointment.session_id
    old_datetime = appointment.appointment_datetime
    new_id = str(uuid.uuid4())
    replacement = AIAppointment(
        appointment_id=new_id,
        session_id=claim_session,
        patient_phone_no=appointment.patient_phone_no,
        patient_uhid=appointment.patient_uhid,
        doctor_name=appointment.doctor_name,
        appointment_datetime=new_slot.slot_start,
        status=AppointmentStatus.confirmed,
        booking_info={**(appointment.booking_info or {}), "location": new_slot.doctor.hospital.name},
        appointment_metadata=appointment.appointment_metadata,
        doctor_id=appointment.doctor_id,
        slot_id=new_slot.slot_id,
        appointment_type=appointment.appointment_type,
        parent_appointment_id=appointment.parent_appointment_id,
        rescheduled_from_id=appointment.appointment_id,
    )
    try:
        claim_slot_for_booking(db, new_slot_id, claim_session, new_id)
        db.add(replacement)
        _mark_cancelled(appointment, cancelled_by, "rescheduled")
        free_booked_slot(db, appointment.slot_id, appointment.appointment_id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(replacement)

    try:
        from app.services import consultation_service

        consultation_service.carry_over_consent(db, appointment, replacement)
        reminder_service.cancel_pending(db, appointment.appointment_id)
        reminder_service.schedule_for_appointment(db, replacement)
        reminder_service.notify_change(
            db, replacement, "rescheduled", {"old_datetime": as_utc(old_datetime).isoformat()}
        )
    except Exception:  # noqa: BLE001 - the move itself is already done
        log.exception("Could not finish rescheduling follow-ups")
        db.rollback()
    return replacement
