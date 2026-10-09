"""Doctor search, slot listing, slot holds and slot-locked booking (SQLite)."""

import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.db.deps import get_db
from app.main import app
from app.models.ai_appointment import AIAppointment
from app.models.doctor import Doctor, DoctorSchedule
from app.models.doctor_slot import DoctorSlot
from app.models.hospital import Hospital
from app.services.slot_service import IST, as_utc, utcnow

client = TestClient(app)


def _db():
    """A session on the same in-memory test database the API uses."""
    generator = app.dependency_overrides[get_db]()
    return next(generator)


def _tomorrow() -> datetime:
    return (utcnow().astimezone(IST) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


@pytest.fixture
def doctor():
    """A doctor working 08:00-20:00 IST every day (24 half-hour slots a day)."""
    db = _db()
    suffix = uuid.uuid4().hex[:8]
    hospital = Hospital(hospital_id=f"hos-{suffix}", name=f"Test Hospital {suffix}", city="mysore")
    record = Doctor(
        doctor_id=f"doc-{suffix}",
        hospital_id=hospital.hospital_id,
        name=f"Dr. Slot Tester {suffix}",
        specialty="Cardiologist",
        slot_minutes=30,
    )
    db.add_all([hospital, record])
    db.flush()
    for weekday in range(7):
        db.add(
            DoctorSchedule(
                schedule_id=str(uuid.uuid4()),
                doctor_id=record.doctor_id,
                weekday=weekday,
                start_time=time(8, 0),
                end_time=time(20, 0),
            )
        )
    db.commit()
    result = {"id": record.doctor_id, "name": record.name, "hospital": hospital.name}
    db.close()
    return result


def _session(user_id="user_slots"):
    response = client.post(
        "/api/v1/sessions",
        json={"user_id": user_id, "channel": "web", "language": "en", "uhid": "UHID-SLOT"},
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def _free_slots(doctor_id, day=None, **params):
    day = day or _tomorrow()
    response = client.get(
        f"/api/v1/doctors/{doctor_id}/slots", params={"date": day.date().isoformat(), **params}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _book(session_id, slot_id):
    return client.post(
        "/api/v1/appointments",
        json={
            "session_id": session_id,
            "patient_uhid": "UHID-SLOT",
            "slot_id": slot_id,
            "status": "confirmed",
        },
    )


# ---------------------------------------------------------------------------
# Doctor search
# ---------------------------------------------------------------------------

def test_doctor_search_by_name_title_and_department(doctor):
    by_name = client.get("/api/v1/doctors", params={"query": doctor["name"]}).json()
    assert [d["doctor_id"] for d in by_name] == [doctor["id"]]
    assert by_name[0]["hospital_name"] == doctor["hospital"]

    # "Dr." title and case are ignored.
    suffix = doctor["name"].split()[-1]
    assert client.get("/api/v1/doctors", params={"query": f"dr slot tester {suffix.upper()}"}).json()

    # The NLU returns department names like "Cardiology"; the doctor's specialty is "Cardiologist".
    by_department = client.get("/api/v1/doctors", params={"query": "Cardiology"}).json()
    assert doctor["id"] in [d["doctor_id"] for d in by_department]

    assert client.get("/api/v1/doctors", params={"query": "Nobody Atall Zzz"}).json() == []


def test_doctor_search_tolerates_a_misspelled_name(doctor):
    misspelled = doctor["name"].replace("Tester", "Testar")
    found = client.get("/api/v1/doctors", params={"query": misspelled}).json()
    assert doctor["id"] in [d["doctor_id"] for d in found]


def test_unknown_doctor_slots_returns_404():
    response = client.get("/api/v1/doctors/does-not-exist/slots", params={"date": "2030-01-01"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Slot listing
# ---------------------------------------------------------------------------

def test_slots_are_generated_from_the_schedule_in_ist(doctor):
    slots = _free_slots(doctor["id"])
    assert len(slots) == 24
    assert slots[0]["slot_start"].endswith("08:00:00+05:30")
    assert slots[-1]["slot_start"].endswith("19:30:00+05:30")
    starts = [s["slot_start"] for s in slots]
    assert starts == sorted(starts)
    assert {s["status"] for s in slots} == {"available"}


def test_listing_twice_does_not_duplicate_slots(doctor):
    _free_slots(doctor["id"])
    _free_slots(doctor["id"])
    db = _db()
    assert db.query(DoctorSlot).filter(DoctorSlot.doctor_id == doctor["id"]).count() == 24
    db.close()


def test_past_slots_are_never_offered(doctor):
    today = utcnow().astimezone(IST)
    now = utcnow()
    for slot in _free_slots(doctor["id"], today):
        assert datetime.fromisoformat(slot["slot_start"]) > now


def test_days_beyond_the_booking_horizon_have_no_slots(doctor):
    far = _tomorrow() + timedelta(days=60)
    assert _free_slots(doctor["id"], far) == []


def test_near_returns_the_closest_free_slots_in_time_order(doctor):
    slots = _free_slots(doctor["id"], near="10:10", limit=3)
    assert [s["slot_start"][11:16] for s in slots] == ["09:30", "10:00", "10:30"]


# ---------------------------------------------------------------------------
# Holding
# ---------------------------------------------------------------------------

def test_hold_removes_the_slot_from_everyone_elses_list(doctor):
    first, second = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[4]

    held = client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": first})
    assert held.status_code == 200
    assert held.json()["status"] == "held"
    assert held.json()["held_until"] is not None

    assert slot["slot_id"] not in [s["slot_id"] for s in _free_slots(doctor["id"])]

    blocked = client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": second})
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "slot_unavailable"


def test_the_holder_can_hold_again_to_refresh(doctor):
    session_id = _session()
    slot = _free_slots(doctor["id"])[0]
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": session_id}).status_code == 200
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": session_id}).status_code == 200


def test_an_expired_hold_can_be_taken_by_someone_else(doctor):
    first, second = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[1]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": first})

    db = _db()
    row = db.get(DoctorSlot, slot["slot_id"])
    row.held_until = utcnow() - timedelta(minutes=1)
    db.commit()
    db.close()

    # Expired holds are offered again, and can be claimed.
    assert slot["slot_id"] in [s["slot_id"] for s in _free_slots(doctor["id"])]
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": second}).status_code == 200
    # ...which means the first patient can no longer book it.
    assert _book(first, slot["slot_id"]).status_code == 409


def test_hold_needs_a_real_session_and_slot(doctor):
    slot = _free_slots(doctor["id"])[0]
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": "nope"}).status_code == 404
    assert client.post("/api/v1/slots/nope/hold", json={"session_id": _session()}).status_code == 404


def test_release_frees_the_slot_and_is_idempotent(doctor):
    holder, other = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[2]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": holder})

    # Someone else cannot release it.
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/release", json={"session_id": other}).json() == {"released": False}
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/release", json={"session_id": holder}).json() == {"released": True}
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/release", json={"session_id": holder}).json() == {"released": False}
    assert slot["slot_id"] in [s["slot_id"] for s in _free_slots(doctor["id"])]


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def test_booking_a_held_slot_locks_it_for_everyone(doctor):
    patient, other = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[6]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})

    booked = _book(patient, slot["slot_id"])
    assert booked.status_code == 201
    body = booked.json()
    # Doctor and time come from the slot, not from the caller.
    assert body["doctor_name"] == doctor["name"]
    assert body["doctor_id"] == doctor["id"]
    assert body["slot_id"] == slot["slot_id"]
    assert as_utc(datetime.fromisoformat(body["appointment_datetime"])) == as_utc(
        datetime.fromisoformat(slot["slot_start"])
    )
    # The hospital is passed on so the WhatsApp confirmation can show where to go.
    assert body["booking_info"]["location"] == doctor["hospital"]

    assert slot["slot_id"] not in [s["slot_id"] for s in _free_slots(doctor["id"])]
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": other}).status_code == 409
    assert _book(other, slot["slot_id"]).status_code == 409

    db = _db()
    assert db.get(DoctorSlot, slot["slot_id"]).status == "booked"
    db.close()


def test_booking_needs_the_slot_to_be_held_by_the_same_session(doctor):
    patient, other = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[7]

    # Never held.
    assert _book(patient, slot["slot_id"]).status_code == 409
    # Held by someone else.
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": other})
    assert _book(patient, slot["slot_id"]).status_code == 409
    # A failed attempt must not have changed the slot.
    db = _db()
    row = db.get(DoctorSlot, slot["slot_id"])
    assert (row.status, row.held_by_session) == ("held", other)
    db.close()


def test_booking_an_unknown_slot_is_404(doctor):
    assert _book(_session(), "no-such-slot").status_code == 404


def test_a_booked_slot_can_only_have_one_active_appointment(doctor):
    patient = _session()
    slot = _free_slots(doctor["id"])[8]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    assert _book(patient, slot["slot_id"]).status_code == 201

    # Even if application code were bypassed, the database refuses a second one.
    db = _db()
    row = db.get(DoctorSlot, slot["slot_id"])
    duplicate = AIAppointment(
        appointment_id=str(uuid.uuid4()),
        session_id=patient,
        patient_uhid="UHID-SLOT",
        doctor_name=doctor["name"],
        appointment_datetime=row.slot_start,
        doctor_id=doctor["id"],
        slot_id=slot["slot_id"],
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    db.close()


def test_slot_is_required_when_the_setting_is_on(doctor, monkeypatch):
    monkeypatch.setattr("app.api.routes.appointments.settings.REQUIRE_SLOT_FOR_BOOKING", True)
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": _session(),
            "patient_uhid": "UHID-SLOT",
            "doctor_name": "Dr. Free Text",
            "appointment_datetime": "2030-01-01T10:00:00+05:30",
        },
    )
    assert response.status_code == 422


def test_booking_without_a_slot_still_works_by_default():
    response = client.post(
        "/api/v1/appointments",
        json={
            "session_id": _session(),
            "patient_uhid": "UHID-SLOT",
            "doctor_name": "Dr. Free Text",
            "appointment_datetime": "2030-01-01T10:00:00+05:30",
        },
    )
    assert response.status_code == 201
    assert response.json()["slot_id"] is None


def test_without_a_slot_a_doctor_and_time_are_required():
    response = client.post(
        "/api/v1/appointments", json={"session_id": _session(), "patient_uhid": "UHID-SLOT"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------

def test_cancelling_frees_the_slot_for_the_next_patient(doctor):
    patient, next_patient = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[10]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    appointment_id = _book(patient, slot["slot_id"]).json()["appointment_id"]

    cancelled = client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={"session_id": patient})
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    assert slot["slot_id"] in [s["slot_id"] for s in _free_slots(doctor["id"])]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": next_patient})
    assert _book(next_patient, slot["slot_id"]).status_code == 201


def test_cancel_is_idempotent(doctor):
    patient = _session()
    slot = _free_slots(doctor["id"])[11]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    appointment_id = _book(patient, slot["slot_id"]).json()["appointment_id"]
    for _ in range(2):
        response = client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={"session_id": patient})
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"


def test_only_the_booking_session_or_staff_can_cancel(doctor):
    patient, stranger = _session("u1"), _session("u2")
    slot = _free_slots(doctor["id"])[12]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    appointment_id = _book(patient, slot["slot_id"]).json()["appointment_id"]

    assert client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={"session_id": stranger}).status_code == 403
    assert client.post(f"/api/v1/appointments/{appointment_id}/cancel").status_code == 403
    assert client.post("/api/v1/appointments/missing/cancel", json={"session_id": patient}).status_code == 404

    # Still booked.
    db = _db()
    assert db.get(DoctorSlot, slot["slot_id"]).status == "booked"
    db.close()


def test_staff_can_cancel_any_appointment(doctor):
    from app.models.authentication import Authentication
    from app.core.security import create_access_token
    from app.services.authentication_service import hash_password

    patient = _session()
    slot = _free_slots(doctor["id"])[13]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    appointment_id = _book(patient, slot["slot_id"]).json()["appointment_id"]

    db = _db()
    staff = Authentication(
        name="Front Desk",
        phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}",
        password_hash=hash_password("not-used"),
        role="staff",
    )
    db.add(staff)
    db.commit()
    token = create_access_token(staff)
    db.close()

    response = client.post(
        f"/api/v1/appointments/{appointment_id}/cancel",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert slot["slot_id"] in [s["slot_id"] for s in _free_slots(doctor["id"])]


def test_cancel_writes_an_audit_entry(doctor):
    patient = _session()
    slot = _free_slots(doctor["id"])[14]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": patient})
    appointment_id = _book(patient, slot["slot_id"]).json()["appointment_id"]
    client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={"session_id": patient})

    from app.models.audit_log import AuditLog

    db = _db()
    actions = [a.action for a in db.query(AuditLog).filter(AuditLog.session_id == patient).all()]
    db.close()
    assert "create_appointment" in actions
    assert "cancel_appointment" in actions
