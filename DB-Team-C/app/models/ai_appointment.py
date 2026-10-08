import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Index, JSON, String, text
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
    patient_phone_no = Column(String, ForeignKey("authentication.phone_no"), nullable=True, index=True)
    patient_uhid = Column(String, nullable=False, index=True)
    doctor_name = Column(String, nullable=False)
    appointment_datetime = Column(DateTime(timezone=True), nullable=False)
    status = Column(Enum(AppointmentStatus), nullable=False, default=AppointmentStatus.pending)
    booking_info = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    appointment_metadata = Column(JSON, nullable=True)
    # Added in migration 009: link to the real doctor and the locked slot.
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=True, index=True)
    slot_id = Column(String, ForeignKey("doctor_slots.slot_id"), nullable=True, index=True)
    appointment_type = Column(String, nullable=False, default="new", server_default="new")
    parent_appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=True)

    # Safety net behind the slot lock: a slot can have at most one non-cancelled
    # appointment, even if application code is bypassed.
    __table_args__ = (
        Index(
            "uq_ai_appointments_active_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("slot_id IS NOT NULL AND status <> 'cancelled'"),
            sqlite_where=text("slot_id IS NOT NULL AND status <> 'cancelled'"),
        ),
    )

    session = relationship("Session", back_populates="appointments")
    authentication = relationship("Authentication", back_populates="appointments")
