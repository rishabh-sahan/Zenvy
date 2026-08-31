import uuid

from sqlalchemy.orm import Session

from app.models.escalation import Escalation, EscalationStatus
from app.schemas.escalation import EscalationCreate


def create_escalation(db: Session, payload: EscalationCreate):
    status = payload.status if isinstance(payload.status, EscalationStatus) else EscalationStatus(payload.status)
    escalation = Escalation(
        escalation_id=str(uuid.uuid4()),
        session_id=payload.session_id,
        reason=payload.reason,
        emergency_type=payload.emergency_type,
        severity=payload.severity,
        status=status,
        handled_by=payload.handled_by,
        escalation_metadata=payload.escalation_metadata,
    )
    db.add(escalation)
    db.commit()
    db.refresh(escalation)
    return escalation


def get_session_escalations(db: Session, session_id: str):
    return db.query(Escalation).filter(Escalation.session_id == session_id).all()
