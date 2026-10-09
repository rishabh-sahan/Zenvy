import pytest
from fakeredis import FakeRedis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db.deps import get_db
from app.main import app
from app.services.session_store import RedisSessionStore, reset_session_store

SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    future=True,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def fake_redis_store():
    client = FakeRedis(decode_responses=True)
    store = RedisSessionStore(client=client, ttl_seconds=3600, key_prefix="zenvy")
    reset_session_store(store)
    yield store
    reset_session_store(None)
    client.flushall()


@pytest.fixture(autouse=True)
def disable_external_whatsapp_sends(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.authentication.send_welcome_notification",
        lambda phone_no, name: "SM-test-welcome",
    )


@pytest.fixture(autouse=True)
def never_contact_whatsapp_for_real(monkeypatch):
    """No test may ever send a real WhatsApp message.

    The service reads your real Meta credentials from DB-Team-C/.env, so without
    this a test that books an appointment for a registered phone number would
    message that number for real. Two layers: the credentials are removed (a send
    then fails with "Missing Meta WhatsApp configuration"), AND any outgoing call
    to WhatsApp raises instead of going out. Tests that check the Meta request
    format set their own fake token and replace httpx.post; that still works.
    """
    from app.core.config import settings
    from app.services import whatsapp_service

    def refuse(*args, **kwargs):
        raise AssertionError("A test tried to send a REAL WhatsApp message; mock it instead.")

    for name in ("META_WHATSAPP_ACCESS_TOKEN", "META_WHATSAPP_PHONE_NUMBER_ID",
                 "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "WABA_AUTH_TOKEN"):
        monkeypatch.setattr(settings, name, None, raising=False)
    monkeypatch.setattr(whatsapp_service.httpx, "post", refuse)
    # DB-Team-C/.env may say REMINDER_MODE=live; tests must never depend on it (tests that need live set it themselves).
    monkeypatch.setattr(settings, "REMINDER_MODE", "mock")
