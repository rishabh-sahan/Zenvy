"""A follow-up visit suggested from a consultation note and, once the doctor approves, booked."""

import enum

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, Time
from sqlalchemy.sql import func

from app.db.database import Base


class FollowUpStatus(str, enum.Enum):
    suggested = "suggested"   # found in the note's plan; waiting for the doctor
    booked = "booked"         # an appointment now exists (new_appointment_id)
    declined = "declined"     # the doctor removed it
    failed = "failed"         # approved, but no free slot could be found


class FollowUp(Base):
    __tablename__ = "follow_ups"

    follow_up_id = Column(String, primary_key=True)
    # One follow-up record per consultation; it is updated as the note changes.
    consultation_id = Column(String, ForeignKey("consultations.consultation_id"), nullable=False, unique=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=False, index=True)
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=False, index=True)
    status = Column(String, nullable=False, default=FollowUpStatus.suggested.value)
    interval_days = Column(Integer, nullable=True)
    source_text = Column(Text, nullable=True)       # the sentence in the plan it came from
    suggested_date = Column(Date, nullable=True)    # IST
    suggested_time = Column(Time, nullable=True)    # IST
    edited_by_doctor = Column(Boolean, nullable=False, default=False)
    new_appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=True)
    failure_reason = Column(String, nullable=True)
    decided_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
