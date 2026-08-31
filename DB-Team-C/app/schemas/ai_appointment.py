from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai_appointment import AppointmentStatus


class AIAppointmentCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    patient_uhid: str = Field(..., min_length=1)
    doctor_name: str = Field(..., min_length=1)
    appointment_datetime: datetime
    status: AppointmentStatus = AppointmentStatus.pending
    booking_info: Optional[dict[str, Any]] = None
    appointment_metadata: Optional[dict[str, Any]] = None


class AIAppointmentResponse(BaseModel):
    appointment_id: str
    session_id: str
    patient_uhid: str
    doctor_name: str
    appointment_datetime: datetime
    status: AppointmentStatus
    booking_info: Optional[dict[str, Any]] = None
    appointment_metadata: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
