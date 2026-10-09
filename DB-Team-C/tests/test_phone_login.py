"""Phone-number-only patient login (what the web dashboard / gateway uses)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.deps import get_db
from app.main import app
from app.models.authentication import Authentication
from app.services.authentication_service import UNUSABLE_PASSWORD_HASH, hash_password

client = TestClient(app)


def _number() -> str:
    """A fresh valid 10-digit number per test (the test database is shared)."""
    return str(9_000_000_000 + uuid.uuid4().int % 99_999_999)


@pytest.fixture
def welcomes(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.api.routes.authentication.send_welcome_notification",
        lambda phone_no, name="there": sent.append((phone_no, name)) or "SM-test",
    )
    return sent


def test_an_unknown_number_is_registered_and_signed_in(welcomes):
    phone = _number()
    response = client.post("/api/v1/auth/phone-login", json={"phone_no": phone})
    assert response.status_code == 200
    body = response.json()
    assert body["phone_no"] == phone
    assert body["is_new"] is True
    assert body["auth_id"]
    # A welcome WhatsApp goes out once, on registration.
    assert welcomes == [(phone, "there")]


def test_a_known_number_signs_in_to_the_same_account(welcomes):
    phone = _number()
    first = client.post("/api/v1/auth/phone-login", json={"phone_no": phone}).json()
    second = client.post("/api/v1/auth/phone-login", json={"phone_no": phone}).json()
    assert second["auth_id"] == first["auth_id"]
    assert second["is_new"] is False
    assert len(welcomes) == 1  # not welcomed again


@pytest.mark.parametrize("typed", ["+91 {a} {b}", "0{a}{b}", "91{a}{b}", "{a}-{b}", " {a}{b} "])
def test_the_number_is_normalised_to_ten_digits(welcomes, typed):
    phone = _number()
    shaped = typed.format(a=phone[:5], b=phone[5:])
    response = client.post("/api/v1/auth/phone-login", json={"phone_no": shaped})
    assert response.status_code == 200
    assert response.json()["phone_no"] == phone


@pytest.mark.parametrize("bad", ["abc", "12345", "123456789", "12345678901", "", "+91 12345"])
def test_a_malformed_number_is_rejected_with_422(welcomes, bad):
    response = client.post("/api/v1/auth/phone-login", json={"phone_no": bad})
    assert response.status_code == 422
    assert welcomes == []


def test_a_failing_welcome_message_never_blocks_login(monkeypatch):
    def boom(phone_no, name="there"):
        raise RuntimeError("Meta WhatsApp API request failed")

    monkeypatch.setattr("app.api.routes.authentication.send_welcome_notification", boom)
    response = client.post("/api/v1/auth/phone-login", json={"phone_no": _number()})
    assert response.status_code == 200
    assert response.json()["is_new"] is True


def test_the_new_account_cannot_be_used_with_a_password(welcomes):
    phone = _number()
    client.post("/api/v1/auth/phone-login", json={"phone_no": phone})

    for guess in ("", "password123", UNUSABLE_PASSWORD_HASH, "!phone-only-no-password"):
        response = client.post("/api/v1/auth/login", json={"phone_no": phone, "password": guess or "x" * 8})
        assert response.status_code == 401
        response = client.post("/api/v1/auth/staff/login", json={"phone_no": phone, "password": guess or "x" * 8})
        assert response.status_code == 401


def test_the_new_account_is_an_ordinary_patient(welcomes):
    phone = _number()
    client.post("/api/v1/auth/phone-login", json={"phone_no": phone})
    db = next(app.dependency_overrides[get_db]())
    account = db.query(Authentication).filter(Authentication.phone_no == phone).one()
    assert account.role == "patient"
    assert account.password_hash == UNUSABLE_PASSWORD_HASH
    db.close()


def test_a_staff_number_cannot_use_the_patient_login(welcomes):
    phone = _number()
    db = next(app.dependency_overrides[get_db]())
    db.add(Authentication(name="Dr Staff", phone_no=phone, password_hash=hash_password("a-real-password"), role="staff"))
    db.commit()
    db.close()

    response = client.post("/api/v1/auth/phone-login", json={"phone_no": phone})
    assert response.status_code == 403
    # ...and the staff password login still works.
    staff = client.post("/api/v1/auth/staff/login", json={"phone_no": phone, "password": "a-real-password"})
    assert staff.status_code == 200


def test_the_password_login_and_register_are_unchanged(welcomes):
    credentials = {"name": "Asha", "phone_no": f"+1555{uuid.uuid4().int % 10**7:07d}", "password": "correct-horse"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    login = client.post("/api/v1/auth/login", json={"phone_no": credentials["phone_no"], "password": credentials["password"]})
    assert login.status_code == 200


def test_registration_by_phone_is_audited_without_the_phone_number(welcomes):
    from app.models.audit_log import AuditLog

    phone = _number()
    auth_id = client.post("/api/v1/auth/phone-login", json={"phone_no": phone}).json()["auth_id"]
    db = next(app.dependency_overrides[get_db]())
    entries = db.query(AuditLog).filter(AuditLog.user_id == auth_id).all()
    db.close()
    assert [e.action for e in entries] == ["register_user"]
    assert phone not in str(entries[0].after_value) and phone not in str(entries[0].relevant_metadata)
