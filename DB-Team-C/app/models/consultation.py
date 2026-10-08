"""Consultation recording and scribe: consent, recording, transcript, note."""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class ConsultationStatus(str, enum.Enum):
    created = "created"                  # consent is on record, nothing recorded yet
    audio_uploaded = "audio_uploaded"    # encrypted recording stored
    transcribing = "transcribing"
    transcribed = "transcribed"
    summarising = "summarising"
    draft_ready = "draft_ready"          # an AI note is waiting for the doctor
    note_approved = "note_approved"      # the doctor signed a note
    failed = "failed"                    # see failure_reason
    recording_deleted = "recording_deleted"  # audio + transcript removed, notes kept


class NoteStatus(str, enum.Enum):
    draft = "draft"
    approved = "approved"


class ConsultationConsent(Base):
    """One consent decision. The newest row for an appointment is the current one."""

    __tablename__ = "consultation_consents"

    consent_id = Column(String, primary_key=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=False, index=True)
    patient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True, index=True)
    consent_given = Column(Boolean, nullable=False)
    # 'patient' (they answered themselves) or 'doctor_on_behalf' (verbal consent
    # recorded by the doctor, for in-person or phone visits).
    recorded_by = Column(String, nullable=False)
    recorded_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    message_version = Column(String, nullable=False)
    language = Column(String, nullable=False, default="en")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Consultation(Base):
    __tablename__ = "consultations"

    consultation_id = Column(String, primary_key=True, index=True)
    appointment_id = Column(
        String, ForeignKey("ai_appointments.appointment_id"), nullable=False, unique=True, index=True
    )
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=False, index=True)
    patient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True, index=True)
    mode = Column(String, nullable=False, default="in_person")  # online | phone | in_person
    status = Column(String, nullable=False, default=ConsultationStatus.created.value, index=True)
    failure_reason = Column(String, nullable=True)

    # Encrypted recording (AES-256-GCM). The path is relative to AUDIO_STORAGE_DIR.
    audio_path = Column(String, nullable=True)
    audio_bytes = Column(Integer, nullable=True)
    audio_seconds = Column(Float, nullable=True)
    audio_sha256 = Column(String, nullable=True)
    audio_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    audio_deleted_at = Column(DateTime(timezone=True), nullable=True)

    language_code = Column(String, nullable=True)  # dominant spoken language, e.g. hi-IN
    transcript_deleted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    turns = relationship(
        "ConsultationTurn", back_populates="consultation", cascade="all, delete-orphan",
        order_by="ConsultationTurn.seq",
    )
    notes = relationship(
        "ConsultationNote", back_populates="consultation", cascade="all, delete-orphan",
        order_by="ConsultationNote.version",
    )


class ConsultationTurn(Base):
    """One labelled stretch of speech, in the language it was spoken."""

    __tablename__ = "consultation_turns"

    turn_id = Column(String, primary_key=True)
    consultation_id = Column(String, ForeignKey("consultations.consultation_id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    speaker = Column(String, nullable=False, default="unknown")  # doctor | patient | unknown
    text = Column(Text, nullable=False)
    start_seconds = Column(Float, nullable=True)
    end_seconds = Column(Float, nullable=True)
    language_code = Column(String, nullable=True)
    edited = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("consultation_id", "seq", name="uq_consultation_turns_seq"),)

    consultation = relationship("Consultation", back_populates="turns")


class ConsultationNote(Base):
    """A version of the structured English note. Rows are never edited after
    they are written, except draft -> approved; any change is a new version."""

    __tablename__ = "consultation_notes"

    note_id = Column(String, primary_key=True)
    consultation_id = Column(String, ForeignKey("consultations.consultation_id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default=NoteStatus.draft.value)
    source = Column(String, nullable=False)  # ai | ai_regenerated | doctor_edit
    chief_complaint = Column(Text, nullable=False, default="")
    discussion_points = Column(JSON, nullable=False, default=list)
    assessment = Column(Text, nullable=False, default="")
    plan = Column(Text, nullable=False, default="")
    created_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    approved_by_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("consultation_id", "version", name="uq_consultation_notes_version"),)

    consultation = relationship("Consultation", back_populates="notes")
