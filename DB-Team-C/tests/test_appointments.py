from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from twilio.base.exceptions import TwilioRestException

from app.main import app
from app.schemas.ai_appointment import AIAppointmentCreate

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


def test_create_appointment_notifies_authenticated_phone(monkeypatch):
    phone_no = "9876543210"
    login_response = client.post("/api/v1/auth/login", json={"phone_no": phone_no})
    assert login_response.status_code == 200
    auth_id = login_response.json()["auth_id"]

    session_response = client.post(
        "/api/v1/sessions",
        json={"user_id": auth_id, "channel": "web", "language": "en"},
    )
    assert session_response.status_code == 201
    session_id = session_response.json()["session_id"]

    notification = {}

    def fake_notification(recipient, appointment):
        notification["recipient"] = recipient
        notification["appointment_id"] = appointment.appointment_id
        return "SM-test"

    # Overrides the blanket no-op patch from conftest's autouse fixture, so
    # this test can assert on exactly what would have been sent.
    monkeypatch.setattr(
        "app.api.routes.appointments.send_appointment_notification",
        fake_notification,
    )

    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
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
    }


def test_booking_still_succeeds_when_whatsapp_send_fails(monkeypatch):
    """
    A failed WhatsApp send must not be reported as a failed booking.

    The appointment is already committed when the notification is attempted, so
    returning an error status made the gateway's orchestrator tell the patient
    "booking failed" and invite them to retry -- producing duplicate
    appointments while the original sat in the database.
    """
    phone_no = "9876543291"
    auth_id = client.post("/api/v1/auth/login", json={"phone_no": phone_no}).json()["auth_id"]
    session_id = client.post(
        "/api/v1/sessions",
        json={"user_id": auth_id, "channel": "web", "language": "en"},
    ).json()["session_id"]

    def twilio_rejects(recipient, appointment):
        raise TwilioRestException(status=400, uri="/Messages", msg="Recipient has not opted in")

    monkeypatch.setattr(
        "app.api.routes.appointments.send_appointment_notification",
        twilio_rejects,
    )

    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-203",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )

    assert response.status_code == 201, "a failed notification must not fail the booking"
    assert response.json()["notification_status"] == "failed"

    # And the booking that was saved is actually retrievable.
    listed = client.get(f"/api/v1/appointments/session/{session_id}").json()
    assert [a["appointment_id"] for a in listed] == [response.json()["appointment_id"]]


def test_create_appointment_reports_notification_sent(monkeypatch):
    phone_no = "9876543292"
    auth_id = client.post("/api/v1/auth/login", json={"phone_no": phone_no}).json()["auth_id"]
    session_id = client.post(
        "/api/v1/sessions",
        json={"user_id": auth_id, "channel": "web", "language": "en"},
    ).json()["session_id"]

    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-204",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )

    assert response.status_code == 201
    assert response.json()["notification_status"] == "sent"


def test_naive_appointment_time_is_read_as_ist_not_utc():
    """
    A datetime with no offset means IST, the hospital's timezone.

    Treating it as UTC (what Postgres does with a naive value in a TIMESTAMPTZ
    column) shifted every booking by 5h30m: a 3:00 PM appointment was stored,
    and confirmed over WhatsApp, as 8:30 PM.

    Asserted against the schema rather than through the API because the test
    database is SQLite, which drops tzinfo on round-trip and so cannot show the
    offset that real Postgres preserves.
    """
    naive = AIAppointmentCreate(
        session_id="s",
        patient_uhid="UHID-205",
        doctor_name="Dr. Rao",
        appointment_datetime="2026-08-24T15:00:00",  # no offset
    ).appointment_datetime

    aware = AIAppointmentCreate(
        session_id="s",
        patient_uhid="UHID-205",
        doctor_name="Dr. Rao",
        appointment_datetime="2026-08-24T15:00:00+05:30",
    ).appointment_datetime

    assert naive.utcoffset() == timedelta(hours=5, minutes=30)
    assert naive == aware, "naive input must mean the same instant as +05:30"
    assert naive.astimezone(timezone.utc).hour == 9, "3:00 PM IST is 09:30 UTC"


def test_appointment_time_survives_the_api_round_trip():
    """The naive value is not silently shifted on its way through the endpoint."""
    session_id = _create_session()

    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-206",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T15:00:00",
        },
    )

    assert response.status_code == 201
    assert datetime.fromisoformat(response.json()["appointment_datetime"]).hour == 15


def test_create_appointment_without_authenticated_session_skips_notification():
    """
    A session whose user_id doesn't match any Authentication row (e.g. an
    anonymous or pre-login session) should still allow booking -- it just
    gets no phone number and no notification, rather than failing.
    """
    session_id = _create_session()
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-202",
            "doctor_name": "Dr. Rao",
            "appointment_datetime": "2026-08-24T10:30:00+05:30",
        },
    )
    assert response.status_code == 201
    assert response.json()["patient_phone_no"] is None
    assert response.json()["notification_status"] == "skipped"
