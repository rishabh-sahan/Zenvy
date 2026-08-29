from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditLogCreate(BaseModel):
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    action: str = Field(..., min_length=1)
    actor: str = Field(..., min_length=1)
    relevant_metadata: Optional[dict[str, Any]] = None
    before_value: Optional[dict[str, Any]] = None
    after_value: Optional[dict[str, Any]] = None


class AuditLogResponse(BaseModel):
    audit_id: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    action: str
    actor: str
    timestamp: datetime
    relevant_metadata: Optional[dict[str, Any]] = None
    before_value: Optional[dict[str, Any]] = None
    after_value: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)
