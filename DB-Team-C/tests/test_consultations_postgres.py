"""Consultation tables against a REAL PostgreSQL database (migration 010).

SQLite does not enforce the CHECK constraints, JSONB columns or timezone-aware
timestamps the real schema has, so the important rules are checked here.
Skipped unless TEST_POSTGRES_URL is set; it refuses non-local databases because
it DROPs and recreates the schema. See test_slots_postgres.py for how to run.
"""
import base64
import io
import os
import uuid
import wave
from datetime import timedelta
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.migrations import run_migrations
from app.models.ai_appointment import AIAppointment
from app.models.authentication import Authentication
from app.models.consultation import Consultation, ConsultationNote, ConsultationTurn
from app.models.doctor import Doctor
from app.models.hospital import Hospital
from app.models.session import Session as SessionModel
from app.services import consultation_service as svc
from app.services.slot_service import utcnow

POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "local-postgres"}

pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL is not set")


@pytest.fixture(scope="module")
def factory():
    host = urlparse(POSTGRES_URL.replace("+psycopg", "")).hostname
    assert host in LOCAL_HOSTS, f"Refusing to wipe a non-local database ({host})"
    engine = create_engine(POSTGRES_URL, future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    run_migrations(engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    engine.dispose()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUDIO_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "AUDIO_STORAGE_DIR", str(tmp_path / "audio"))
    return tmp_path / "audio"


def _wav(seconds=1.0):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x01\x02" * int(16000 * seconds))
    return buffer.getvalue()


@pytest.fixture
def case(factory):
    db = factory()
    suffix = uuid.uuid4().hex[:8]
    staff = Authentication(name="Dr", phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}", password_hash="x", role="staff")
    patient = Authentication(name="Pat", phone_no=str(9_000_000_000 + uuid.uuid4().int % 99_999_999), password_hash="!x")
    hospital = Hospital(hospital_id=f"hos-{suffix}", name="H", city="mysore")
    db.add_all([staff, patient, hospital])
    db.flush()
    doctor = Doctor(doctor_id=f"doc-{suffix}", hospital_id=hospital.hospital_id, auth_id=staff.auth_id, name="Dr. X", specialty="Cardiologist")
    session = SessionModel(session_id=f"s-{suffix}", user_id=patient.auth_id, channel="web", language="en")
    db.add_all([doctor, session])
    db.flush()
    appointment = AIAppointment(
        appointment_id=f"a-{suffix}", session_id=session.session_id, patient_phone_no=patient.phone_no,
        patient_uhid="U", doctor_name="Dr. X", doctor_id=doctor.doctor_id,
        appointment_datetime=utcnow() + timedelta(days=1),
    )
    db.add(appointment)
    db.commit()
    yield db, appointment, doctor, staff, patient
    db.close()


def _consultation(case):
    db, appointment, doctor, staff, patient = case
    svc.record_consent(db, appointment, True, "patient", patient.auth_id)
    return svc.create_consultation(db, appointment, "online")


def test_migration_010_created_the_tables(factory):
    names = set(inspect(factory().get_bind()).get_table_names())
    assert {"consultation_consents", "consultations", "consultation_turns", "consultation_notes"} <= names


def test_the_newest_consent_decision_wins_on_postgres(case):
    db, appointment, doctor, staff, patient = case
    for given in (True, False, True, False):
        svc.record_consent(db, appointment, given, "patient", patient.auth_id)
    assert svc.consent_state(svc.current_consent(db, appointment.appointment_id)) == "declined"
    svc.record_consent(db, appointment, True, "patient", patient.auth_id)
    assert svc.consent_state(svc.current_consent(db, appointment.appointment_id)) == "granted"


def test_the_whole_flow_runs_and_note_json_round_trips(case, storage):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)
    svc.store_audio(db, consultation, _wav(2.0))
    assert len(list(storage.iterdir())) == 1

    svc.replace_transcript(db, consultation, [
        {"speaker": "doctor", "text": "What brings you in?", "start_seconds": 0, "end_seconds": 2, "language_code": "en-IN"},
        {"speaker": "patient", "text": "Fever.", "start_seconds": 3, "end_seconds": 4, "language_code": "en-IN"},
    ], "en-IN")

    note = svc.add_note(db, consultation, {
        "chief_complaint": "Fever", "discussion_points": ["Three days", "No cough"], "assessment": "Viral", "plan": "Rest",
    }, "ai", staff.auth_id)
    db.expire_all()
    stored = db.get(ConsultationNote, note.note_id)
    assert stored.discussion_points == ["Three days", "No cough"]  # a real JSON list, not a string

    approved = svc.approve_note(db, consultation, stored, staff.auth_id)
    assert approved.status == "approved" and approved.approved_at.tzinfo is not None


