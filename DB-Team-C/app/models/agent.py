"""The three-agent system: events for the coordinator, messages between agents, and an audit trail."""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.db.database import Base


class EventStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"   # claimed by one scheduler; nobody else will handle it
    done = "done"
    failed = "failed"


class AgentEvent(Base):
    """Something happened that the coordinator should route (a booking, a signed prescription, ...)."""

    __tablename__ = "agent_events"

    event_id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=True)
    payload = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default=EventStatus.pending.value)
    attempts = Column(Integer, nullable=False, default=0)
    dedupe_key = Column(String, nullable=True, unique=True)   # the same fact is never queued twice
    last_error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_agent_events_pending", "status", "created_at"),)


class AgentMessage(Base):
    """A note from the coordinator to a doctor or a patient (shown in their dashboard)."""

    __tablename__ = "agent_messages"

    message_id = Column(String, primary_key=True)
    recipient_type = Column(String, nullable=False)   # doctor | patient
    recipient_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=False, index=True)
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=True)
    kind = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at = Column(DateTime(timezone=True), nullable=True)


class AgentAction(Base):
    """What an agent did. Holds ids and short summaries only - never a phone number."""

    __tablename__ = "agent_actions"

    action_id = Column(String, primary_key=True)
    agent = Column(String, nullable=False)            # patient | doctor | coordinator
    actor_auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True)
    tool = Column(String, nullable=False)
    summary = Column(String, nullable=True)
    result = Column(String, nullable=False, default="ok")   # ok | refused | error
    appointment_id = Column(String, ForeignKey("ai_appointments.appointment_id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
