from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.escalation import EscalationStatus


class EscalationCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    emergency_type: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    status: EscalationStatus = EscalationStatus.open
    handled_by: Optional[str] = None
    escalation_metadata: Optional[dict[str, Any]] = None


class EscalationResponse(BaseModel):
    escalation_id: str
    session_id: str
    reason: str
    emergency_type: str
    severity: str
    status: EscalationStatus
    created_at: datetime
    resolved_at: Optional[datetime] = None
    handled_by: Optional[str] = None
    escalation_metadata: Optional[dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)
