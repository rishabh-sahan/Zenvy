from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.session import Session as SessionModel
from app.schemas.ai_appointment import AIAppointmentCreate, AIAppointmentResponse
from app.services.appointment_service import create_appointment, get_session_appointments

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
    return create_appointment(db, payload)


@router.get("/session/{session_id}", response_model=list[AIAppointmentResponse])
def list_appointments(session_id: str, db: Session = Depends(get_db)):
    return get_session_appointments(db, session_id)
