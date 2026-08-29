from sqlalchemy import Column, String, DateTime, Enum, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.database import Base
import enum

class SessionStatus(str, enum.Enum):
    active = "active"
    completed = "completed"

class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(String, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    uhid = Column(String, nullable=True, index=True)
    channel = Column(String, nullable=False)
    language = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(SessionStatus), nullable=False, default=SessionStatus.active)
    session_metadata = Column(JSON, nullable=True)

    turns = relationship("ConversationTurn", back_populates="session", cascade="all, delete-orphan")
    appointments = relationship("AIAppointment", back_populates="session", cascade="all, delete-orphan")
    escalations = relationship("Escalation", back_populates="session", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="session", cascade="all, delete-orphan")
