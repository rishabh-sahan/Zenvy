"""Prescriptions: versioned medicine lists, the doses they schedule, and whether each was taken."""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class PrescriptionStatus(str, enum.Enum):
    draft = "draft"            # being prepared (AI draft or the doctor's edit); not shown to the patient
    signed = "signed"          # the doctor signed it; locked; doses are scheduled
    superseded = "superseded"  # replaced by a newer signed version


class DoseStatus(str, enum.Enum):
    scheduled = "scheduled"
    taken = "taken"
    missed = "missed"          # nobody marked it taken within MISSED_AFTER_HOURS
    cancelled = "cancelled"    # the prescription was replaced


class Prescription(Base):
    __tablename__ = "prescriptions"

    prescription_id = Column(String, primary_key=True)
    consultation_id = Column(String, ForeignKey("consultations.consultation_id"), nullable=False, index=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=False)
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=False, index=True)
    patient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default=PrescriptionStatus.draft.value)
    source = Column(String, nullable=False, default="doctor_edit")   # transcript | doctor_edit | carried_forward
    created_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    signed_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    signed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items = relationship(
        "PrescriptionItem", back_populates="prescription", cascade="all, delete-orphan",
        order_by="PrescriptionItem.position",
    )

    __table_args__ = (
        UniqueConstraint("consultation_id", "version", name="uq_prescriptions_version"),
        Index("idx_prescriptions_patient", "patient_auth_id", "status"),
    )


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    item_id = Column(String, primary_key=True)
    prescription_id = Column(String, ForeignKey("prescriptions.prescription_id"), nullable=False, index=True)
    position = Column(Integer, nullable=False)
    drug_name = Column(String, nullable=False)          # as spoken / written; never "corrected" by the system
    strength = Column(String, nullable=True)            # "500 mg"
    form = Column(String, nullable=True)                # tablet, syrup, ...
    dose_text = Column(String, nullable=True)           # "1 tablet"
    frequency_text = Column(String, nullable=True)      # "1-0-1" or "twice a day"
    dose_times = Column(JSON, nullable=False, default=list)   # ["08:00", "21:00"] (IST)
    food = Column(String, nullable=False, default="any")      # before | after | with | any
    duration_days = Column(Integer, nullable=True)
    as_needed = Column(Boolean, nullable=False, default=False)
    instructions = Column(Text, nullable=True)
    from_transcript = Column(Boolean, nullable=False, default=False)   # drafted by the AI: verify

    prescription = relationship("Prescription", back_populates="items")


class MedicationDose(Base):
    __tablename__ = "medication_doses"

    dose_id = Column(String, primary_key=True)
    prescription_id = Column(String, ForeignKey("prescriptions.prescription_id"), nullable=False)
    item_id = Column(String, ForeignKey("prescription_items.item_id"), nullable=False)
    patient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default=DoseStatus.scheduled.value)
    taken_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("item_id", "due_at", name="uq_medication_doses_slot"),
        Index("idx_medication_doses_patient", "patient_auth_id", "status", "due_at"),
        Index("idx_medication_doses_due", "status", "due_at"),
    )
