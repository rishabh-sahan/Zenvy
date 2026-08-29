from sqlalchemy import Column, DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    audit_id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.session_id"), nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    relevant_metadata = Column(JSON, nullable=True)
    before_value = Column(JSON, nullable=True)
    after_value = Column(JSON, nullable=True)

    session = relationship("Session", back_populates="audit_logs")
