from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DoctorResponse(BaseModel):
    doctor_id: str
    name: str
    specialty: str
    hospital_id: str
    hospital_name: str
    city: str
    slot_minutes: int


class SlotResponse(BaseModel):
    """A slot. Times are returned in IST (+05:30)."""

    slot_id: str
    doctor_id: str
    slot_start: datetime
    slot_end: datetime
    status: str
    held_until: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SlotHoldRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class SlotReleaseResponse(BaseModel):
    released: bool
