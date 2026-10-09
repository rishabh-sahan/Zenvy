"""Reminders, follow-ups, cancel and reschedule against a REAL PostgreSQL database.

SQLite cannot show that two schedulers will not both send a reminder, or that a
reschedule is all-or-nothing under a race. These tests use real threads.
Skipped unless TEST_POSTGRES_URL is set; they refuse non-local databases because
they DROP and recreate the schema (see test_slots_postgres.py for how to run).
"""
import os
import threading
import uuid
from datetime import time, timedelta
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.migrations import run_migrations
from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.authentication import Authentication
from app.models.doctor import Doctor, DoctorSchedule
from app.models.doctor_slot import DoctorSlot
from app.models.hospital import Hospital
from app.models.reminder import Reminder
from app.models.session import Session as SessionModel
from app.schemas.ai_appointment import AIAppointmentCreate
from app.services import reminder_service
from app.services.appointment_service import cancel_appointment, create_appointment, reschedule_appointment
from app.services.scheduler import Scheduler
from app.services.slot_service import IST, SlotUnavailableError, hold_slot, list_free_slots, utcnow

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "local-postgres"}

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")

CONTENDERS = 20


@pytest.fixture(scope="module")
def factory():
    host = urlparse(POSTGRES_URL.replace("+psycopg", "")).hostname
    assert host in LOCAL_HOSTS, f"Refusing to wipe a non-local database ({host})"
    engine = create_engine(POSTGRES_URL, future=True, pool_size=CONTENDERS + 10)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    run_migrations(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    engine.dispose()


def race(count, work):
    """Run ``work(i)`` in ``count`` threads released at the same instant."""
    barrier = threading.Barrier(count)
    outcomes = [None] * count

    def runner(index):
        barrier.wait()
        try:
            outcomes[index] = ("ok", work(index))
        except Exception as exc:  # noqa: BLE001
            outcomes[index] = ("error", exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def make_clinic(factory, patients=1):
    """A doctor (with a staff login) and `patients` patients each holding their own appointment."""
    db = factory()
    suffix = uuid.uuid4().hex[:8]
    staff = Authentication(name="Dr", phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}", password_hash="x", role="staff")
    hospital = Hospital(hospital_id=f"hos-{suffix}", name=f"H {suffix}", city="mysore")
    db.add_all([staff, hospital])
    db.flush()
    doctor = Doctor(doctor_id=f"doc-{suffix}", hospital_id=hospital.hospital_id, auth_id=staff.auth_id, name=f"Dr. {suffix}", specialty="Cardiologist")
    db.add(doctor)
    db.flush()
    for weekday in range(7):
        db.add(DoctorSchedule(schedule_id=str(uuid.uuid4()), doctor_id=doctor.doctor_id, weekday=weekday, start_time=time(8, 0), end_time=time(20, 0)))
    db.commit()

    day = (utcnow().astimezone(IST) + timedelta(days=3)).date()
    slots = list_free_slots(db, db.get(Doctor, doctor.doctor_id), day)
    appointments = []
    for index in range(patients):
        patient = Authentication(name=f"P{index}", phone_no=str(9_000_000_000 + uuid.uuid4().int % 99_999_999), password_hash="!x")
        db.add(patient)
        db.flush()
        session = SessionModel(session_id=f"s-{uuid.uuid4().hex[:10]}", user_id=patient.auth_id, channel="web", language="en")
        db.add(session)
        db.commit()
        hold_slot(db, slots[index].slot_id, session.session_id)
        appointment = create_appointment(
            db,
            AIAppointmentCreate(session_id=session.session_id, patient_uhid="U", slot_id=slots[index].slot_id, status="confirmed"),
            patient_phone_no=patient.phone_no,
        )
        appointments.append(appointment.appointment_id)
    result = {"doctor_id": doctor.doctor_id, "slots": [s.slot_id for s in slots], "appointments": appointments}
    db.close()
    return result


def test_migration_011_created_what_it_should(factory):
    inspector = inspect(factory().get_bind())
    assert {"reminders", "follow_ups"} <= set(inspector.get_table_names())
    columns = {c["name"] for c in inspector.get_columns("ai_appointments")}
    assert {"cancelled_at", "cancel_reason", "cancelled_by", "rescheduled_from_id"} <= columns
    assert "idx_reminders_due" in {i["name"] for i in inspector.get_indexes("reminders")}


def test_the_database_itself_rejects_bad_reminders_and_follow_ups(factory):
    clinic = make_clinic(factory)
    db = factory()
    appointment_id = clinic["appointments"][0]

    def refuses(sql, **values):
        with pytest.raises(IntegrityError):
            db.execute(text(sql), values)
            db.commit()
        db.rollback()

    base = "INSERT INTO reminders (reminder_id, appointment_id, recipient_type, kind, send_at, status) VALUES (:i, :a, :r, :k, NOW(), :s)"
    refuses(base, i=str(uuid.uuid4()), a=appointment_id, r="stranger", k="reminder_2h", s="pending")
    refuses(base, i=str(uuid.uuid4()), a=appointment_id, r="patient", k="telepathy", s="pending")
    refuses(base, i=str(uuid.uuid4()), a=appointment_id, r="patient", k="reminder_2h", s="maybe")

    ok = {"a": appointment_id, "r": "patient", "k": "cancelled", "s": "pending"}
    db.execute(text(base), {"i": str(uuid.uuid4()), **ok})
    db.commit()
    refuses(base, i=str(uuid.uuid4()), **ok)   # the same message twice
    db.close()


def test_twenty_schedulers_claiming_one_reminder_give_exactly_one_winner(factory):
    clinic = make_clinic(factory)
    db = factory()
    row = reminder_service._ensure(db, clinic["appointments"][0], "patient", None, "booked", utcnow() - timedelta(minutes=1))
    db.commit()
    reminder_id = row.reminder_id
    db.close()

    def attempt(index):
        session = factory()
        try:
            return reminder_service._claim(session, reminder_id)
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" for kind, _ in outcomes)
    assert [won for _, won in outcomes].count(True) == 1


def test_twenty_schedulers_processing_the_same_due_reminders_send_each_once(factory):
    clinic = make_clinic(factory)
    appointment_id = clinic["appointments"][0]
    db = factory()
    appointment = db.get(AIAppointment, appointment_id)
    reminder_service.schedule_for_appointment(db, appointment)
    due = utcnow() + timedelta(days=10)       # everything is due
    db.close()

    def attempt(index):
        session = factory()
        try:
            return reminder_service.process_due(session, now=due - timedelta(days=9), appointment_id=appointment_id)
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    handled = sum(sum(counts.values()) for _, counts in outcomes)

    db = factory()
    rows = db.query(Reminder).filter(Reminder.appointment_id == appointment_id).all()
    db.close()
    # every row was handled by exactly one scheduler, exactly once
    assert handled == sum(1 for r in rows if r.status != "pending")
    assert all(r.attempts <= 1 for r in rows)
    assert len({r.reminder_id for r in rows}) == len(rows)
    sent = [r for r in rows if r.status == "sent"]
    assert sent and all(r.mode == "mock" and r.sent_at.tzinfo is not None for r in sent)


def test_one_scheduler_pass_runs_on_postgres(factory):
    clinic = make_clinic(factory)
    db = factory()
    reminder_service.schedule_for_appointment(db, db.get(AIAppointment, clinic["appointments"][0]))
    db.close()
    result = Scheduler(session_factory=factory).tick(utcnow() + timedelta(days=30))
    assert result["reminders"]            # something was processed
    assert result["purged"] == {"audio": 0, "transcripts": 0}


def test_twenty_patients_racing_to_reschedule_into_one_slot_leave_everyone_with_an_appointment(factory):
    clinic = make_clinic(factory, patients=CONTENDERS)
    target = clinic["slots"][CONTENDERS + 3]           # one free slot nobody holds yet

    def attempt(index):
        session = factory()
        try:
            appointment = session.get(AIAppointment, clinic["appointments"][index])
            return reschedule_appointment(session, appointment, target, cancelled_by="patient").appointment_id
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    winners = [o for o in outcomes if o[0] == "ok"]
    losers = [o for o in outcomes if o[0] == "error"]
    assert len(winners) == 1, outcomes
    assert all(isinstance(o[1], SlotUnavailableError) for o in losers), [o[1] for o in losers]

    db = factory()
    old = [db.get(AIAppointment, a) for a in clinic["appointments"]]
    cancelled = [a for a in old if a.status == AppointmentStatus.cancelled]
    kept = [a for a in old if a.status != AppointmentStatus.cancelled]
    assert len(cancelled) == 1 and len(kept) == CONTENDERS - 1       # only the winner's old booking was released
    for appointment in kept:                                          # nobody lost anything
        assert db.get(DoctorSlot, appointment.slot_id).status == "booked"
    assert db.get(DoctorSlot, cancelled[0].slot_id).status == "available"
    assert db.get(DoctorSlot, target).status == "booked"
    replacements = db.query(AIAppointment).filter(AIAppointment.slot_id == target, AIAppointment.status != AppointmentStatus.cancelled).all()
    assert len(replacements) == 1 and replacements[0].rescheduled_from_id == cancelled[0].appointment_id
    db.close()


def test_a_rescheduled_appointment_has_no_overlap_with_anything_the_database_would_reject(factory):
    """The unique index behind the lock still holds after cancel + rebook of the same slot."""
    clinic = make_clinic(factory)
    db = factory()
    original = db.get(AIAppointment, clinic["appointments"][0])
    slot_id = original.slot_id
    cancel_appointment(db, original, cancelled_by="patient")
    session_id = original.session_id
    hold_slot(db, slot_id, session_id)
    again = create_appointment(db, AIAppointmentCreate(session_id=session_id, patient_uhid="U", slot_id=slot_id), patient_phone_no=original.patient_phone_no)
    assert again.appointment_id != original.appointment_id
    assert db.get(DoctorSlot, slot_id).status == "booked"
    db.close()


def test_cancelling_stops_pending_reminders_and_queues_notices_on_postgres(factory):
    clinic = make_clinic(factory)
    db = factory()
    appointment = db.get(AIAppointment, clinic["appointments"][0])
    reminder_service.schedule_for_appointment(db, appointment)
    cancel_appointment(db, appointment, cancelled_by="patient")
    rows = {(r.recipient_type, r.kind): r.status for r in db.query(Reminder).filter(Reminder.appointment_id == appointment.appointment_id)}
    db.close()
    assert rows[("doctor", "reminder_2h")] == "cancelled"
    assert rows[("doctor", "cancelled")] == "pending" and rows[("patient", "cancelled")] == "pending"
