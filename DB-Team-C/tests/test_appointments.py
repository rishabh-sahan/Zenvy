from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _create_session(uhid="UHID-200"):
    response = client.post(
        "/api/v1/sessions",
        json={"user_id": "user_apt", "channel": "web", "language": "en", "uhid": uhid},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def test_appointments_endpoint_exists():
    response = client.get("/api/v1/appointments/session/nonexistent")
    assert response.status_code in (200, 404)


def test_create_appointment_requires_valid_payload():
    session_id = _create_session()
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-200",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
            "status": "pending",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == session_id
    assert body["patient_uhid"] == "UHID-200"
    assert body["doctor_name"] == "Dr. Rao"
    assert body["status"] == "pending"


def test_create_appointment_notifies_authenticated_phone(monkeypatch):
    phone_no = "+15550001111"
    auth_response = client.post(
        "/api/v1/auth/register",
        json={"name": "Asha Rao", "phone_no": phone_no, "password": "correct-horse"},
    )
    session_response = client.post(
        "/api/v1/sessions",
        json={"user_id": auth_response.json()["auth_id"], "channel": "web", "language": "en"},
    )
    notification = {}

    def fake_notification(recipient, appointment, patient_name):
        notification["recipient"] = recipient
        notification["appointment_id"] = appointment.appointment_id
        notification["patient_name"] = patient_name
        return "SM-test"

    monkeypatch.setattr(
        "app.api.routes.appointments.send_appointment_notification",
        fake_notification,
    )
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_response.json()["session_id"],
            "patient_uhid": "UHID-201",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )

    assert response.status_code == 201
    assert response.json()["patient_phone_no"] == phone_no
    assert notification == {
        "recipient": phone_no,
        "appointment_id": response.json()["appointment_id"],
        "patient_name": "Asha Rao",
    }


def test_create_appointment_rejects_unknown_session():
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": "missing-session",
            "patient_uhid": "UHID-200",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )
    assert response.status_code == 404


def test_create_appointment_rejects_invalid_status():
    session_id = _create_session()
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-200",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
            "status": "not-a-status",
        },
    )
    assert response.status_code == 422


def test_create_appointment_rejects_missing_required_fields():
    session_id = _create_session()
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-200",
        },
    )
    assert response.status_code == 422


def test_create_appointment_rejects_blank_patient_or_doctor():
    session_id = _create_session()
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "   ",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )
    assert response.status_code == 422
