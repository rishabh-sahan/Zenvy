from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_optional_staff
from app.db.deps import get_db
from app.models.ai_appointment import AIAppointment
from app.models.session import Session as SessionModel
from app.models.authentication import Authentication
from app.schemas.ai_appointment import (
    AIAppointmentCreate,
    AIAppointmentResponse,
    AppointmentCancelRequest,
    AppointmentRescheduleRequest,
)
from app.services import reminder_service
from app.services.appointment_service import (
    CannotChange,
    cancel_appointment,
    create_appointment,
    get_session_appointments,
    reschedule_appointment,
)
from app.services.slot_service import SlotNotFoundError, SlotUnavailableError
from app.services.whatsapp_service import send_appointment_notification
from app.services.audit_service import write_audit_log

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])


@router.post("", response_model=AIAppointmentResponse, status_code=status.HTTP_201_CREATED)
def create_appointment_endpoint(payload: AIAppointmentCreate, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if not payload.patient_uhid.strip() or not (payload.doctor_name or payload.slot_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_uhid and doctor_name are required",
        )
    if payload.slot_id is None and settings.REQUIRE_SLOT_FOR_BOOKING:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="slot_id is required: hold a slot first",
        )
    authentication = (
        db.query(Authentication)
        .filter(
            (Authentication.auth_id == session.user_id)
            | (Authentication.phone_no == session.user_id)
        )
        .first()
    )
    try:
        appointment = create_appointment(
            db,
            payload,
            patient_phone_no=authentication.phone_no if authentication is not None else None,
        )
    except SlotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found") from exc
    except SlotUnavailableError as exc:
        # Not held by this session, or someone else took it: the caller should
        # offer the patient other times.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slot_unavailable") from exc
    write_audit_log(
        db,
        action="create_appointment",
        actor="conversation-service",
        session_id=appointment.session_id,
        user_id=session.user_id,
        after_value={
            "appointment_id": appointment.appointment_id,
            "doctor_name": appointment.doctor_name,
            "doctor_id": appointment.doctor_id,
            "slot_id": appointment.slot_id,
            "status": appointment.status.value,
        },
    )
    try:
        reminder_service.schedule_for_appointment(db, appointment)
    except Exception:  # noqa: BLE001 - never fail a booking because reminders could not be queued
        db.rollback()
    if authentication is not None:
        try:
            send_appointment_notification(
                authentication.phone_no,
                appointment,
                patient_name=authentication.name,
            )
            reminder_service.record_booking_confirmation(db, appointment, ok=True)
        except RuntimeError as exc:
            reminder_service.record_booking_confirmation(db, appointment, ok=False, error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Appointment saved, but WhatsApp notification failed",
            ) from exc
    return appointment


def _who_may_change(db: Session, appointment: AIAppointment, staff, session_id: str | None, auth_id: str | None) -> str:
    """Return who is asking ("staff" or "patient"), or refuse with 403.

    Allowed: hospital staff (bearer token); the patient the appointment belongs
    to (their auth_id); or the conversation that booked it (session_id).
    """
    if staff is not None:
        return "staff"
    patient = (
        db.query(Authentication).filter(Authentication.phone_no == appointment.patient_phone_no).first()
        if appointment.patient_phone_no
        else None
    )
    if auth_id and patient is not None and patient.auth_id == auth_id:
        return "patient"
    if session_id and session_id == appointment.session_id:
        return "patient"
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only staff, the patient, or the booking session can change this appointment",
    )


def _patient_auth_id(db: Session, appointment: AIAppointment) -> str | None:
    if not appointment.patient_phone_no:
        return None
    patient = db.query(Authentication).filter(Authentication.phone_no == appointment.patient_phone_no).first()
    return patient.auth_id if patient else None


@router.post("/{appointment_id}/cancel", response_model=AIAppointmentResponse)
def cancel_appointment_endpoint(
    appointment_id: str,
    payload: AppointmentCancelRequest | None = None,
    db: Session = Depends(get_db),
    staff: Authentication | None = Depends(get_optional_staff),
):
    """Cancel an appointment and free its slot.

    Allowed for hospital staff (bearer token), for the patient (auth_id), or for
    the conversation that made the booking (session_id). Not possible once the
    appointment has started or already has a consultation.
    """
    appointment = (
        db.query(AIAppointment).filter(AIAppointment.appointment_id == appointment_id).first()
    )
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    payload = payload or AppointmentCancelRequest()
    who = _who_may_change(db, appointment, staff, payload.session_id, payload.auth_id)
    was_cancelled = appointment.status.value == "cancelled"
    before = {"status": appointment.status.value, "slot_id": appointment.slot_id}
    try:
        appointment = cancel_appointment(
            db, appointment, cancelled_by=who, reason=payload.reason or f"{who}_request"
        )
    except CannotChange as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
    if not was_cancelled:
        write_audit_log(
            db,
            action="cancel_appointment",
            actor=f"staff:{staff.auth_id}" if staff else "conversation-service",
            session_id=appointment.session_id,
            user_id=_patient_auth_id(db, appointment),
            before_value=before,
            after_value={
                "appointment_id": appointment.appointment_id,
                "status": "cancelled",
                "cancelled_by": who,
                "reason": appointment.cancel_reason,
            },
        )
    return appointment


@router.post("/{appointment_id}/reschedule", response_model=AIAppointmentResponse)
def reschedule_appointment_endpoint(
    appointment_id: str,
    payload: AppointmentRescheduleRequest,
    db: Session = Depends(get_db),
    staff: Authentication | None = Depends(get_optional_staff),
):
    """Move an appointment to another slot of the same doctor.

    The new slot is booked and the old one released in a single step, so the
    patient is never left without an appointment. Returns the NEW appointment.
    """
    appointment = (
        db.query(AIAppointment).filter(AIAppointment.appointment_id == appointment_id).first()
    )
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    who = _who_may_change(db, appointment, staff, payload.session_id, payload.auth_id)
    if payload.session_id and db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    try:
        replacement = reschedule_appointment(
            db, appointment, payload.slot_id, session_id=payload.session_id, cancelled_by=who
        )
    except CannotChange as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
    except SlotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found") from exc
    except SlotUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slot_unavailable") from exc
    write_audit_log(
        db,
        action="reschedule_appointment",
        actor=f"staff:{staff.auth_id}" if staff else "conversation-service",
        session_id=replacement.session_id,
        user_id=_patient_auth_id(db, replacement),
        before_value={"appointment_id": appointment.appointment_id, "slot_id": appointment.slot_id},
        after_value={
            "appointment_id": replacement.appointment_id,
            "slot_id": replacement.slot_id,
            "rescheduled_by": who,
        },
    )
    return replacement


@router.get("/session/{session_id}", response_model=list[AIAppointmentResponse])
def list_appointments(session_id: str, db: Session = Depends(get_db)):
    return get_session_appointments(db, session_id)
