import datetime as _dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ItemIn(BaseModel):
    drug_name: str = Field(min_length=1, max_length=120)
    strength: str | None = None
    form: str | None = None
    dose_text: str | None = None
    frequency_text: str | None = None
    dose_times: list[str] = Field(default_factory=list)
    food: str = "any"
    duration_days: int | None = None
    as_needed: bool = False
    instructions: str | None = None
    from_transcript: bool = False


class PrescriptionIn(BaseModel):
    items: list[ItemIn] = Field(max_length=20)
    source: Literal["doctor_edit", "transcript"] = "doctor_edit"


class AdherenceOut(BaseModel):
    taken: int = 0
    missed: int = 0
    scheduled: int = 0
    cancelled: int = 0


class ItemOut(BaseModel):
    item_id: str
    drug_name: str
    strength: str | None = None
    form: str | None = None
    dose_text: str | None = None
    frequency_text: str | None = None
    dose_times: list[str] = []
    food: str = "any"
    duration_days: int | None = None
    as_needed: bool = False
    instructions: str | None = None
    from_transcript: bool = False
    adherence: AdherenceOut | None = None


class PrescriptionOut(BaseModel):
    prescription_id: str
    consultation_id: str
    version: int
    status: str
    source: str
    signed_at: _dt.datetime | None = None
    items: list[ItemOut] = []


class PrescriptionStateOut(BaseModel):
    """What the doctor's Prescription card needs in one call."""

    current: PrescriptionOut | None = None      # the newest version (a draft, or the signed one)
    signed: PrescriptionOut | None = None       # the newest signed version, with adherence
    can_carry_forward: bool = False


class DoseOut(BaseModel):
    dose_id: str
    due_at: _dt.datetime
    status: str
    can_mark: bool = False


class MedicineOut(ItemOut):
    next_dose_at: _dt.datetime | None = None
    remaining_doses: int = 0
    today: list[DoseOut] = []


class PatientPrescriptionOut(BaseModel):
    prescription_id: str
    doctor_name: str | None = None
    signed_at: _dt.datetime | None = None
    items: list[MedicineOut] = []


class AgentMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: str
    kind: str
    text: str
    appointment_id: str | None = None
    created_at: _dt.datetime
    read_at: _dt.datetime | None = None


class MedicationsOut(BaseModel):
    prescriptions: list[PatientPrescriptionOut] = []
    messages: list[AgentMessageOut] = []


class TakenOut(BaseModel):
    taken: int
    medicines: list[str] = []


class ReadIn(BaseModel):
    ids: list[str] | None = None


class ActionIn(BaseModel):
    agent: Literal["patient", "doctor"]
    tool: str = Field(min_length=1, max_length=60)
    summary: str | None = Field(default=None, max_length=200)
    appointment_id: str | None = None
    auth_id: str | None = None          # the patient (the doctor is known from the token)
    result: Literal["ok", "refused", "error"] = "ok"


class VisitOut(BaseModel):
    appointment_id: str
    date: str
    chief_complaint: str = ""
    assessment: str = ""
    plan: str = ""
    medicines: list[str] = []
