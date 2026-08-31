from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ConversationTurnCreate(BaseModel):
    speaker: Literal["user", "assistant", "system"]
    content: str = Field(..., min_length=1)
    language: str = Field(..., min_length=2, max_length=10)
    input_text: Optional[str] = None
    response_text: Optional[str] = None
    turn_metadata: Optional[dict[str, Any]] = None


class ConversationTurnResponse(BaseModel):
    turn_id: str
    session_id: str
    speaker: str
    content: str
    language: str
    sequence_number: int = 1
    input_text: Optional[str] = None
    response_text: Optional[str] = None
    turn_metadata: Optional[dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
