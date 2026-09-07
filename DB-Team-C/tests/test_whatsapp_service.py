import json
from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config import settings
from app.services import whatsapp_service


def test_appointment_notification_uses_template_variables(monkeypatch):
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(sid="SM-template-test")

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            assert account_sid == "AC-test"
            assert auth_token == "token-test"
            self.messages = FakeMessages()

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_CONTENT_SID", "HX-template-test")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", True)
    appointment = SimpleNamespace(
        appointment_id="booking-123",
        doctor_name="Dr. Vinay",
        appointment_datetime=datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc),
        status=SimpleNamespace(value="confirmed"),
        booking_info={"location": "Zenvy Clinic"},
    )

    message_sid = whatsapp_service.send_appointment_notification("9071265960", appointment)

    assert message_sid == "SM-template-test"
    assert sent["from_"] == "whatsapp:+17372212163"
    assert sent["to"] == "whatsapp:+919071265960"
    assert sent["content_sid"] == "HX-template-test"
    assert json.loads(sent["content_variables"]) == {
        "1": "Dr. Vinay",
        "2": "30 Aug 2026",
        "3": "10:30 AM",
        "4": "Zenvy Clinic",
        "6": "booking-123",
    }


def test_welcome_notification_uses_welcome_template(monkeypatch):
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(sid="SM-welcome-test")

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            self.messages = FakeMessages()

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_WELCOME_CONTENT_SID", "HX-welcome-test")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", True)

    message_sid = whatsapp_service.send_welcome_notification("9071265960")

    assert message_sid == "SM-welcome-test"
    assert sent == {
        "from_": "whatsapp:+17372212163",
        "to": "whatsapp:+919071265960",
        "content_sid": "HX-welcome-test",
    }


def test_welcome_notification_can_send_plain_body(monkeypatch):
    sent = {}

    class FakeMessages:
        def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(sid="SM-welcome-body-test")

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            self.messages = FakeMessages()

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", False)

    message_sid = whatsapp_service.send_welcome_notification("9071265960")

    assert message_sid == "SM-welcome-body-test"
    assert sent["to"] == "whatsapp:+919071265960"
    assert "Welcome to Zenvy" in sent["body"]
    assert "content_sid" not in sent


def test_twilio_auth_token_authentication(monkeypatch):
    client_args = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            client_args["args"] = args
            client_args["kwargs"] = kwargs
            self.messages = SimpleNamespace(
                create=lambda **message: SimpleNamespace(sid="SM-api-key-test")
            )

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", False)

    message_sid = whatsapp_service.send_welcome_notification("9071265960")

    assert message_sid == "SM-api-key-test"
    assert client_args == {
        "args": ("AC-test", "token-test"),
        "kwargs": {},
    }