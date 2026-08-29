from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    user_id: str = Field(..., min_length=1)
    channel: Literal["phone", "sms", "web"]
    language: str = Field(..., min_length=2, max_length=10)
    uhid: Optional[str] = None
    session_metadata: Optional[dict[str, Any]] = None


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    uhid: Optional[str] = None
    channel: str
    language: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str
    session_metadata: Optional[dict[str, Any]] = None
    runtime_active: bool = False

    model_config = ConfigDict(from_attributes=True)
