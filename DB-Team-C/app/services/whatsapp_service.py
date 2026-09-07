import json
from zoneinfo import ZoneInfo

from twilio.rest import Client

from app.core.config import settings


def _whatsapp_number(phone_no: str) -> str:
    normalized = phone_no.strip()
    if normalized.isdigit() and not normalized.startswith("+"):
        normalized = f"{settings.TWILIO_WHATSAPP_COUNTRY_CODE}{normalized}"
    return normalized if normalized.startswith("whatsapp:") else f"whatsapp:{normalized}"


def _twilio_client() -> Client:
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def _twilio_configuration_error() -> str | None:
    missing = []
    if not settings.TWILIO_ACCOUNT_SID:
        missing.append("TWILIO_ACCOUNT_SID")
    if not settings.TWILIO_AUTH_TOKEN:
        missing.append("TWILIO_AUTH_TOKEN")
    if not settings.TWILIO_WHATSAPP_FROM:
        missing.append("TWILIO_WHATSAPP_FROM")
    return ", ".join(missing) if missing else None


def send_appointment_notification(phone_no: str, appointment) -> str:
    missing = _twilio_configuration_error()
    if missing:
        raise RuntimeError(f"Missing Twilio configuration: {missing}")

    client = _twilio_client()
    booking_info = appointment.booking_info or {}
    location = booking_info.get("location") or booking_info.get("clinic") or "To be confirmed"
    appointment_datetime = appointment.appointment_datetime.astimezone(ZoneInfo("Asia/Kolkata"))
    content_variables = json.dumps(
        {
            "1": appointment.doctor_name,
            "2": appointment_datetime.strftime("%d %b %Y"),
            "3": appointment_datetime.strftime("%I:%M %p"),
            "4": location,
            "6": appointment.appointment_id,
        },
        default=str,
    )
    message_data = {
        "from_": _whatsapp_number(settings.TWILIO_WHATSAPP_FROM),
        "to": _whatsapp_number(phone_no),
    }
    if settings.TWILIO_USE_CONTENT_TEMPLATE:
        if not settings.TWILIO_CONTENT_SID:
            raise RuntimeError("TWILIO_CONTENT_SID is required when template mode is enabled")
        message_data.update(
            content_sid=settings.TWILIO_CONTENT_SID,
            content_variables=content_variables,
        )
    else:
        message_data["body"] = (
            f"Hi *{appointment.doctor_name}*! ✓\n\n"
            "Your appointment is confirmed. Here are the details:\n\n"
            f"🗓 *Date:* {appointment_datetime.strftime('%d %b %Y')}\n"
            f"⏰ *Time:* {appointment_datetime.strftime('%I:%M %p')}\n"
            f"📍 *Location:* {location}\n"
            f"Booking ID: *{appointment.appointment_id}*\n\n"
            "Please arrive 10 minutes early. If you need to change your time, "
            "reply to this message or tap below.\n\n"
            "[Confirm] [Reschedule]"
        )
    message = client.messages.create(**message_data)
    return message.sid


def send_welcome_notification(phone_no: str) -> str:
    missing = _twilio_configuration_error()
    if missing:
        raise RuntimeError(f"Missing Twilio welcome configuration: {missing}")

    client = _twilio_client()
    message_data = {
        "from_": _whatsapp_number(settings.TWILIO_WHATSAPP_FROM),
        "to": _whatsapp_number(phone_no),
    }
    if settings.TWILIO_USE_CONTENT_TEMPLATE:
        if not settings.TWILIO_WELCOME_CONTENT_SID:
            raise RuntimeError("TWILIO_WELCOME_CONTENT_SID is required when template mode is enabled")
        message_data["content_sid"] = settings.TWILIO_WELCOME_CONTENT_SID
    else:
        message_data["body"] = (
            "Welcome to Zenvy! Your account has been created successfully. "
            "You can now book and manage your appointments here."
        )
    message = client.messages.create(**message_data)
    return message.sid
