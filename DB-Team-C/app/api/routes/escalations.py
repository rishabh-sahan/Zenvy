from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.session import Session as SessionModel
from app.schemas.escalation import EscalationCreate, EscalationResponse
from app.services.escalation_service import create_escalation, get_session_escalations

router = APIRouter(prefix="/api/v1/escalations", tags=["escalations"])


@router.post("", response_model=EscalationResponse, status_code=status.HTTP_201_CREATED)
def create_escalation_endpoint(payload: EscalationCreate, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return create_escalation(db, payload)


@router.get("/session/{session_id}", response_model=list[EscalationResponse])
def list_escalations(session_id: str, db: Session = Depends(get_db)):
    return get_session_escalations(db, session_id)
