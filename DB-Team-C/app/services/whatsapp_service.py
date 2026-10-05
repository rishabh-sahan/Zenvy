import re
from zoneinfo import ZoneInfo

import httpx

from app.core.config import settings


def _whatsapp_number(phone_no: str) -> str:
    digits = re.sub(r"\D", "", phone_no)
    if not digits:
        return ""
    if len(digits) == 10:
        return f"+91{digits}"
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if not digits.startswith("+"):
        return f"+{digits}"
    return digits


def _meta_configuration_error(template_name: str) -> str | None:
    missing = []
    if not settings.META_WHATSAPP_ACCESS_TOKEN:
        missing.append("META_WHATSAPP_ACCESS_TOKEN")
    if not settings.META_WHATSAPP_PHONE_NUMBER_ID:
        missing.append("META_WHATSAPP_PHONE_NUMBER_ID")
    if not settings.META_WHATSAPP_API_VERSION:
        missing.append("META_WHATSAPP_API_VERSION")
    if not template_name:
        missing.append("WhatsApp template name")
    return ", ".join(missing) if missing else None


def _send_template_message(phone_no: str, template_name: str, parameters: list[tuple[str, str]]) -> str:
    missing = _meta_configuration_error(template_name)
    if missing:
        raise RuntimeError(f"Missing Meta WhatsApp configuration: {missing}")

    if not re.fullmatch(r"\d+", settings.META_WHATSAPP_PHONE_NUMBER_ID):
        raise RuntimeError("META_WHATSAPP_PHONE_NUMBER_ID must contain only digits")
    recipient = _whatsapp_number(phone_no)
    if not recipient:
        raise RuntimeError("Recipient phone number must contain digits")

    url = (
        f"https://graph.facebook.com/{settings.META_WHATSAPP_API_VERSION}/"
        f"{settings.META_WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": settings.META_WHATSAPP_TEMPLATE_LANGUAGE},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "parameter_name": name, "text": value}
                        for name, value in parameters
                    ],
                }
            ],
        },
    }
    try:
        response = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.META_WHATSAPP_ACCESS_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15.0,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("Meta WhatsApp API request failed") from exc

    try:
        return response.json()["messages"][0]["id"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Meta WhatsApp API returned no message ID") from exc


def send_appointment_notification(phone_no: str, appointment, patient_name: str = "there") -> str:
    booking_info = appointment.booking_info or {}
    location = booking_info.get("location") or booking_info.get("clinic") or "To be confirmed"
    appointment_datetime = appointment.appointment_datetime.astimezone(ZoneInfo("Asia/Kolkata"))
    parameters = [
        ("name", patient_name),
        ("doctor", appointment.doctor_name),
        ("date", appointment_datetime.strftime("%d %b %Y")),
        ("time", appointment_datetime.strftime("%I:%M %p")),
        ("location", location),
        ("id", appointment.appointment_id),
    ]
    return _send_template_message(
        phone_no,
        settings.META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME,
        parameters,
    )


def send_welcome_notification(phone_no: str, name: str = "there") -> str:
    return _send_template_message(
        phone_no,
        settings.META_WHATSAPP_WELCOME_TEMPLATE_NAME,
        [("name", name)],
    )
