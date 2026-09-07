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

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
    TWILIO_WHATSAPP_FROM = os.getenv("TWILIO_WHATSAPP_FROM")
    TWILIO_CONTENT_SID = os.getenv("TWILIO_CONTENT_SID")
    TWILIO_WELCOME_CONTENT_SID = os.getenv("TWILIO_WELCOME_CONTENT_SID")
    TWILIO_USE_CONTENT_TEMPLATE = os.getenv("TWILIO_USE_CONTENT_TEMPLATE", "true").lower() == "true"
    TWILIO_WHATSAPP_COUNTRY_CODE = os.getenv("TWILIO_WHATSAPP_COUNTRY_CODE", "+91")

    # Meta WhatsApp Cloud API settings are reserved for a future provider.
    META_WHATSAPP_ACCESS_TOKEN = os.getenv("META_WHATSAPP_ACCESS_TOKEN")
    META_WHATSAPP_PHONE_NUMBER_ID = os.getenv("META_WHATSAPP_PHONE_NUMBER_ID")
    META_WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("META_WHATSAPP_BUSINESS_ACCOUNT_ID")
    META_WHATSAPP_API_VERSION = os.getenv("META_WHATSAPP_API_VERSION", "v23.0")


settings = Settings()
