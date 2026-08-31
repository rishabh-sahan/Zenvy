import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class EscalationStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"


class Escalation(Base):
    __tablename__ = "escalations"

    escalation_id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.session_id"), nullable=False, index=True)
    reason = Column(String, nullable=False)
    emergency_type = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    status = Column(Enum(EscalationStatus), nullable=False, default=EscalationStatus.open)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    handled_by = Column(String, nullable=True)
    escalation_metadata = Column(JSON, nullable=True)

    session = relationship("Session", back_populates="escalations")
