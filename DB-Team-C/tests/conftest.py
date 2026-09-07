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
    """
    Prevent any test from accidentally placing a real Twilio call.

    Both WhatsApp call sites (welcome message on first login, appointment
    confirmation on booking) are patched here so no individual test has to
    remember to do it. A test that wants to assert on notification content
    overrides this patch locally with monkeypatch, same as
    test_create_appointment_notifies_authenticated_phone does below.
    """
    monkeypatch.setattr(
        "app.api.routes.auth.send_welcome_notification",
        lambda phone_no: "SM-test-welcome",
    )
    monkeypatch.setattr(
        "app.api.routes.appointments.send_appointment_notification",
        lambda phone_no, appointment: "SM-test-appointment",
    )
