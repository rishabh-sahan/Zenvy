from fastapi.testclient import TestClient
from uuid import uuid4

from app.main import app
from conftest import TestingSessionLocal
from app.models.audit_log import AuditLog


client = TestClient(app)


def test_register_and_login_authentication():
    credentials = {"name": "Asha Rao", "phone_no": "+15551234567", "password": "correct-horse"}

    register_response = client.post("/api/v1/auth/register", json=credentials)

    assert register_response.status_code == 201
    assert register_response.json()["name"] == credentials["name"]
    assert register_response.json()["phone_no"] == credentials["phone_no"]
    assert "password" not in register_response.json()
    assert "password_hash" not in register_response.json()

    login_response = client.post("/api/v1/auth/login", json=credentials)
    assert login_response.status_code == 200
    assert login_response.json()["auth_id"] == register_response.json()["auth_id"]

    invalid_login_response = client.post(
        "/api/v1/auth/login",
        json={"phone_no": credentials["phone_no"], "password": "wrong-password"},
    )
    assert invalid_login_response.status_code == 401

def test_register_triggers_welcome_whatsapp(monkeypatch):
    notification = {}
    monkeypatch.setattr(
        "app.api.routes.authentication.send_welcome_notification",
        lambda phone_no, name: notification.update(phone_no=phone_no, name=name) or "SM-welcome-test",
    )
    from app.api.routes.authentication import settings

    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "whatsapp:+17372212163")
    monkeypatch.setattr(settings, "TWILIO_WELCOME_CONTENT_SID", "HX-welcome-test")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", True)

    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Asha Rao", "phone_no": "+15550002222", "password": "correct-horse"},
    )

    assert response.status_code == 201
    assert notification == {"phone_no": "+15550002222", "name": "Asha Rao"}


def test_register_rejects_duplicate_phone_number():
    credentials = {"name": "Asha Rao", "phone_no": "+15557654321", "password": "correct-horse"}
    client.post("/api/v1/auth/register", json=credentials)

    response = client.post("/api/v1/auth/register", json=credentials)

    assert response.status_code == 409


def test_registration_writes_audit_log():
    phone_no = f"+1555{uuid4().int % 10000000:07d}"
    response = client.post(
        "/api/v1/auth/register",
        json={"name": "Asha Rao", "phone_no": phone_no, "password": "correct-horse"},
    )
    assert response.status_code == 201

    db = TestingSessionLocal()
    try:
        audit = (
            db.query(AuditLog)
            .filter(AuditLog.user_id == response.json()["auth_id"])
            .order_by(AuditLog.timestamp.desc())
            .first()
        )
        assert audit is not None
        assert audit.action == "register_user"
        assert audit.actor == "authentication-service"
    finally:
        db.close()
