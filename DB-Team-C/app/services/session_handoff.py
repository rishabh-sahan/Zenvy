from datetime import datetime, timezone
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.models.conversation_turn import ConversationTurn as ConversationTurnModel
from app.models.session import Session as SessionModel, SessionStatus
from app.services.session_store import get_session_store, session_to_dict


def _parse_datetime(value: Any):
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def persist_runtime_to_db(db: Session, session_id: str) -> SessionModel | None:
    """Copy Redis runtime state into PostgreSQL, joined by session_id."""
    try:
        runtime = get_session_store().get_session(session_id)
    except RedisError:
        runtime = None
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()

    if session is None and runtime is not None:
        session = SessionModel(
            session_id=runtime["session_id"],
            user_id=runtime.get("user_id") or "unknown",
            uhid=runtime.get("uhid"),
            channel=runtime.get("channel") or "web",
            language=runtime.get("language") or "en",
            started_at=_parse_datetime(runtime.get("started_at")),
            ended_at=_parse_datetime(runtime.get("ended_at")),
            status=SessionStatus(runtime.get("status") or SessionStatus.active),
            session_metadata=runtime.get("session_metadata"),
        )
        db.add(session)
        db.flush()
    elif session is not None and runtime is not None:
        session.uhid = runtime.get("uhid", session.uhid)
        session.language = runtime.get("language", session.language)
        session.session_metadata = runtime.get("session_metadata", session.session_metadata)
        if runtime.get("status"):
            session.status = SessionStatus(runtime["status"])
        session.ended_at = _parse_datetime(runtime.get("ended_at")) or session.ended_at

    if runtime is not None:
        existing_ids = {
            turn_id
            for (turn_id,) in db.query(ConversationTurnModel.turn_id)
            .filter(ConversationTurnModel.session_id == session_id)
            .all()
        }
        for turn in runtime.get("turns", []):
            turn_id = turn.get("turn_id")
            if not turn_id or turn_id in existing_ids:
                continue
            db.add(
                ConversationTurnModel(
                    turn_id=turn_id,
                    session_id=session_id,
                    speaker=turn.get("speaker") or "user",
                    content=turn.get("content") or "",
                    input_text=turn.get("input_text"),
                    response_text=turn.get("response_text"),
                    language=turn.get("language") or (session.language if session else "en"),
                    sequence_number=turn.get("sequence_number") or 1,
                    created_at=_parse_datetime(turn.get("created_at")),
                    turn_metadata=turn.get("turn_metadata"),
                )
            )
            existing_ids.add(turn_id)

    if session is not None:
        db.commit()
        db.refresh(session)
    return session


def handoff_session_to_db(db: Session, session_id: str) -> SessionModel | None:
    """End the runtime session: persist Redis state, mark durable row complete, drop Redis key."""
    session = persist_runtime_to_db(db, session_id)
    if session is None:
        return None
    session.status = SessionStatus.completed
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    try:
        get_session_store().delete_session(session_id)
    except RedisError:
        pass
    return session


def session_response_payload(session: SessionModel, runtime: dict | None = None) -> dict[str, Any]:
    payload = session_to_dict(session)
    if runtime:
        payload["uhid"] = runtime.get("uhid", payload["uhid"])
        payload["language"] = runtime.get("language", payload["language"])
        payload["session_metadata"] = runtime.get("session_metadata", payload["session_metadata"])
        payload["status"] = runtime.get("status", payload["status"])
        payload["ended_at"] = runtime.get("ended_at", payload["ended_at"])
        payload["runtime_active"] = True
    else:
        payload["runtime_active"] = False
    return payload


def load_session_view(db: Session, session_id: str) -> dict[str, Any] | None:
    try:
        runtime = get_session_store().get_session(session_id)
    except RedisError:
        runtime = None
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    if session is None and runtime is None:
        return None
    if session is None:
        session = persist_runtime_to_db(db, session_id)
    if session is None:
        return None
    return session_response_payload(session, runtime)
