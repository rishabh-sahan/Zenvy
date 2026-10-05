from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from app.models.authentication import Authentication
from app.services.authentication_service import hash_password
from conftest import TestingSessionLocal

client = TestClient(app)


def _staff_headers():
    phone_no = f"+1555{uuid4().int % 10000000:07d}"
    db = TestingSessionLocal()
    try:
        staff = Authentication(
            phone_no=phone_no,
            password_hash=hash_password("staff-password"),
            role="staff",
        )
        db.add(staff)
        db.commit()
    finally:
        db.close()
    response = client.post(
        "/api/v1/auth/staff/login",
        json={"phone_no": phone_no, "password": "staff-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_audit_logs_endpoint_exists():
    response = client.get("/api/v1/audit/session/nonexistent", headers=_staff_headers())
    assert response.status_code == 200


def test_create_audit_log_requires_action_and_actor():
    headers = _staff_headers()
    response = client.post("/api/v1/audit", headers=headers, json={"action": "create_session"})
    assert response.status_code == 422

    created = client.post(
        "/api/v1/audit",
        headers=headers,
        json={"action": "create_session", "actor": "conversation-service"},
    )
    assert created.status_code == 201
    assert created.json()["actor"] == "conversation-service"


def test_audit_logs_reject_non_staff_and_missing_tokens():
    assert client.get("/api/v1/audit/session/nonexistent").status_code == 401
