from datetime import datetime, timezone
from types import SimpleNamespace

from app.core.config import settings
from app.services import whatsapp_service


def test_meta_welcome_template_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"messages": [{"id": "wamid.test_welcome"}]},
        )

    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "1259494890588867")
    monkeypatch.setattr(settings, "META_WHATSAPP_API_VERSION", "v23.0")
    monkeypatch.setattr(settings, "META_WHATSAPP_WELCOME_TEMPLATE_NAME", "zenvy_welcome")
    monkeypatch.setattr(settings, "META_WHATSAPP_TEMPLATE_LANGUAGE", "en")
    monkeypatch.setattr(whatsapp_service.httpx, "post", fake_post)

    sid = whatsapp_service.send_welcome_notification("9071265960", "Asha Rao")

    assert sid == "wamid.test_welcome"
    assert captured["url"] == "https://graph.facebook.com/v23.0/1259494890588867/messages"
    assert captured["headers"]["Authorization"] == "Bearer test-access-token"
    assert captured["json"]["messaging_product"] == "whatsapp"
    assert captured["json"]["to"] == "+919071265960"
    assert captured["json"]["type"] == "template"
    assert captured["json"]["template"]["name"] == "zenvy_welcome"
    assert captured["json"]["template"]["language"] == {"code": "en"}
    assert captured["json"]["template"]["components"][0]["parameters"][0] == {
        "type": "text",
        "parameter_name": "name",
        "text": "Asha Rao",
    }


def test_meta_appointment_template_request(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"messages": [{"id": "wamid.test_appointment"}]},
        )

    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "1259494890588867")
    monkeypatch.setattr(settings, "META_WHATSAPP_API_VERSION", "v23.0")
    monkeypatch.setattr(settings, "META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME", "zenvy_appointment_confirmation")
    monkeypatch.setattr(settings, "META_WHATSAPP_TEMPLATE_LANGUAGE", "en")
    monkeypatch.setattr(whatsapp_service.httpx, "post", fake_post)

    appointment = SimpleNamespace(
        appointment_id="booking-123",
        doctor_name="Dr. Vinay",
        appointment_datetime=datetime(2026, 8, 30, 5, 0, tzinfo=timezone.utc),
        status=SimpleNamespace(value="confirmed"),
        booking_info={"location": "Zenvy Clinic"},
    )

    sid = whatsapp_service.send_appointment_notification("9071265960", appointment, patient_name="Asha Rao")

    assert sid == "wamid.test_appointment"
    assert captured["json"]["template"]["name"] == "zenvy_appointment_confirmation"
    assert captured["json"]["template"]["language"] == {"code": "en"}
    parameters = captured["json"]["template"]["components"][0]["parameters"]
    assert [param["parameter_name"] for param in parameters] == [
        "name",
        "doctor",
        "date",
        "time",
        "location",
        "id",
    ]
    assert [param["text"] for param in parameters] == [
        "Asha Rao",
        "Dr. Vinay",
        "30 Aug 2026",
        "10:30 AM",
        "Zenvy Clinic",
        "booking-123",
    ]
