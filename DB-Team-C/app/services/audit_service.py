import uuid

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.schemas.audit_log import AuditLogCreate


def create_audit_log(db: Session, payload: AuditLogCreate):
    audit = AuditLog(
        audit_id=str(uuid.uuid4()),
        session_id=payload.session_id,
        user_id=payload.user_id,
        action=payload.action,
        actor=payload.actor,
        relevant_metadata=payload.relevant_metadata,
        before_value=payload.before_value,
        after_value=payload.after_value,
    )
    db.add(audit)
    db.commit()
    db.refresh(audit)
    return audit


def get_session_audit_logs(db: Session, session_id: str):
    return db.query(AuditLog).filter(AuditLog.session_id == session_id).all()
