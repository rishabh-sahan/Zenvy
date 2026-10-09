"""Shared set-up for the reminder / follow-up / cancel / reschedule tests (SQLite)."""
import base64
import io
import os
import uuid
import wave
from datetime import time, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.db.deps import get_db
from app.main import app
from app.models.authentication import Authentication
from app.models.doctor import Doctor, DoctorSchedule
from app.models.hospital import Hospital
from app.services.authentication_service import hash_password
from app.services.slot_service import IST, utcnow

client = TestClient(app)


def db():
    """A session on the same in-memory database the API uses."""
    return next(app.dependency_overrides[get_db]())


def phone() -> str:
    return str(9_000_000_000 + uuid.uuid4().int % 99_999_999)


def wav(seconds=1.0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x01\x02" * int(16000 * seconds))
    return buffer.getvalue()


def make_doctor(session, label="Doc"):
    """A doctor with a staff login who works 08:00-20:00 IST every day."""
    suffix = uuid.uuid4().hex[:8]
    staff = Authentication(
        name=f"Dr {label}", phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}",
        password_hash=hash_password("not-used-here"), role="staff",
    )
    hospital = Hospital(hospital_id=f"hos-{suffix}", name=f"Hospital {suffix}", city="mysore")
    session.add_all([staff, hospital])
    session.flush()
    doctor = Doctor(
        doctor_id=f"doc-{suffix}", hospital_id=hospital.hospital_id, auth_id=staff.auth_id,
        name=f"Dr. {label} {suffix}", specialty="Cardiologist", slot_minutes=30,
    )
    session.add(doctor)
    session.flush()
    for weekday in range(7):
        session.add(DoctorSchedule(
            schedule_id=str(uuid.uuid4()), doctor_id=doctor.doctor_id, weekday=weekday,
            start_time=time(8, 0), end_time=time(20, 0),
        ))
    session.commit()
    token = create_access_token(staff)
    return {
        "id": doctor.doctor_id, "name": doctor.name, "hospital": hospital.name,
        "auth_id": staff.auth_id, "headers": {"Authorization": f"Bearer {token}"},
    }


def day_ist(days_ahead: int):
    return (utcnow().astimezone(IST) + timedelta(days=days_ahead)).date()


def free_slots(doctor_id, days_ahead):
    return client.get(
        f"/api/v1/doctors/{doctor_id}/slots", params={"date": day_ist(days_ahead).isoformat()}
    ).json()


def make_session(user_id):
    response = client.post("/api/v1/sessions", json={"user_id": user_id, "channel": "web", "language": "en"})
    assert response.status_code == 201
    return response.json()["session_id"]


def book(doctor_id, patient_auth_id, slot_index=0, days_ahead=3):
    """Book through the real API (hold, then book). Returns (appointment_id, session_id, slot)."""
    slot = free_slots(doctor_id, days_ahead)[slot_index]
    session_id = make_session(patient_auth_id)
    held = client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": session_id})
    assert held.status_code == 200, held.text
    booked = client.post(
        "/api/v1/appointments",
        json={"session_id": session_id, "patient_uhid": "U", "slot_id": slot["slot_id"], "status": "confirmed"},
    )
    assert booked.status_code == 201, booked.text
    return booked.json()["appointment_id"], session_id, slot


@pytest.fixture(autouse=True)
def quiet_world(tmp_path, monkeypatch):
    """No real WhatsApp, a private audio folder and key, and mock reminders."""
    monkeypatch.setattr(settings, "AUDIO_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "AUDIO_STORAGE_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(settings, "REMINDER_MODE", "mock")
    monkeypatch.setattr("app.api.routes.appointments.send_appointment_notification", lambda *a, **k: "SM-test")


@pytest.fixture
def world():
    session = db()
    doctor = make_doctor(session, "Owner")
    other = make_doctor(session, "Other")
    session.close()
    patient_phone = phone()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": patient_phone}).json()
    appointment_id, session_id, slot = book(doctor["id"], patient["auth_id"])
    return {
        "doctor": doctor, "other": other, "phone": patient_phone, "patient_auth_id": patient["auth_id"],
        "appointment_id": appointment_id, "session_id": session_id, "slot": slot,
    }
