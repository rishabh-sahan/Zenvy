import uuid
from sqlalchemy.orm import Session
from app.models.session import Session as SessionModel, SessionStatus
from app.models.conversation_turn import ConversationTurn as ConversationTurnModel
from app.schemas.session import SessionCreate
from app.schemas.conversation_turn import ConversationTurnCreate
from app.services.session_store import (
    backfill_session_safely,
    cache_session_safely,
    cache_turn_safely,
    load_cached_turns,
)


def create_session(db: Session, session_in: SessionCreate) -> SessionModel:
    new_session = SessionModel(
        session_id=str(uuid.uuid4()),
        user_id=session_in.user_id,
        uhid=session_in.uhid,
        channel=session_in.channel,
        language=session_in.language,
        status=SessionStatus.active,
        session_metadata=session_in.session_metadata,
    )
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    cache_session_safely(new_session, turns=[])
    return new_session


def create_conversation_turn(db: Session, session_id: str, turn_in: ConversationTurnCreate) -> ConversationTurnModel:
    last_turn = (
        db.query(ConversationTurnModel)
        .filter(ConversationTurnModel.session_id == session_id)
        .order_by(ConversationTurnModel.sequence_number.desc())
        .first()
    )
    next_sequence = 1 if last_turn is None else last_turn.sequence_number + 1
    new_turn = ConversationTurnModel(
        turn_id=str(uuid.uuid4()),
        session_id=session_id,
        speaker=turn_in.speaker,
        content=turn_in.content,
        language=turn_in.language,
        input_text=turn_in.input_text,
        response_text=turn_in.response_text,
        turn_metadata=turn_in.turn_metadata,
        sequence_number=next_sequence,
    )
    db.add(new_turn)
    db.commit()
    db.refresh(new_turn)
    cache_turn_safely(new_turn)
    return new_turn


def get_session_turns(db: Session, session_id: str):
    cached_turns = load_cached_turns(session_id)
    if cached_turns is not None:
        return cached_turns
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    turns = (
        db.query(ConversationTurnModel)
        .filter(ConversationTurnModel.session_id == session_id)
        .order_by(ConversationTurnModel.sequence_number, ConversationTurnModel.created_at)
        .all()
    )
    if session is not None:
        backfill_session_safely(session, turns)
    return turns
