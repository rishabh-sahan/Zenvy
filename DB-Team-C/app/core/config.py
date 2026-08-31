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


settings = Settings()
