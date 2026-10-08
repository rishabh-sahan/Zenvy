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
)
from app.services.appointment_service import (
    cancel_appointment,
    create_appointment,
    get_session_appointments,
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
    if authentication is not None:
        try:
            send_appointment_notification(
                authentication.phone_no,
                appointment,
                patient_name=authentication.name,
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Appointment saved, but WhatsApp notification failed",
            ) from exc
    return appointment


@router.post("/{appointment_id}/cancel", response_model=AIAppointmentResponse)
def cancel_appointment_endpoint(
    appointment_id: str,
    payload: AppointmentCancelRequest | None = None,
    db: Session = Depends(get_db),
    staff: Authentication | None = Depends(get_optional_staff),
):
    """Cancel an appointment and free its slot.

    Allowed for hospital staff (bearer token), or for the booking conversation
    itself (the session_id that made the booking).
    """
    appointment = (
        db.query(AIAppointment).filter(AIAppointment.appointment_id == appointment_id).first()
    )
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found")
    caller_session = payload.session_id if payload else None
    if staff is None and caller_session != appointment.session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only staff or the booking session can cancel this appointment",
        )
    was_cancelled = appointment.status.value == "cancelled"
    before = {"status": appointment.status.value, "slot_id": appointment.slot_id}
    appointment = cancel_appointment(db, appointment)
    if not was_cancelled:
        patient = (
            db.query(Authentication)
            .filter(Authentication.phone_no == appointment.patient_phone_no)
            .first()
            if appointment.patient_phone_no
            else None
        )
        write_audit_log(
            db,
            action="cancel_appointment",
            actor=f"staff:{staff.auth_id}" if staff else "conversation-service",
            session_id=appointment.session_id,
            user_id=patient.auth_id if patient else None,
            before_value=before,
            after_value={"appointment_id": appointment.appointment_id, "status": "cancelled"},
        )
    return appointment


@router.get("/session/{session_id}", response_model=list[AIAppointmentResponse])
def list_appointments(session_id: str, db: Session = Depends(get_db)):
    return get_session_appointments(db, session_id)
