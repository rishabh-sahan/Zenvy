from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
MIGRATION_001 = Path(__file__).resolve().parents[1] / "db" / "migrations" / "001_initial_schema.sql"


def test_migration_001_exists_and_defines_core_tables():
    assert MIGRATION_001.exists()
    sql = MIGRATION_001.read_text(encoding="utf-8")
    for table in ("sessions", "conversation_turns", "ai_appointments", "escalations", "audit_log"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
    assert "session_id" in sql
    assert "patient_uhid" in sql


def test_escalations_endpoint_exists():
    response = client.get("/api/v1/escalations/session/nonexistent")
    assert response.status_code in (200, 404)


def test_create_escalation_with_schema():
    session = client.post(
        "/api/v1/sessions",
        json={"user_id": "user_esc", "channel": "phone", "language": "en"},
    )
    session_id = session.json()["session_id"]
    response = client.post(
        "/api/v1/escalations",
        json={
            "session_id": session_id,
            "reason": "Chest pain",
            "emergency_type": "medical",
            "severity": "high",
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "open"
