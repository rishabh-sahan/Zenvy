from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_login_self_registers_new_phone_number():
    phone_no = "9123456780"

    response = client.post("/api/v1/auth/login", json={"phone_no": phone_no})

    assert response.status_code == 200
    body = response.json()
    assert body["phone_no"] == phone_no
    assert body["is_new"] is True
    assert "auth_id" in body


def test_login_returns_existing_account_on_second_login():
    phone_no = "9123456781"

    first = client.post("/api/v1/auth/login", json={"phone_no": phone_no})
    second = client.post("/api/v1/auth/login", json={"phone_no": phone_no})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["auth_id"] == second.json()["auth_id"]
    assert second.json()["is_new"] is False


def test_login_normalizes_phone_number_formats():
    """
    '+91 98765 43210', '098765 43210', and '9876543210' must all resolve to
    the same 10-digit account rather than creating three separate rows.
    """
    responses = [
        client.post("/api/v1/auth/login", json={"phone_no": "+91 98765 43211"}),
        client.post("/api/v1/auth/login", json={"phone_no": "098765 43211"}),
        client.post("/api/v1/auth/login", json={"phone_no": "9876543211"}),
    ]

    for response in responses:
        assert response.status_code == 200
        assert response.json()["phone_no"] == "9876543211"

    auth_ids = {response.json()["auth_id"] for response in responses}
    assert len(auth_ids) == 1


def test_login_rejects_invalid_phone_number():
    response = client.post("/api/v1/auth/login", json={"phone_no": "12345"})
    assert response.status_code == 422


def test_login_triggers_welcome_whatsapp_on_new_account(monkeypatch):
    notification = {}

    def fake_welcome(phone_no):
        notification["phone_no"] = phone_no
        return "SM-welcome-test"

    # Overrides the blanket no-op patch from conftest's autouse fixture, so
    # this test can assert on exactly what would have been sent.
    monkeypatch.setattr(
        "app.api.routes.auth.send_welcome_notification",
        fake_welcome,
    )

    response = client.post("/api/v1/auth/login", json={"phone_no": "9123456782"})

    assert response.status_code == 200
    assert notification == {"phone_no": "9123456782"}


def test_login_does_not_resend_welcome_whatsapp_on_repeat_login(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "app.api.routes.auth.send_welcome_notification",
        lambda phone_no: calls.append(phone_no) or "SM-welcome-test",
    )

    phone_no = "9123456783"
    client.post("/api/v1/auth/login", json={"phone_no": phone_no})  # first login: new account
    client.post("/api/v1/auth/login", json={"phone_no": phone_no})  # second login: existing account

    assert calls == [phone_no]


def test_login_does_not_fail_when_welcome_whatsapp_send_fails(monkeypatch):
    """
    A Twilio outage must never block a patient's first login -- the welcome
    message is best-effort only. See the comment in app/api/routes/auth.py.
    """

    def raise_runtime_error(phone_no):
        raise RuntimeError("Missing Twilio configuration: TWILIO_ACCOUNT_SID")

    monkeypatch.setattr(
        "app.api.routes.auth.send_welcome_notification",
        raise_runtime_error,
    )

    response = client.post("/api/v1/auth/login", json={"phone_no": "9123456784"})

    assert response.status_code == 200
    assert response.json()["is_new"] is True
