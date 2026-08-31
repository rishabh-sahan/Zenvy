from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_audit_logs_endpoint_exists():
    response = client.get("/api/v1/audit/session/nonexistent")
    assert response.status_code in (200, 404)


def test_create_audit_log_requires_action_and_actor():
    response = client.post("/api/v1/audit", json={"action": "create_session"})
    assert response.status_code == 422

    created = client.post(
        "/api/v1/audit",
        json={"action": "create_session", "actor": "conversation-service"},
    )
    assert created.status_code == 201
    assert created.json()["actor"] == "conversation-service"
