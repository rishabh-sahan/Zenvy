import uuid

from sqlalchemy.orm import Session

from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.doctor_slot import DoctorSlot
from app.schemas.ai_appointment import AIAppointmentCreate
from app.services.slot_service import (
    SlotNotFoundError,
    claim_held_slot_for_booking,
    free_booked_slot,
)


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


def cancel_appointment(db: Session, appointment: AIAppointment) -> AIAppointment:
    """Cancel an appointment and put its slot back on offer. Idempotent."""
    if appointment.status == AppointmentStatus.cancelled:
        return appointment
    appointment.status = AppointmentStatus.cancelled
    if appointment.slot_id:
        free_booked_slot(db, appointment.slot_id, appointment.appointment_id)
    db.commit()
    db.refresh(appointment)
    return appointment
