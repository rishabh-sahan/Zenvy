"""Live Assist: what the AI suggested to the doctor during a consultation, and what the doctor did."""

import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.db.database import Base


class SuggestionKind(str, enum.Enum):
    ask = "ask"              # a question the doctor may want to ask next
    red_flag = "red_flag"    # something that may need urgent attention
    history = "history"      # recalled from this doctor's earlier visits with the patient
    wrap_up = "wrap_up"      # something not covered yet
    consider = "consider"    # a gentle "keep in mind" (never a final diagnosis)


class SuggestionStatus(str, enum.Enum):
    shown = "shown"
    dismissed = "dismissed"
    pinned = "pinned"
    added = "added"          # the doctor sent it to the plan list
    asked = "asked"          # the doctor marked the question as asked


class LiveSuggestion(Base):
    __tablename__ = "live_suggestions"

    suggestion_id = Column(String, primary_key=True)
    consultation_id = Column(String, ForeignKey("consultations.consultation_id"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    evidence = Column(Text, nullable=True)           # the words from the conversation that triggered it
    source = Column(String, nullable=False, default="model")   # model | rule (a built-in safety rule)
    status = Column(String, nullable=False, default=SuggestionStatus.shown.value)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    acted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("consultation_id", "seq", name="uq_live_suggestions_seq"),
        Index("idx_live_suggestions_consultation", "consultation_id", "seq"),
    )
