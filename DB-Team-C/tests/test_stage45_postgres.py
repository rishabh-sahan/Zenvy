"""Agents and medication against a REAL PostgreSQL database (threads, constraints, migrations 012-013).

Skipped unless TEST_POSTGRES_URL is set. Like test_stage3_postgres.py it DROPs and recreates the schema
and refuses non-local databases.
"""
import os
import uuid
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.agent import AgentEvent, AgentMessage
from app.models.ai_appointment import AIAppointment
from app.models.authentication import Authentication
from app.models.consultation import Consultation
from app.models.prescription import MedicationDose, Prescription
from app.models.reminder import Reminder
from app.services import agent_service, reminder_service
from app.services import prescription_service as rx
from app.services.slot_service import IST, as_utc, utcnow
from test_stage3_postgres import CONTENDERS, factory, make_clinic, race  # noqa: F401  (fixtures)

pytestmark = pytest.mark.skipif(not os.getenv("TEST_POSTGRES_URL"), reason="TEST_POSTGRES_URL is not set")

ITEM = {"drug_name": "Paracetamol", "strength": "500 mg", "dose_text": "1 tablet", "frequency_text": "1-0-1",
        "food": "after", "duration_days": 3}


def signed_prescription(factory, clinic):
    """A consultation with a signed prescription for the clinic's first appointment."""
    db = factory()
    appointment = db.get(AIAppointment, clinic["appointments"][0])
    patient = db.query(Authentication).filter(Authentication.phone_no == appointment.patient_phone_no).one()
    doctor_auth = db.query(Authentication).filter(Authentication.auth_id == db.execute(
        text("SELECT auth_id FROM doctors WHERE doctor_id = :d"), {"d": clinic["doctor_id"]}).scalar()).one()
    consultation = Consultation(
        consultation_id=str(uuid.uuid4()), appointment_id=appointment.appointment_id, doctor_id=clinic["doctor_id"],
        patient_auth_id=patient.auth_id, mode="online", status="created",
    )
    db.add(consultation)
    db.commit()
    rx.save_draft(db, consultation, [ITEM], "doctor_edit", doctor_auth.auth_id)
    prescription = rx.sign(db, consultation, doctor_auth.auth_id)
    result = {"appointment_id": appointment.appointment_id, "prescription_id": prescription.prescription_id,
              "patient_auth_id": patient.auth_id, "doctor_auth_id": doctor_auth.auth_id, "consultation_id": consultation.consultation_id}
    db.close()
    return result


def seven_am():
    return datetime.combine(utcnow().astimezone(IST).date(), time(7, 0), tzinfo=IST)


def test_migrations_012_and_013_created_what_they_should(factory):
    inspector = inspect(factory().get_bind())
    assert {"agent_events", "agent_messages", "agent_actions", "prescriptions", "prescription_items", "medication_doses"} <= set(inspector.get_table_names())
    assert "dose_id" in {c["name"] for c in inspector.get_columns("reminders")}
    assert "idx_agent_events_pending" in {i["name"] for i in inspector.get_indexes("agent_events")}


def test_the_database_itself_rejects_bad_prescription_rows(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    db = factory()
    agent_service.process_all(db, seven_am())        # creates the doses the status check below needs

    def refuses(sql, **values):
        with pytest.raises(IntegrityError):
            db.execute(text(sql), values)
            db.commit()
        db.rollback()

    item = "INSERT INTO prescription_items (item_id, prescription_id, position, drug_name, food, duration_days) VALUES (:i, :p, 9, 'X', :f, :d)"
    refuses(item, i=str(uuid.uuid4()), p=ids["prescription_id"], f="whenever", d=3)       # bad food value
    refuses(item, i=str(uuid.uuid4()), p=ids["prescription_id"], f="any", d=0)            # days out of range
    refuses(item, i=str(uuid.uuid4()), p=ids["prescription_id"], f="any", d=91)
    refuses("UPDATE prescriptions SET status = 'maybe' WHERE prescription_id = :p", p=ids["prescription_id"])
    refuses("UPDATE medication_doses SET status = 'eaten' WHERE prescription_id = :p", p=ids["prescription_id"])
    refuses("INSERT INTO agent_actions (action_id, agent, tool) VALUES (:i, 'coordinator-ish', 'x')", i=str(uuid.uuid4()))
    refuses("INSERT INTO agent_messages (message_id, recipient_type, recipient_auth_id, kind, text) VALUES (:i, 'nobody', :a, 'k', 't')",
            i=str(uuid.uuid4()), a=ids["patient_auth_id"])
    db.close()


def test_a_second_version_with_the_same_number_is_refused(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    db = factory()
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO prescriptions (prescription_id, consultation_id, appointment_id, doctor_id, version, status, source) "
            "VALUES (:i, :c, :a, :d, 1, 'draft', 'doctor_edit')"),
            {"i": str(uuid.uuid4()), "c": ids["consultation_id"], "a": ids["appointment_id"], "d": clinic["doctor_id"]})
        db.commit()
    db.rollback()
    db.close()


