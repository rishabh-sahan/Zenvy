from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.schemas.audit_log import AuditLogCreate, AuditLogResponse
from app.services.audit_service import create_audit_log, get_session_audit_logs

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.post("", response_model=AuditLogResponse, status_code=201)
def create_audit_log_endpoint(payload: AuditLogCreate, db: Session = Depends(get_db)):
    return create_audit_log(db, payload)


@router.get("/session/{session_id}", response_model=list[AuditLogResponse])
def list_audit_logs(session_id: str, db: Session = Depends(get_db)):
    return get_session_audit_logs(db, session_id)
