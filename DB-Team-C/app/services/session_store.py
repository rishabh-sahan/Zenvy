import json
from datetime import datetime
from enum import Enum
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.db.redis import get_redis

_store: "RedisSessionStore | None" = None


def _json_ready(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def session_to_dict(session) -> dict[str, Any]:
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "uhid": session.uhid,
        "channel": session.channel,
        "language": session.language,
        "started_at": _json_ready(session.started_at),
        "ended_at": _json_ready(session.ended_at),
        "status": _json_ready(session.status),
        "session_metadata": session.session_metadata,
    }


def turn_to_dict(turn) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "session_id": turn.session_id,
        "speaker": turn.speaker,
        "content": turn.content,
        "input_text": turn.input_text,
        "response_text": turn.response_text,
        "language": turn.language,
        "sequence_number": turn.sequence_number,
        "created_at": _json_ready(turn.created_at),
        "turn_metadata": turn.turn_metadata,
    }


class RedisSessionStore:
    """Live conversation session cache. Postgres remains the durable store."""

    def __init__(self, client: Redis, ttl_seconds: int, key_prefix: str):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.key_prefix = key_prefix

    def _session_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:session:{session_id}"

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except RedisError:
            return False

    def _read(self, session_id: str) -> dict[str, Any] | None:
        raw = self.client.get(self._session_key(session_id))
        if raw is None:
            return None
        return json.loads(raw)

    def _write(self, session_id: str, document: dict[str, Any]) -> None:
        self.client.set(
            self._session_key(session_id),
            json.dumps(document),
            ex=self.ttl_seconds,
        )

    def save_session(self, session_data: dict[str, Any], turns: list[dict[str, Any]] | None = None) -> None:
        session_id = session_data["session_id"]
        existing = self._read(session_id)
        if turns is None:
            turns = existing.get("turns", []) if existing else []
        document = {**session_data, "turns": turns}
        self._write(session_id, document)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self._read(session_id)

    def exists(self, session_id: str) -> bool:
        return bool(self.client.exists(self._session_key(session_id)))

    def append_turn(self, session_id: str, turn_data: dict[str, Any]) -> None:
        document = self._read(session_id) or {"session_id": session_id, "turns": []}
        document.setdefault("turns", []).append(turn_data)
        self._write(session_id, document)

    def get_turns(self, session_id: str) -> list[dict[str, Any]] | None:
        document = self._read(session_id)
        if document is None:
            return None
        return document.get("turns", [])

    def replace_turns(self, session_id: str, turns: list[dict[str, Any]]) -> None:
        document = self._read(session_id) or {"session_id": session_id}
        document["turns"] = turns
        self._write(session_id, document)

    def delete_session(self, session_id: str) -> None:
        self.client.delete(self._session_key(session_id))


def get_session_store() -> RedisSessionStore:
    global _store
    if _store is None:
        _store = RedisSessionStore(
            client=get_redis(),
            ttl_seconds=settings.SESSION_TTL_SECONDS,
            key_prefix=settings.REDIS_KEY_PREFIX,
        )
    return _store


def reset_session_store(store: RedisSessionStore | None = None) -> None:
    global _store
    _store = store


def cache_session_safely(session, turns: list | None = None) -> None:
    try:
        serialized_turns = None
        if turns is not None:
            serialized_turns = [turn_to_dict(turn) for turn in turns]
        get_session_store().save_session(session_to_dict(session), turns=serialized_turns)
    except RedisError:
        return


def cache_turn_safely(turn) -> None:
    try:
        get_session_store().append_turn(turn.session_id, turn_to_dict(turn))
    except RedisError:
        return


def load_cached_turns(session_id: str) -> list[dict[str, Any]] | None:
    try:
        return get_session_store().get_turns(session_id)
    except RedisError:
        return None


def backfill_session_safely(session, turns) -> None:
    cache_session_safely(session, turns=turns)
