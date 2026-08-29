import uuid

from sqlalchemy.orm import Session

from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.schemas.ai_appointment import AIAppointmentCreate


def create_appointment(db: Session, payload: AIAppointmentCreate):
    status = payload.status if isinstance(payload.status, AppointmentStatus) else AppointmentStatus(payload.status)
    appointment = AIAppointment(
        appointment_id=str(uuid.uuid4()),
        session_id=payload.session_id,
        patient_uhid=payload.patient_uhid.strip(),
        doctor_name=payload.doctor_name.strip(),
        appointment_datetime=payload.appointment_datetime,
        status=status,
        booking_info=payload.booking_info,
        appointment_metadata=payload.appointment_metadata,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def get_session_appointments(db: Session, session_id: str):
    return db.query(AIAppointment).filter(AIAppointment.session_id == session_id).all()
