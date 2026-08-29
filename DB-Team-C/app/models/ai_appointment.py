import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class AppointmentStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"


class AIAppointment(Base):
    __tablename__ = "ai_appointments"

    appointment_id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.session_id"), nullable=False, index=True)
    patient_uhid = Column(String, nullable=False, index=True)
    doctor_name = Column(String, nullable=False)
    appointment_datetime = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(AppointmentStatus), nullable=False, default=AppointmentStatus.pending)
    booking_info = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    appointment_metadata = Column(JSON, nullable=True)

    session = relationship("Session", back_populates="appointments")