def test_the_database_itself_rejects_bad_values(case):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)

    def insert(sql, **values):
        with pytest.raises(IntegrityError):
            db.execute(text(sql), values)
            db.commit()
        db.rollback()

    insert(
        "INSERT INTO consultation_turns (turn_id, consultation_id, seq, speaker, text) VALUES (:t, :c, 0, 'robot', 'x')",
        t=str(uuid.uuid4()), c=consultation.consultation_id,
    )
    insert(
        "INSERT INTO consultation_consents (consent_id, appointment_id, consent_given, recorded_by, message_version) "
        "VALUES (:i, :a, true, 'stranger', 'v1')",
        i=str(uuid.uuid4()), a=appointment.appointment_id,
    )
    insert(
        "INSERT INTO consultation_notes (note_id, consultation_id, version, status, source) VALUES (:i, :c, 1, 'final', 'ai')",
        i=str(uuid.uuid4()), c=consultation.consultation_id,
    )
    insert(
        "INSERT INTO consultations (consultation_id, appointment_id, doctor_id, mode) VALUES (:i, :a, :d, 'telepathy')",
        i=str(uuid.uuid4()), a=appointment.appointment_id, d=doctor.doctor_id,
    )


def test_one_consultation_per_appointment_and_unique_versions(case):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)

    duplicate = Consultation(consultation_id=str(uuid.uuid4()), appointment_id=appointment.appointment_id, doctor_id=doctor.doctor_id)
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    svc.add_note(db, consultation, {"chief_complaint": "a"}, "ai", staff.auth_id)
    clash = ConsultationNote(note_id=str(uuid.uuid4()), consultation_id=consultation.consultation_id, version=1, source="ai")
    db.add(clash)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_two_turns_cannot_share_a_position(case):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)
    for _ in range(2):
        db.add(ConsultationTurn(turn_id=str(uuid.uuid4()), consultation_id=consultation.consultation_id, seq=0, speaker="doctor", text="x"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_deleting_a_consultation_removes_its_turns_and_notes(case):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)
    cid = consultation.consultation_id
    svc.replace_transcript(db, consultation, [{"speaker": "doctor", "text": "hi"}], "en-IN")
    svc.add_note(db, consultation, {"chief_complaint": "a"}, "ai", staff.auth_id)
    db.execute(text("DELETE FROM consultations WHERE consultation_id = :c"), {"c": cid})
    db.commit()
    assert db.query(ConsultationTurn).filter_by(consultation_id=cid).count() == 0
    assert db.query(ConsultationNote).filter_by(consultation_id=cid).count() == 0


def test_retention_works_with_real_timezone_aware_timestamps(case, storage):
    db, appointment, doctor, staff, patient = case
    consultation = _consultation(case)
    svc.store_audio(db, consultation, _wav(1.0))
    svc.replace_transcript(db, consultation, [{"speaker": "doctor", "text": "hi"}], "en-IN")
    note = svc.add_note(db, consultation, {"chief_complaint": "a"}, "ai", staff.auth_id)

    consultation.audio_uploaded_at = utcnow() - timedelta(days=95)
    db.commit()
    removed = svc.purge_expired(db)
    assert removed["audio"] >= 1 and removed["transcripts"] >= 1
    db.refresh(consultation)
    assert consultation.audio_deleted_at is not None and consultation.turns == []
    assert list(storage.iterdir()) == []
    assert db.get(ConsultationNote, note.note_id) is not None  # the note is kept
