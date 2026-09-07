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


def test_appointment_body_does_not_greet_patient_by_doctor_name(monkeypatch):
    """
    The recipient is the patient, not the doctor. The body used to open with
    "Hi *Dr. Rao*!", which reads as though the doctor were being messaged.
    """
    sent = {}

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            self.messages = SimpleNamespace(
                create=lambda **kwargs: (sent.update(kwargs), SimpleNamespace(sid="SM-body"))[1]
            )

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", False)

    appointment = SimpleNamespace(
        appointment_id="booking-123",
        doctor_name="Dr. Vinay",
        appointment_datetime=datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc),
        status=SimpleNamespace(value="confirmed"),
        booking_info={"location": "Zenvy Clinic"},
    )

    whatsapp_service.send_appointment_notification("9071265960", appointment)

    body = sent["body"]
    assert not body.startswith("Hi *Dr. Vinay*")
    assert "Hi *Dr. Vinay*" not in body
    # The doctor still appears, as a detail rather than as the greeting.
    assert "Dr. Vinay" in body
    # Leftover fake button markup should not be shown to patients.
    assert "[Confirm]" not in body and "[Reschedule]" not in body


def test_appointment_body_renders_naive_datetime_as_ist(monkeypatch):
    """
    A naive datetime (as SQLite returns) must be read as IST, not as the
    server's local zone -- otherwise the patient is told the wrong time.
    """
    sent = {}

    class FakeClient:
        def __init__(self, account_sid, auth_token):
            self.messages = SimpleNamespace(
                create=lambda **kwargs: (sent.update(kwargs), SimpleNamespace(sid="SM-tz"))[1]
            )

    monkeypatch.setattr(whatsapp_service, "Client", FakeClient)
    monkeypatch.setattr(settings, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "token-test")
    monkeypatch.setattr(settings, "TWILIO_WHATSAPP_FROM", "+17372212163")
    monkeypatch.setattr(settings, "TWILIO_USE_CONTENT_TEMPLATE", False)

    appointment = SimpleNamespace(
        appointment_id="booking-124",
        doctor_name="Dr. Vinay",
        appointment_datetime=datetime(2026, 8, 30, 15, 0),  # naive 3:00 PM
        status=SimpleNamespace(value="confirmed"),
        booking_info={"location": "Zenvy Clinic"},
    )

    whatsapp_service.send_appointment_notification("9071265960", appointment)

    assert "03:00 PM" in sent["body"], "naive 15:00 must stay 3:00 PM, not shift"


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