import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    APP_NAME = "Zenvy Conversation Service"
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://zenvy_user:zenvy_pass@localhost:5433/zenvy_db")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_KEY_PREFIX = os.getenv("REDIS_KEY_PREFIX", "zenvy")
    SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
    AUTH_TOKEN_SECRET = os.getenv("AUTH_TOKEN_SECRET", "local-development-secret")
    AUTH_TOKEN_TTL_SECONDS = int(os.getenv("AUTH_TOKEN_TTL_SECONDS", "3600"))

    # Slot booking. A held slot is reserved for one patient for this long while
    # they confirm; slots are generated this many days ahead.
    SLOT_HOLD_SECONDS = int(os.getenv("SLOT_HOLD_SECONDS", "300"))
    BOOKING_HORIZON_DAYS = int(os.getenv("BOOKING_HORIZON_DAYS", "14"))
    # When true, POST /appointments without a slot_id is rejected, so every
    # booking goes through the slot lock. Off by default so older callers keep working.
    REQUIRE_SLOT_FOR_BOOKING = os.getenv("REQUIRE_SLOT_FOR_BOOKING", "false").lower() == "true"

    # Consultation recordings. Audio is encrypted (AES-256-GCM) before it is
    # written; the key is a base64 string of 32 random bytes. Generate one with:
    #   python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())"
    # Without a key, uploading audio is refused rather than stored in the clear.
    AUDIO_ENCRYPTION_KEY = os.getenv("AUDIO_ENCRYPTION_KEY")
    AUDIO_STORAGE_DIR = os.getenv("AUDIO_STORAGE_DIR", "/data/audio")
    MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_BYTES", str(120 * 1024 * 1024)))
    # How long recordings are kept (RETENTION_POLICY.md). Approved notes are kept.
    AUDIO_RETENTION_DAYS = int(os.getenv("AUDIO_RETENTION_DAYS", "30"))
    TRANSCRIPT_RETENTION_DAYS = int(os.getenv("TRANSCRIPT_RETENTION_DAYS", "90"))

    META_WHATSAPP_ACCESS_TOKEN = os.getenv("META_WHATSAPP_ACCESS_TOKEN")
    META_WHATSAPP_PHONE_NUMBER_ID = os.getenv("META_WHATSAPP_PHONE_NUMBER_ID")
    META_WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("META_WHATSAPP_BUSINESS_ACCOUNT_ID")
    META_WHATSAPP_API_VERSION = os.getenv("META_WHATSAPP_API_VERSION", "v23.0")
    META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME = os.getenv(
        "META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME", "zenvy_appointment_confirmation"
    )
    META_WHATSAPP_WELCOME_TEMPLATE_NAME = os.getenv(
        "META_WHATSAPP_WELCOME_TEMPLATE_NAME", "zenvy_welcome"
    )
    META_WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("META_WHATSAPP_TEMPLATE_LANGUAGE", "en")

    WABA_ACCOUNT_SID = os.getenv("WABA_ACCOUNT_SID") or os.getenv("TWILIO_ACCOUNT_SID")
    WABA_AUTH_TOKEN = os.getenv("WABA_AUTH_TOKEN") or os.getenv("TWILIO_AUTH_TOKEN")
    WABA_WHATSAPP_FROM = os.getenv("WABA_WHATSAPP_FROM") or os.getenv("TWILIO_WHATSAPP_FROM")
    WABA_WELCOME_TEMPLATE_NAME = os.getenv("WABA_WELCOME_TEMPLATE_NAME") or META_WHATSAPP_WELCOME_TEMPLATE_NAME
    WABA_APPOINTMENT_TEMPLATE_NAME = os.getenv("WABA_APPOINTMENT_TEMPLATE_NAME") or META_WHATSAPP_APPOINTMENT_TEMPLATE_NAME
    WABA_TEMPLATE_LANGUAGE = os.getenv("WABA_TEMPLATE_LANGUAGE") or META_WHATSAPP_TEMPLATE_LANGUAGE

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID") or WABA_ACCOUNT_SID
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN") or WABA_AUTH_TOKEN
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM") or WABA_WHATSAPP_FROM
    TWILIO_WELCOME_CONTENT_SID = os.getenv("TWILIO_WELCOME_CONTENT_SID") or WABA_WELCOME_TEMPLATE_NAME
    TWILIO_APPOINTMENT_CONTENT_SID = os.getenv("TWILIO_APPOINTMENT_CONTENT_SID") or WABA_APPOINTMENT_TEMPLATE_NAME
    TWILIO_USE_CONTENT_TEMPLATE = os.getenv("TWILIO_USE_CONTENT_TEMPLATE", "true").lower() == "true"


settings = Settings()
