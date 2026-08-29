from fastapi.testclient import TestClient

from app.main import app
from app.services.session_store import get_session_store

client = TestClient(app)


def test_create_session_and_store_turn():
    session_payload = {
        "user_id": "user_001",
        "channel": "phone",
        "language": "en",
        "uhid": "UHID-100",
    }
    session_response = client.post("/api/v1/sessions", json=session_payload)
    assert session_response.status_code == 201
    body = session_response.json()
    session_id = body["session_id"]
    assert body["runtime_active"] is True
    assert body["status"] == "active"
    assert body["uhid"] == "UHID-100"

    turn_payload = {
        "speaker": "user",
        "content": "I want to book an appointment",
        "language": "en",
    }
    turn_response = client.post(f"/api/v1/sessions/{session_id}/turns", json=turn_payload)
    assert turn_response.status_code == 201
    turn_json = turn_response.json()
    assert turn_json["session_id"] == session_id
    assert turn_json["speaker"] == "user"
    assert turn_json["content"] == "I want to book an appointment"
    assert turn_json["sequence_number"] == 1

    get_response = client.get(f"/api/v1/sessions/{session_id}/turns")
    assert get_response.status_code == 200
    turns = get_response.json()
    assert len(turns) == 1
    assert turns[0]["content"] == "I want to book an appointment"

    cached = get_session_store().get_session(session_id)
    assert cached is not None
    assert cached["user_id"] == "user_001"
    cached_turns = get_session_store().get_turns(session_id)
    assert cached_turns is not None
    assert cached_turns[0]["content"] == "I want to book an appointment"

    live = client.get(f"/api/v1/sessions/{session_id}")
    assert live.status_code == 200
    assert live.json()["runtime_active"] is True
    assert live.json()["session_id"] == session_id


def test_session_handoff_persists_to_db_and_clears_redis():
    created = client.post(
        "/api/v1/sessions",
        json={"user_id": "user_002", "channel": "web", "language": "en"},
    )
    session_id = created.json()["session_id"]
    client.post(
        f"/api/v1/sessions/{session_id}/turns",
        json={"speaker": "user", "content": "hello", "language": "en"},
    )
    assert get_session_store().exists(session_id)

    handoff = client.post(f"/api/v1/sessions/{session_id}/handoff")
    assert handoff.status_code == 200
    payload = handoff.json()
    assert payload["status"] == "completed"
    assert payload["runtime_active"] is False
    assert payload["ended_at"] is not None
    assert get_session_store().get_session(session_id) is None

    durable = client.get(f"/api/v1/sessions/{session_id}")
    assert durable.status_code == 200
    assert durable.json()["runtime_active"] is False
    assert durable.json()["status"] == "completed"

    turns = client.get(f"/api/v1/sessions/{session_id}/turns")
    assert turns.status_code == 200
    assert len(turns.json()) == 1


def test_required_tables_exist():
    from app.db.database import Base

    required_tables = {
        "sessions",
        "conversation_turns",
        "ai_appointments",
        "escalations",
        "audit_log",
    }
    actual_tables = set(Base.metadata.tables.keys())
    missing_tables = required_tables - actual_tables
    assert not missing_tables, f"Missing tables: {sorted(missing_tables)}"


def test_health_reports_redis_unavailable(monkeypatch):
    class UnavailableRedis:
        def ping(self):
            return False

    monkeypatch.setattr("app.main.get_session_store", lambda: UnavailableRedis())

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "redis": False}
