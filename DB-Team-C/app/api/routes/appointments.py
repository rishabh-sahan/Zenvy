from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.session import Session as SessionModel
from app.models.authentication import Authentication
from app.schemas.ai_appointment import AIAppointmentCreate, AIAppointmentResponse
from app.services.appointment_service import create_appointment, get_session_appointments
from app.services.whatsapp_service import send_appointment_notification
from app.services.audit_service import write_audit_log

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])


@router.post("", response_model=AIAppointmentResponse, status_code=status.HTTP_201_CREATED)
def create_appointment_endpoint(payload: AIAppointmentCreate, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if not payload.patient_uhid.strip() or not payload.doctor_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_uhid and doctor_name are required",
        )
    authentication = (
        db.query(Authentication)
        .filter(
            (Authentication.auth_id == session.user_id)
            | (Authentication.phone_no == session.user_id)
        )
        .first()
    )
    appointment = create_appointment(
        db,
        payload,
        patient_phone_no=authentication.phone_no if authentication is not None else None,
    )
    write_audit_log(
        db,
        action="create_appointment",
        actor="conversation-service",
        session_id=appointment.session_id,
        user_id=session.user_id,
        after_value={
            "appointment_id": appointment.appointment_id,
            "doctor_name": appointment.doctor_name,
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


@router.get("/session/{session_id}", response_model=list[AIAppointmentResponse])
def list_appointments(session_id: str, db: Session = Depends(get_db)):
    return get_session_appointments(db, session_id)
