from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from redis.exceptions import RedisError

from app.db.deps import get_db
from app.models.session import Session as SessionModel, SessionStatus
from app.schemas.session import SessionCreate, SessionResponse
from app.schemas.conversation_turn import ConversationTurnCreate, ConversationTurnResponse
from app.services.conversation_service import create_session, create_conversation_turn, get_session_turns
from app.services.session_handoff import handoff_session_to_db, load_session_view, session_response_payload
from app.services.session_store import get_session_store

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


def require_session(session_id: str, db: Session) -> SessionModel:
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session_endpoint(session_in: SessionCreate, db: Session = Depends(get_db)):
    session = create_session(db, session_in)
    try:
        runtime = get_session_store().get_session(session.session_id)
    except RedisError:
        runtime = None
    return session_response_payload(session, runtime)


@router.get("/{session_id}", response_model=SessionResponse)
def get_session_endpoint(session_id: str, db: Session = Depends(get_db)):
    view = load_session_view(db, session_id)
    if view is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return view


@router.post("/{session_id}/handoff", response_model=SessionResponse)
def handoff_session_endpoint(session_id: str, db: Session = Depends(get_db)):
    session = require_session(session_id, db)
    if session.status == SessionStatus.completed:
        try:
            still_runtime = get_session_store().exists(session_id)
        except RedisError:
            still_runtime = False
        if not still_runtime:
            return session_response_payload(session)
    handed_off = handoff_session_to_db(db, session_id)
    if handed_off is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session_response_payload(handed_off)


@router.post("/{session_id}/turns", response_model=ConversationTurnResponse, status_code=status.HTTP_201_CREATED)
def create_turn_endpoint(
    session_id: str,
    turn_in: ConversationTurnCreate,
    db: Session = Depends(get_db),
):
    require_session(session_id, db)
    turn = create_conversation_turn(db, session_id, turn_in)
    return turn


@router.get("/{session_id}/turns", response_model=list[ConversationTurnResponse])
def get_turns_endpoint(session_id: str, db: Session = Depends(get_db)):
    require_session(session_id, db)
    return get_session_turns(db, session_id)
