from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.ai_appointment import AppointmentStatus


# The hospital runs on IST. appointment_datetime lands in a TIMESTAMPTZ column,
# where Postgres reads a value with no offset as UTC -- so a naive "15:00" was
# being stored as 15:00 UTC and read back as 8:30 PM IST. Callers should send an
# explicit offset; this assumes IST for those that don't, rather than silently
# shifting the appointment by 5h30m.
HOSPITAL_TIMEZONE = ZoneInfo("Asia/Kolkata")


class AIAppointmentCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    patient_uhid: str = Field(..., min_length=1)
    doctor_name: str = Field(..., min_length=1)
    appointment_datetime: datetime
    status: AppointmentStatus = AppointmentStatus.pending
    booking_info: Optional[dict[str, Any]] = None
    appointment_metadata: Optional[dict[str, Any]] = None

    @field_validator("appointment_datetime")
    @classmethod
    def _assume_hospital_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=HOSPITAL_TIMEZONE)
        return value


class AIAppointmentResponse(BaseModel):
    appointment_id: str
    session_id: str
    patient_phone_no: Optional[str] = None
    patient_uhid: str
    doctor_name: str
    appointment_datetime: datetime
    status: AppointmentStatus
    booking_info: Optional[dict[str, Any]] = None
    appointment_metadata: Optional[dict[str, Any]] = None
    created_at: datetime

    # Outcome of the WhatsApp confirmation for this booking:
    #   "sent"    -- confirmation delivered to Twilio
    #   "failed"  -- Twilio rejected it or is misconfigured (see server logs)
    #   "skipped" -- no phone number linked to this session
    # Not a database column; set on the response only when an appointment is
    # created, which is why it is None when listing existing appointments.
    notification_status: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
