"""Slot locking against a REAL PostgreSQL database.

SQLite (used by the other tests) cannot prove that two simultaneous requests
are serialised correctly, so these tests run real threads against Postgres.

They are skipped unless TEST_POSTGRES_URL is set, and they refuse to run
against anything that is not a local database, because they DROP and recreate
the schema. Start the disposable database and run them with:

    docker compose -f docker-compose.yml -f docker-compose.local-db.yml up -d local-postgres
    TEST_POSTGRES_URL=postgresql+psycopg://zenvy:zenvy_local_only@localhost:55432/zenvy_local \
        python -m pytest tests/test_slots_postgres.py -v
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

from app.db.migrations import run_migrations
from app.models.ai_appointment import AIAppointment
from app.models.doctor import Doctor, DoctorSchedule
from app.models.doctor_slot import DoctorSlot
from app.models.hospital import Hospital
from app.models.session import Session as SessionModel
from app.schemas.ai_appointment import AIAppointmentCreate
from app.services.appointment_service import cancel_appointment, create_appointment
from app.services.slot_service import (
    IST,
    SlotUnavailableError,
    hold_slot,
    list_free_slots,
    utcnow,
)

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "local-postgres"}

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")

CONTENDERS = 20


@pytest.fixture(scope="module")
def pg():
    host = urlparse(POSTGRES_URL.replace("+psycopg", "")).hostname
    assert host in LOCAL_HOSTS, f"Refusing to wipe a non-local database ({host})"
    engine = create_engine(POSTGRES_URL, future=True, pool_size=CONTENDERS + 5)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    run_migrations(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True), engine
    engine.dispose()


def _make_doctor(factory):
    db = factory()
    suffix = uuid.uuid4().hex[:8]
    hospital = Hospital(hospital_id=f"hos-{suffix}", name=f"PG Hospital {suffix}", city="mysore")
    doctor = Doctor(
        doctor_id=f"doc-{suffix}",
        hospital_id=hospital.hospital_id,
        name=f"Dr. PG {suffix}",
        specialty="Cardiologist",
    )
    db.add_all([hospital, doctor])
    db.flush()
    for weekday in range(7):
        db.add(
            DoctorSchedule(
                schedule_id=str(uuid.uuid4()),
                doctor_id=doctor.doctor_id,
                weekday=weekday,
                start_time=time(8, 0),
                end_time=time(20, 0),
            )
        )
    db.commit()
    doctor_id = doctor.doctor_id
    db.close()
    return doctor_id


def _tomorrow():
    return (utcnow().astimezone(IST) + timedelta(days=1)).date()


def _one_slot(factory):
    doctor_id = _make_doctor(factory)
    db = factory()
    doctor = db.get(Doctor, doctor_id)
    slot = list_free_slots(db, doctor, _tomorrow())[3]
    slot_id = slot.slot_id
    db.close()
    return doctor_id, slot_id


def _make_session(factory, session_id):
    db = factory()
    db.add(SessionModel(session_id=session_id, user_id="pg-user", channel="web", language="en"))
    db.commit()
    db.close()


def _race(count, work):
    """Run ``work(index)`` in ``count`` threads that all start at the same instant."""
    barrier = threading.Barrier(count)
    outcomes = [None] * count

    def runner(index):
        barrier.wait()
        try:
            outcomes[index] = ("ok", work(index))
        except Exception as exc:  # noqa: BLE001 - we want to classify every outcome
            outcomes[index] = ("error", exc)

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def test_migrations_create_the_new_tables_and_columns(pg):
    _, engine = pg
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"hospitals", "doctors", "doctor_schedules", "doctor_slots"} <= tables
    columns = {c["name"] for c in inspector.get_columns("ai_appointments")}
    assert {"doctor_id", "slot_id", "appointment_type", "parent_appointment_id"} <= columns
    indexes = {i["name"] for i in inspector.get_indexes("ai_appointments")}
    assert "uq_ai_appointments_active_slot" in indexes


def test_running_migrations_twice_is_harmless(pg):
    _, engine = pg
    run_migrations(engine)


def test_twenty_simultaneous_holds_give_exactly_one_winner(pg):
    factory, _ = pg
    _, slot_id = _one_slot(factory)

    def attempt(index):
        db = factory()
        try:
            return hold_slot(db, slot_id, f"racer-{index}").held_by_session
        finally:
            db.close()

    outcomes = _race(CONTENDERS, attempt)
    winners = [o for o in outcomes if o[0] == "ok"]
    losers = [o for o in outcomes if o[0] == "error"]
    assert len(winners) == 1, outcomes
    assert len(losers) == CONTENDERS - 1
    assert all(isinstance(o[1], SlotUnavailableError) for o in losers), [o[1] for o in losers]

    db = factory()
    row = db.get(DoctorSlot, slot_id)
    assert row.status == "held"
    assert row.held_by_session == winners[0][1]
    db.close()


def test_twenty_simultaneous_takeovers_of_an_expired_hold_give_one_winner(pg):
    factory, _ = pg
    _, slot_id = _one_slot(factory)
    db = factory()
    db.execute(
        text(
            "UPDATE doctor_slots SET status='held', held_by_session='stale', "
            "held_until = NOW() - INTERVAL '1 minute' WHERE slot_id = :id"
        ),
        {"id": slot_id},
    )
    db.commit()
    db.close()

    def attempt(index):
        db = factory()
        try:
            return hold_slot(db, slot_id, f"racer-{index}").held_by_session
        finally:
            db.close()

    outcomes = _race(CONTENDERS, attempt)
    assert len([o for o in outcomes if o[0] == "ok"]) == 1, outcomes


def test_twenty_patients_race_to_book_one_slot_and_exactly_one_appointment_exists(pg):
    factory, _ = pg
    doctor_id, slot_id = _one_slot(factory)
    session_ids = [f"patient-{uuid.uuid4().hex[:8]}" for _ in range(CONTENDERS)]
    for session_id in session_ids:
        _make_session(factory, session_id)

    def hold_and_book(index):
        db = factory()
        try:
            hold_slot(db, slot_id, session_ids[index])
            return create_appointment(
                db,
                AIAppointmentCreate(
                    session_id=session_ids[index],
                    patient_uhid="UHID-PG",
                    slot_id=slot_id,
                    status="confirmed",
                ),
            ).appointment_id
        finally:
            db.close()

    outcomes = _race(CONTENDERS, hold_and_book)
    assert len([o for o in outcomes if o[0] == "ok"]) == 1, outcomes
    assert all(isinstance(o[1], SlotUnavailableError) for o in outcomes if o[0] == "error")

    db = factory()
    appointments = db.query(AIAppointment).filter(AIAppointment.slot_id == slot_id).all()
    assert len(appointments) == 1
    assert appointments[0].doctor_id == doctor_id
    assert db.get(DoctorSlot, slot_id).status == "booked"
    db.close()


def test_the_database_itself_refuses_a_second_active_appointment_for_a_slot(pg):
    factory, _ = pg
    _, slot_id = _one_slot(factory)
    session_id = f"patient-{uuid.uuid4().hex[:8]}"
    _make_session(factory, session_id)
    db = factory()
    hold_slot(db, slot_id, session_id)
    first = create_appointment(
        db,
        AIAppointmentCreate(session_id=session_id, patient_uhid="UHID-PG", slot_id=slot_id),
    )

    # Bypass the application lock entirely and insert straight into the table.
    row = db.get(DoctorSlot, slot_id)
    db.add(
        AIAppointment(
            appointment_id=str(uuid.uuid4()),
            session_id=session_id,
            patient_uhid="UHID-PG",
            doctor_name="x",
            appointment_datetime=row.slot_start,
            slot_id=slot_id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # Once the first one is cancelled the slot is free for a new appointment.
    cancel_appointment(db, db.get(AIAppointment, first.appointment_id))
    hold_slot(db, slot_id, session_id)
    second = create_appointment(
        db,
        AIAppointmentCreate(session_id=session_id, patient_uhid="UHID-PG", slot_id=slot_id),
    )
    assert second.appointment_id != first.appointment_id
    db.close()


def test_generating_slots_from_many_requests_at_once_creates_each_slot_once(pg):
    factory, _ = pg
    doctor_id = _make_doctor(factory)
    day = _tomorrow()

    def attempt(index):
        db = factory()
        try:
            return len(list_free_slots(db, db.get(Doctor, doctor_id), day))
        finally:
            db.close()

    outcomes = _race(10, attempt)
    assert all(o[0] == "ok" for o in outcomes), outcomes
    assert {o[1] for o in outcomes} == {24}

    db = factory()
    assert db.query(DoctorSlot).filter(DoctorSlot.doctor_id == doctor_id).count() == 24
    db.close()
