"""Reminders and notices: what is created, when, how it is sent and what happens on failure."""
import json
from datetime import timedelta

import pytest

from app.core.config import settings
from app.models.ai_appointment import AIAppointment
from app.models.reminder import Reminder
from app.services import reminder_service
from app.services.scheduler import Scheduler
from app.services.slot_service import as_utc, utcnow
from helpers_stage3 import book, client, db, free_slots, make_doctor, phone, quiet_world, quiet_world, world  # noqa: F401 (fixtures)


def rows(appointment_id, **filters):
    session = db()
    query = session.query(Reminder).filter(Reminder.appointment_id == appointment_id)
    for key, value in filters.items():
        query = query.filter(getattr(Reminder, key) == value)
    result = query.all()
    session.expunge_all()
    session.close()
    return result


def by_kind(appointment_id):
    return {(r.recipient_type, r.kind): r for r in rows(appointment_id)}


def isolate(appointment_id):
    """Cancel everyone else's pending reminders: all tests share one database."""
    from sqlalchemy import update

    session = db()
    session.execute(
        update(Reminder)
        .where(Reminder.appointment_id != appointment_id, Reminder.status == "pending")
        .values(status="cancelled")
    )
    session.commit()
    session.close()


def start_of(appointment_id):
    session = db()
    value = as_utc(session.get(AIAppointment, appointment_id).appointment_datetime)
    session.close()
    return value


# ---------------------------------------------------------------------------
# what is created when an appointment is booked
# ---------------------------------------------------------------------------

def test_a_booking_creates_reminders_for_the_patient_and_the_doctor(world):
    found = by_kind(world["appointment_id"])
    assert set(found) == {
        ("patient", "booked"),                       # the existing booking confirmation, recorded
        ("patient", "reminder_24h"), ("patient", "reminder_2h"),
        ("doctor", "booked"),                        # "a new appointment exists"
        ("doctor", "reminder_24h"), ("doctor", "reminder_2h"),
    }


def test_the_reminders_are_timed_24_and_2_hours_before(world):
    start = start_of(world["appointment_id"])
    found = by_kind(world["appointment_id"])
    for who in ("patient", "doctor"):
        assert abs((as_utc(found[(who, "reminder_24h")].send_at) - (start - timedelta(hours=24))).total_seconds()) < 2
        assert abs((as_utc(found[(who, "reminder_2h")].send_at) - (start - timedelta(hours=2))).total_seconds()) < 2


def test_the_patients_own_booking_confirmation_is_recorded_as_sent(world):
    row = by_kind(world["appointment_id"])[("patient", "booked")]
    assert row.status == "sent" and row.mode == "live"


