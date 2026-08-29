from sqlalchemy import Column, String, DateTime, ForeignKey, Integer, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.db.database import Base

class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    turn_id = Column(String, primary_key=True, index=True)
    session_id = Column(String, ForeignKey("sessions.session_id"), nullable=False, index=True)
    speaker = Column(String, nullable=False)
    content = Column(String, nullable=False)
    input_text = Column(String, nullable=True)
    response_text = Column(String, nullable=True)
    language = Column(String, nullable=False)
    sequence_number = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    turn_metadata = Column(JSON, nullable=True)

    session = relationship("Session", back_populates="turns")
