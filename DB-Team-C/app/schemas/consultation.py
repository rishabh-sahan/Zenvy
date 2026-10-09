import datetime as _dt
from datetime import date, datetime, time
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConsentRequest(BaseModel):
    consent_given: bool
    # Patients prove who they are with their auth_id; a doctor sends a staff token instead.
    auth_id: Optional[str] = None
    language: str = "en"


class ConsentResponse(BaseModel):
    appointment_id: str
    state: Literal["none", "granted", "declined"]
    recorded_by: Optional[str] = None
    message_version: Optional[str] = None
    recorded_at: Optional[datetime] = None


class ConsentMessageResponse(BaseModel):
    version: str
    language: str
    message: str


class DoctorAppointmentResponse(BaseModel):
    appointment_id: str
    appointment_datetime: datetime
    status: str
    patient_label: str
    consent_state: str
    consent_recorded_by: Optional[str] = None
    appointment_type: str = "new"
    consultation_id: Optional[str] = None
    consultation_status: Optional[str] = None
    note_status: Optional[str] = None


class PatientAppointmentResponse(BaseModel):
    appointment_id: str
    doctor_id: Optional[str] = None
    appointment_type: str = "new"
    can_change: bool = True            # False once it has started or has a consultation
    change_blocker: Optional[str] = None
    doctor_name: str
    hospital_name: Optional[str] = None
    appointment_datetime: datetime
    status: str
    consent_state: str


class ConsultationCreate(BaseModel):
    appointment_id: str = Field(..., min_length=1)
    mode: Literal["online", "phone", "in_person"] = "in_person"


class TurnIn(BaseModel):
    speaker: Literal["doctor", "patient", "unknown"] = "unknown"
    text: str = Field(..., min_length=1, max_length=8000)
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    language_code: Optional[str] = None


class TranscriptIn(BaseModel):
    turns: list[TurnIn] = Field(..., min_length=1, max_length=5000)
    language_code: Optional[str] = None


class TurnPatch(BaseModel):
    speaker: Optional[Literal["doctor", "patient", "unknown"]] = None
    text: Optional[str] = Field(None, min_length=1, max_length=8000)


class StatusPatch(BaseModel):
    status: str
    failure_reason: Optional[str] = Field(None, max_length=500)


class NoteIn(BaseModel):
    chief_complaint: str = ""
    discussion_points: list[str] = Field(default_factory=list)
    assessment: str = ""
    plan: str = ""
    source: Literal["ai", "ai_regenerated", "doctor_edit"] = "doctor_edit"


class TurnOut(BaseModel):
    turn_id: str
    seq: int
    speaker: str
    text: str
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    language_code: Optional[str] = None
    edited: bool = False

    model_config = ConfigDict(from_attributes=True)


class NoteOut(BaseModel):
    note_id: str
    version: int
    status: str
    source: str
    chief_complaint: str
    discussion_points: list[str]
    assessment: str
    plan: str
    created_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class ConsultationResponse(BaseModel):
    consultation_id: str
    appointment_id: str
    doctor_id: str
    mode: str
    status: str
    failure_reason: Optional[str] = None
    audio_seconds: Optional[float] = None
    has_recording: bool = False
    language_code: Optional[str] = None
    consent_state: str = "none"
    created_at: Optional[datetime] = None


class ConsultationDetailResponse(ConsultationResponse):
    turns: list[TurnOut] = []
    notes: list[NoteOut] = []


class FollowUpIn(BaseModel):
    # (The field names shadow the type names, so the types are spelled out through
    # the module: otherwise "time" would be read as the default value None.)
    date: _dt.date
    time: Optional[_dt.time] = None


class FollowUpOut(BaseModel):
    status: str                                   # suggested | booked | declined | failed
    interval_days: Optional[int] = None
    source_text: Optional[str] = None             # the sentence in the plan it came from
    suggested_date: Optional[date] = None
    suggested_time: Optional[str] = None          # HH:MM
    edited_by_doctor: bool = False
    failure_reason: Optional[str] = None
    new_appointment_id: Optional[str] = None
    new_appointment_datetime: Optional[datetime] = None   # IST, once booked
    preview_datetime: Optional[datetime] = None           # IST: the slot a booking would take now


class NoteApproveOut(NoteOut):
    """The approved note, and what happened to its follow-up (if it had one)."""

    follow_up: Optional[FollowUpOut] = None


class HistoryEvent(BaseModel):
    at: Optional[datetime] = None
    kind: str
    detail: str


class AppointmentHistoryOut(BaseModel):
    appointment_id: str
    appointment_type: str
    status: str
    appointment_datetime: datetime
    cancelled_by: Optional[str] = None
    cancel_reason: Optional[str] = None
    rescheduled_from_id: Optional[str] = None
    rescheduled_to_id: Optional[str] = None
    parent_appointment_id: Optional[str] = None
    follow_up_appointment_id: Optional[str] = None
    consent_state: str
    consultation_status: Optional[str] = None
    reminders: list[dict] = []
    events: list[HistoryEvent] = []