def test_a_failed_booking_confirmation_is_recorded_as_failed(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("Meta WhatsApp API request failed")

    monkeypatch.setattr("app.api.routes.appointments.send_appointment_notification", broken)
    session = db()
    doctor = make_doctor(session)
    session.close()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": phone()}).json()
    slot = free_slots(doctor["id"], 3)[0]
    session_id = client.post("/api/v1/sessions", json={"user_id": patient["auth_id"], "channel": "web", "language": "en"}).json()["session_id"]
    client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": session_id})
    response = client.post("/api/v1/appointments", json={"session_id": session_id, "patient_uhid": "U", "slot_id": slot["slot_id"]})
    assert response.status_code == 502  # unchanged behaviour: saved, but the confirmation failed
    appointment_id = client.get(f"/api/v1/appointments/session/{session_id}").json()[0]["appointment_id"]
    row = by_kind(appointment_id)[("patient", "booked")]
    assert row.status == "failed" and "WhatsApp" in row.last_error


def test_a_booking_made_less_than_24_hours_ahead_has_no_24_hour_reminder(world):
    session = db()
    appointment = session.get(AIAppointment, world["appointment_id"])
    now = as_utc(appointment.appointment_datetime) - timedelta(hours=5)
    for old in session.query(Reminder).filter(Reminder.appointment_id == appointment.appointment_id).all():
        session.delete(old)
    session.commit()
    reminder_service.schedule_for_appointment(session, appointment, now=now)
    kinds = {(r.recipient_type, r.kind) for r in session.query(Reminder).filter(Reminder.appointment_id == appointment.appointment_id)}
    session.close()
    assert kinds == {("patient", "reminder_2h"), ("doctor", "booked"), ("doctor", "reminder_2h")}


def test_a_booking_made_less_than_2_hours_ahead_only_notifies_the_doctor(world):
    session = db()
    appointment = session.get(AIAppointment, world["appointment_id"])
    for old in session.query(Reminder).filter(Reminder.appointment_id == appointment.appointment_id).all():
        session.delete(old)
    session.commit()
    reminder_service.schedule_for_appointment(session, appointment, now=as_utc(appointment.appointment_datetime) - timedelta(minutes=30))
    kinds = {(r.recipient_type, r.kind) for r in session.query(Reminder).filter(Reminder.appointment_id == appointment.appointment_id)}
    session.close()
    assert kinds == {("doctor", "booked")}


def test_scheduling_twice_does_not_duplicate(world):
    session = db()
    appointment = session.get(AIAppointment, world["appointment_id"])
    assert reminder_service.schedule_for_appointment(session, appointment) == []
    session.close()
    assert len(rows(world["appointment_id"])) == 6


def test_no_phone_number_is_stored_in_the_reminders_table(world):
    dump = json.dumps([[r.recipient_auth_id, r.details, r.message_text, r.last_error] for r in rows(world["appointment_id"])], default=str)
    assert world["phone"] not in dump


# ---------------------------------------------------------------------------
# sending (mock mode)
# ---------------------------------------------------------------------------

def due_now(world, hours_before):
    return start_of(world["appointment_id"]) - timedelta(hours=hours_before) + timedelta(seconds=5)


def test_nothing_is_sent_before_it_is_due(world):
    session = db()
    result = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 25))
    session.close()
    # Only the doctor's "new appointment" notice is due (it is due the moment of booking).
    assert result == {"sent": 1}
    assert by_kind(world["appointment_id"])[("doctor", "booked")].status == "sent"
    assert by_kind(world["appointment_id"])[("patient", "reminder_24h")].status == "pending"
    assert by_kind(world["appointment_id"])[("doctor", "reminder_24h")].status == "pending"


def test_a_due_reminder_is_sent_in_mock_mode_and_recorded(world):
    session = db()
    result = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    assert result == {"sent": 3}   # the patient's 24h, the doctor's 24h, and the doctor's "new appointment" notice (due at once)
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert row.status == "sent" and row.mode == "mock" and row.attempts == 1
    assert row.provider_message_id.startswith("mock-")
    assert world["doctor"]["name"] in row.message_text and "tomorrow" in row.message_text
    # the 2-hour reminders are still waiting
    assert by_kind(world["appointment_id"])[("patient", "reminder_2h")].status == "pending"


def test_the_doctors_notice_names_the_patient_masked_never_by_number(world):
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    message = by_kind(world["appointment_id"])[("doctor", "reminder_24h")].message_text
    assert f"Patient ••••{world['phone'][-4:]}" in message
    assert world["phone"] not in message


def test_a_reminder_is_sent_only_once(world):
    session = db()
    first = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    second = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    assert first["sent"] == 3 and second == {}


def test_two_schedulers_cannot_both_claim_one_reminder(world):
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    first, second = db(), db()
    assert reminder_service._claim(first, row.reminder_id) is True
    assert reminder_service._claim(second, row.reminder_id) is False
    first.close()
    second.close()


def test_a_reminder_is_skipped_once_the_appointment_has_started(world):
    session = db()
    result = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=start_of(world["appointment_id"]) + timedelta(minutes=1))
    session.close()
    assert result.get("skipped", 0) >= 5 and "sent" not in result
    row = by_kind(world["appointment_id"])[("patient", "reminder_2h")]
    assert row.status == "skipped" and "started" in row.last_error


def test_a_person_without_a_phone_number_is_skipped_not_failed(world):
    session = db()
    from app.models.authentication import Authentication

    patient = session.query(Authentication).filter(Authentication.auth_id == world["patient_auth_id"]).one()
    patient.phone_no = ""   # no way to reach them
    session.commit()
    appointment = session.get(AIAppointment, world["appointment_id"])
    appointment.patient_phone_no = patient.phone_no
    session.commit()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert row.status == "skipped" and "phone" in row.last_error


# ---------------------------------------------------------------------------
# sending (live mode): success, one retry, give up
# ---------------------------------------------------------------------------

@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_MODE", "live")
    sent = []
    outcome = {"fail": 0}

    def fake_send(phone_no, template, parameters):
        if outcome["fail"] > 0:
            outcome["fail"] -= 1
            raise RuntimeError("Meta WhatsApp API request failed")
        sent.append((phone_no, template, dict(parameters)))
        return f"wamid.{len(sent)}"

    monkeypatch.setattr(reminder_service.whatsapp_service, "send_template", fake_send)
    return sent, outcome


