from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.ai_appointment import AppointmentStatus


class AIAppointmentCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    patient_uhid: str = Field(..., min_length=1)
    # With slot_id the doctor and time come from the locked slot, so they are
    # optional. Without slot_id (older callers) they are both required.
    doctor_name: Optional[str] = None
    appointment_datetime: Optional[datetime] = None
    slot_id: Optional[str] = None
    appointment_type: str = "new"
    parent_appointment_id: Optional[str] = None
    status: AppointmentStatus = AppointmentStatus.pending
    booking_info: Optional[dict[str, Any]] = None
    appointment_metadata: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _doctor_and_time_without_slot(self):
        if self.slot_id is None:
            if not (self.doctor_name and self.doctor_name.strip()):
                raise ValueError("doctor_name is required when slot_id is not given")
            if self.appointment_datetime is None:
                raise ValueError("appointment_datetime is required when slot_id is not given")
        return self


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
    doctor_id: Optional[str] = None
    slot_id: Optional[str] = None
    appointment_type: str = "new"
    parent_appointment_id: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AppointmentCancelRequest(BaseModel):
    session_id: Optional[str] = None
