"""Reminders and notices sent about an appointment (to the patient or the doctor)."""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.database import Base


class ReminderKind(str, enum.Enum):
    booked = "booked"                      # a new appointment exists (patient's own confirmation is recorded too)
    reminder_24h = "reminder_24h"
    reminder_2h = "reminder_2h"
    follow_up_booked = "follow_up_booked"
    cancelled = "cancelled"
    rescheduled = "rescheduled"
    medication = "medication"              # time to take one dose (see medication_doses)


class ReminderStatus(str, enum.Enum):
    pending = "pending"        # waiting for send_at
    sending = "sending"        # claimed by one scheduler; nobody else will send it
    sent = "sent"
    failed = "failed"          # gave up after the retry
    cancelled = "cancelled"    # the appointment was cancelled first
    skipped = "skipped"        # no longer useful (appointment started, no phone number, ...)


class Reminder(Base):
    """One message to one person about one appointment.

    The phone number is NOT stored here. It is looked up from the person's
    account at send time, so this table (and the audit trail) holds only ids.
    """

    __tablename__ = "reminders"

    reminder_id = Column(String, primary_key=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=False, index=True)
    recipient_type = Column(String, nullable=False)  # patient | doctor
    recipient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    kind = Column(String, nullable=False)
    dose_id = Column(String, nullable=False, default="", server_default="")   # set for kind=medication
    send_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String, nullable=False, default=ReminderStatus.pending.value)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String, nullable=True)
    details = Column(JSON, nullable=True)            # e.g. the old time of a rescheduled appointment
    message_text = Column(Text, nullable=True)       # what was (or in mock mode would have been) sent
    mode = Column(String, nullable=True)             # mock | live
    provider_message_id = Column(String, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("appointment_id", "recipient_type", "kind", "dose_id", name="uq_reminders_once"),
        Index("idx_reminders_due", "status", "send_at"),
    )