def test_live_mode_sends_the_approved_template_with_named_parameters(world, live):
    sent, _ = live
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    patient_message = next(item for item in sent if item[0] == world["phone"])
    assert patient_message[1] == settings.META_WHATSAPP_REMINDER_TEMPLATE_NAME
    parameters = patient_message[2]
    assert parameters["doctor"] == world["doctor"]["name"] and parameters["when"] == "tomorrow"
    assert {"name", "doctor", "date", "time", "location", "when"} <= set(parameters)
    doctor_message = next(item for item in sent if item[1] == settings.META_WHATSAPP_DOCTOR_NOTICE_TEMPLATE_NAME)
    assert set(doctor_message[2]) == {"message"}
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert row.status == "sent" and row.mode == "live" and row.provider_message_id == "wamid.1" or row.provider_message_id.startswith("wamid.")


def test_a_failed_send_is_retried_once_after_the_wait_and_then_succeeds(world, live):
    sent, outcome = live
    when = due_now(world, 24)
    session = db()
    # the doctor's "new appointment" notice goes out first and is not what this test is about
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when - timedelta(hours=2))
    sent.clear()
    outcome["fail"] = 1
    first = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when)
    row = next(r for r in rows(world["appointment_id"]) if r.recipient_type == "patient" and r.kind == "reminder_24h")
    assert first.get("pending", 0) == 1                                  # the failed one is waiting to retry
    assert row.status == "pending" and row.attempts == 1 and "failed" in row.last_error
    assert abs((as_utc(row.send_at) - (when + timedelta(minutes=settings.REMINDER_RETRY_MINUTES))).total_seconds()) < 2

    # too early for the retry: nothing happens
    assert reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when + timedelta(minutes=1)).get("sent", 0) == 0
    # after the wait it is sent
    later = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when + timedelta(minutes=settings.REMINDER_RETRY_MINUTES, seconds=5))
    session.close()
    assert later.get("sent", 0) >= 1
    final = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert final.status == "sent" and final.attempts == 2


def test_after_the_retry_fails_too_it_is_recorded_as_failed_and_not_tried_again(world, live):
    sent, outcome = live
    outcome["fail"] = 100
    when = due_now(world, 24)
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when)
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when + timedelta(minutes=settings.REMINDER_RETRY_MINUTES, seconds=5))
    third = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=when + timedelta(hours=1))
    session.close()
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert row.status == "failed" and row.attempts == 2 and row.last_error
    assert sent == [] and "sent" not in third