def test_twenty_coordinators_handle_one_prescription_event_exactly_once(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    now = seven_am()

    def attempt(index):
        session = factory()
        try:
            return agent_service.process_all(session, now)
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" for kind, _ in outcomes), outcomes

    db = factory()
    event = db.query(AgentEvent).filter(AgentEvent.appointment_id == ids["appointment_id"], AgentEvent.kind == "prescription_signed").one()
    doses = db.query(MedicationDose).filter(MedicationDose.prescription_id == ids["prescription_id"]).all()
    reminders = db.query(Reminder).filter(Reminder.appointment_id == ids["appointment_id"], Reminder.kind == "medication").all()
    told = db.query(AgentMessage).filter(AgentMessage.appointment_id == ids["appointment_id"], AgentMessage.kind == "prescription_signed").all()
    db.close()
    assert event.status == "done" and event.attempts == 1                  # one claim, one handling
    assert len(doses) == 6 and len({d.due_at for d in doses}) == 6         # 3 days x 2 times, none doubled
    assert len(reminders) == 6
    assert sorted(m.recipient_type for m in told) == ["doctor", "patient"]  # each told once


def test_twenty_schedulers_scheduling_the_same_prescription_make_no_duplicate_doses(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    now = seven_am()

    def attempt(index):
        session = factory()
        try:
            prescription = session.get(Prescription, ids["prescription_id"])
            try:
                return rx.schedule_doses(session, prescription, now)
            except IntegrityError:
                session.rollback()
                return 0        # the database refused the duplicate: that is the safety net working
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    db = factory()
    doses = db.query(MedicationDose).filter(MedicationDose.prescription_id == ids["prescription_id"]).all()
    reminders = db.query(Reminder).filter(Reminder.appointment_id == ids["appointment_id"], Reminder.kind == "medication").all()
    db.close()
    assert len(doses) == 6 and len(reminders) == 6


def test_twenty_taps_on_taken_leave_one_taken_dose(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    now = seven_am()
    db = factory()
    agent_service.process_all(db, now)
    first = db.query(MedicationDose).filter(MedicationDose.prescription_id == ids["prescription_id"]).order_by(MedicationDose.due_at).first()
    dose_id, due = first.dose_id, as_utc(first.due_at)
    db.close()

    def attempt(index):
        session = factory()
        try:
            return rx.mark_taken(session, dose_id, ids["patient_auth_id"], now=due + timedelta(minutes=2)).status
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" and value == "taken" for kind, value in outcomes), outcomes
    db = factory()
    assert db.get(MedicationDose, dose_id).status == "taken"
    assert db.query(Reminder).filter(Reminder.dose_id == dose_id).one().status == "cancelled"
    db.close()


def test_twenty_schedulers_send_each_dose_reminder_once(factory):
    clinic = make_clinic(factory)
    ids = signed_prescription(factory, clinic)
    now = seven_am()
    db = factory()
    agent_service.process_all(db, now)
    last_due = max(as_utc(d.due_at) for d in db.query(MedicationDose).filter(MedicationDose.prescription_id == ids["prescription_id"]))
    db.close()

    def attempt(index):
        session = factory()
        try:
            return reminder_service.process_due(session, now=last_due + timedelta(minutes=1), appointment_id=ids["appointment_id"])
        finally:
            session.close()

    outcomes = race(CONTENDERS, attempt)
    assert all(kind == "ok" for kind, _ in outcomes), outcomes
    db = factory()
    rows = db.query(Reminder).filter(Reminder.appointment_id == ids["appointment_id"], Reminder.kind == "medication").all()
    db.close()
    sent = [r for r in rows if r.status == "sent"]
    assert all(r.attempts <= 1 for r in rows)
    assert len(sent) >= 1 and len({r.reminder_id for r in sent}) == len(sent)
    assert all(r.mode == "mock" for r in sent)
