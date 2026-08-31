import os
from dotenv import load_dotenv

load_dotenv()

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_BASE_URL = "https://api.sarvam.ai"

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

if not SARVAM_API_KEY:
    raise RuntimeError(
        "SARVAM_API_KEY is not set. Copy .env.example to .env and add your key."
    )