def test_live_mode_with_missing_meta_settings_fails_cleanly(world, monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_MODE", "live")
    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", None)
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    row = by_kind(world["appointment_id"])[("patient", "reminder_24h")]
    assert row.status == "pending" and "Missing Meta WhatsApp configuration" in row.last_error


# ---------------------------------------------------------------------------
# cancelling and rescheduling change what is pending
# ---------------------------------------------------------------------------

def test_cancelling_stops_pending_reminders_and_notifies_both(world):
    response = client.post(f"/api/v1/appointments/{world['appointment_id']}/cancel", json={"auth_id": world["patient_auth_id"]})
    assert response.status_code == 200
    found = by_kind(world["appointment_id"])
    assert found[("patient", "reminder_24h")].status == "cancelled"
    assert found[("doctor", "reminder_2h")].status == "cancelled"
    assert found[("patient", "cancelled")].status == "pending" and found[("doctor", "cancelled")].status == "pending"

    session = db()
    result = reminder_service.process_due(session, appointment_id=world["appointment_id"], now=utcnow() + timedelta(seconds=5))
    session.close()
    assert result == {"sent": 2}
    found = by_kind(world["appointment_id"])
    assert "has been cancelled" in found[("patient", "cancelled")].message_text
    assert found[("doctor", "cancelled")].message_text.startswith("Cancelled: Patient")


def test_a_cancelled_appointment_never_sends_its_old_reminders(world):
    client.post(f"/api/v1/appointments/{world['appointment_id']}/cancel", json={"auth_id": world["patient_auth_id"]})
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    sent_kinds = {k for k, r in by_kind(world["appointment_id"]).items() if r.status == "sent"}
    assert ("patient", "reminder_24h") not in sent_kinds and ("doctor", "reminder_2h") not in sent_kinds
    assert ("patient", "cancelled") in sent_kinds


def test_rescheduling_moves_the_reminders_to_the_new_appointment(world):
    new_slot = free_slots(world["doctor"]["id"], 4)[2]
    moved = client.post(
        f"/api/v1/appointments/{world['appointment_id']}/reschedule",
        json={"slot_id": new_slot["slot_id"], "auth_id": world["patient_auth_id"]},
    )
    assert moved.status_code == 200
    new_id = moved.json()["appointment_id"]

    old = by_kind(world["appointment_id"])
    assert old[("patient", "reminder_24h")].status == "cancelled" and old[("doctor", "reminder_2h")].status == "cancelled"

    new = by_kind(new_id)
    assert {("patient", "reminder_24h"), ("patient", "reminder_2h"), ("doctor", "reminder_24h"), ("doctor", "reminder_2h")} <= set(new)
    assert new[("patient", "rescheduled")].status == "pending" and new[("doctor", "rescheduled")].status == "pending"

    session = db()
    reminder_service.process_due(session, appointment_id=new_id, now=utcnow() + timedelta(seconds=5))
    session.close()
    new = by_kind(new_id)
    assert "moved to" in new[("patient", "rescheduled")].message_text
    assert "(was " in new[("doctor", "rescheduled")].message_text   # the doctor is told the old time too


# ---------------------------------------------------------------------------
# the scheduler
# ---------------------------------------------------------------------------

def make_scheduler():
    from app.db.deps import get_db
    from app.main import app

    return Scheduler(session_factory=lambda: next(app.dependency_overrides[get_db]()))


def test_one_scheduler_pass_sends_due_reminders_and_purges_once_a_day(world, monkeypatch):
    purges = []
    from app.services import consultation_service

    monkeypatch.setattr(consultation_service, "purge_expired", lambda db, now=None: purges.append(now) or {"audio": 0, "transcripts": 0})
    scheduler = make_scheduler()
    when = due_now(world, 24)
    isolate(world["appointment_id"])

    first = scheduler.tick(when)
    assert first["reminders"].get("sent") == 3
    assert first["purged"] == {"audio": 0, "transcripts": 0} and len(purges) == 1

    scheduler.tick(when + timedelta(minutes=5))
    assert len(purges) == 1                                   # not again within the day
    scheduler.tick(when + timedelta(hours=settings.PURGE_INTERVAL_HOURS, minutes=1))
    assert len(purges) == 2                                   # the next day


def test_a_failure_inside_the_scheduler_never_escapes(monkeypatch):
    def boom(db, now=None, limit=100):
        raise RuntimeError("database went away")

    monkeypatch.setattr(reminder_service, "process_due", boom)
    result = make_scheduler().tick()
    assert result["reminders"] == {}


def test_the_scheduler_thread_starts_and_stops(monkeypatch):
    monkeypatch.setattr(settings, "REMINDER_POLL_SECONDS", 1)
    scheduler = make_scheduler()
    scheduler.start()
    assert scheduler.running
    scheduler.stop()
    assert not scheduler.running
    assert scheduler.last_tick is not None


def test_the_scheduler_status_endpoint_works():
    body = client.get("/healthz/scheduler").json()
    assert body["reminder_mode"] == "mock" and "running" in body
    assert client.get("/healthz").json().keys() == {"status", "redis"}   # the old contract is untouched


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------

def test_the_history_lists_everything_recorded_about_an_appointment(world):
    session = db()
    reminder_service.process_due(session, appointment_id=world["appointment_id"], now=due_now(world, 24))
    session.close()
    history = client.get(f"/api/v1/appointments/{world['appointment_id']}/history", headers=world["doctor"]["headers"])
    assert history.status_code == 200
    body = history.json()
    assert body["status"] == "confirmed" and body["consent_state"] == "none"
    kinds = {(r["recipient"], r["kind"]): r["status"] for r in body["reminders"]}
    assert kinds[("patient", "reminder_24h")] == "sent" and kinds[("doctor", "reminder_2h")] == "pending"
    assert any(e["kind"] == "booked" for e in body["events"])
    assert world["phone"] not in json.dumps(body)


def test_only_the_treating_doctor_can_read_the_history(world):
    url = f"/api/v1/appointments/{world['appointment_id']}/history"
    assert client.get(url, headers=world["other"]["headers"]).status_code == 403
    assert client.get(url).status_code == 401
    assert client.get("/api/v1/appointments/missing/history", headers=world["doctor"]["headers"]).status_code == 404
