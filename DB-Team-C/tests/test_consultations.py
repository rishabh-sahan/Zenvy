"""Consent, recording, transcript, notes, deletion and retention (SQLite)."""

import base64
import io
import json
import os
import uuid
import wave
from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token
from app.db.deps import get_db
from app.main import app
from app.models.audit_log import AuditLog
from app.models.authentication import Authentication
from app.models.consultation import Consultation, ConsultationNote
from app.models.doctor import Doctor, DoctorSchedule
from app.models.hospital import Hospital
from app.services import consultation_service as svc
from app.services.authentication_service import hash_password
from app.services.crypto_service import DecryptionFailed, decrypt_bytes
from app.services.slot_service import IST, utcnow

client = TestClient(app)


def _db():
    return next(app.dependency_overrides[get_db]())


def _wav(seconds=2.0, rate=16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x02" * int(rate * seconds))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    """Encryption key and a private audio folder for every test."""
    monkeypatch.setattr(settings, "AUDIO_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setattr(settings, "AUDIO_STORAGE_DIR", str(tmp_path / "audio"))
    monkeypatch.setattr(
        "app.api.routes.appointments.send_appointment_notification", lambda *a, **k: "SM-test"
    )
    return tmp_path / "audio"


def _phone() -> str:
    return str(9_000_000_000 + uuid.uuid4().int % 99_999_999)


def _make_doctor(db, label):
    suffix = uuid.uuid4().hex[:8]
    staff = Authentication(
        name=f"Dr {label}", phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}",
        password_hash=hash_password("not-used-here"), role="staff",
    )
    hospital = Hospital(hospital_id=f"hos-{suffix}", name=f"Hospital {suffix}", city="mysore")
    db.add_all([staff, hospital])
    db.flush()
    doctor = Doctor(
        doctor_id=f"doc-{suffix}", hospital_id=hospital.hospital_id, auth_id=staff.auth_id,
        name=f"Dr. {label} {suffix}", specialty="Cardiologist", slot_minutes=30,
    )
    db.add(doctor)
    db.flush()
    for weekday in range(7):
        db.add(DoctorSchedule(
            schedule_id=str(uuid.uuid4()), doctor_id=doctor.doctor_id, weekday=weekday,
            start_time=time(8, 0), end_time=time(20, 0),
        ))
    db.commit()
    token = create_access_token(staff)
    return {"id": doctor.doctor_id, "auth_id": staff.auth_id, "headers": {"Authorization": f"Bearer {token}"}}


def _book(doctor_id, patient_auth_id, slot_index=0):
    day = (utcnow().astimezone(IST) + timedelta(days=1)).date().isoformat()
    slots = client.get(f"/api/v1/doctors/{doctor_id}/slots", params={"date": day}).json()
    slot = slots[slot_index]
    session_id = client.post(
        "/api/v1/sessions", json={"user_id": patient_auth_id, "channel": "web", "language": "en"}
    ).json()["session_id"]
    assert client.post(f"/api/v1/slots/{slot['slot_id']}/hold", json={"session_id": session_id}).status_code == 200
    booked = client.post(
        "/api/v1/appointments",
        json={"session_id": session_id, "patient_uhid": "U", "slot_id": slot["slot_id"], "status": "confirmed"},
    )
    assert booked.status_code == 201, booked.text
    return booked.json()["appointment_id"]


@pytest.fixture
def world():
    db = _db()
    doctor = _make_doctor(db, "Owner")
    other = _make_doctor(db, "Other")
    db.close()
    phone = _phone()
    patient = client.post("/api/v1/auth/phone-login", json={"phone_no": phone}).json()
    appointment_id = _book(doctor["id"], patient["auth_id"])
    return {
        "doctor": doctor, "other": other, "phone": phone,
        "patient_auth_id": patient["auth_id"], "appointment_id": appointment_id,
    }


def _consent(world, given=True, as_patient=True, language="en"):
    if as_patient:
        return client.post(
            f"/api/v1/appointments/{world['appointment_id']}/consent",
            json={"consent_given": given, "auth_id": world["patient_auth_id"], "language": language},
        )
    return client.post(
        f"/api/v1/appointments/{world['appointment_id']}/consent",
        json={"consent_given": given}, headers=world["doctor"]["headers"],
    )


def _start(world, mode="online"):
    assert _consent(world).status_code == 200
    response = client.post(
        "/api/v1/consultations",
        json={"appointment_id": world["appointment_id"], "mode": mode}, headers=world["doctor"]["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()["consultation_id"]


def _upload(world, consultation_id, wav=None, headers=None):
    return client.post(
        f"/api/v1/consultations/{consultation_id}/audio",
        content=wav if wav is not None else _wav(),
        headers=headers or {**world["doctor"]["headers"], "Content-Type": "audio/wav"},
    )


NOTE = {
    "chief_complaint": "Fever for three days",
    "discussion_points": ["Temperature 101F", "No cough"],
    "assessment": "Viral fever",
    "plan": "Paracetamol, review in one week",
}


def _note(world, cid, source="ai", **overrides):
    return client.post(
        f"/api/v1/consultations/{cid}/notes",
        json={**NOTE, **overrides, "source": source}, headers=world["doctor"]["headers"],
    )


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------

def test_the_patient_can_agree_with_their_auth_id(world):
    response = _consent(world, True)
    assert response.status_code == 200
    assert response.json()["state"] == "granted"
    assert response.json()["recorded_by"] == "patient"


def test_nobody_else_can_answer_for_the_patient(world):
    url = f"/api/v1/appointments/{world['appointment_id']}/consent"
    assert client.post(url, json={"consent_given": True}).status_code == 403
    assert client.post(url, json={"consent_given": True, "auth_id": "someone-else"}).status_code == 403
    stranger = client.post("/api/v1/auth/phone-login", json={"phone_no": _phone()}).json()["auth_id"]
    assert client.post(url, json={"consent_given": True, "auth_id": stranger}).status_code == 403
    # ...and another doctor cannot either.
    assert client.post(url, json={"consent_given": True}, headers=world["other"]["headers"]).status_code == 403


def test_the_treating_doctor_can_record_verbal_consent(world):
    response = _consent(world, True, as_patient=False)
    assert response.status_code == 200
    assert response.json()["recorded_by"] == "doctor_on_behalf"


def test_a_doctor_cannot_overrule_the_patients_own_refusal(world):
    assert _consent(world, False, as_patient=True).json()["state"] == "declined"
    blocked = _consent(world, True, as_patient=False)
    assert blocked.status_code == 409 and blocked.json()["detail"] == "patient_declined"
    # The patient can still change their own mind...
    assert _consent(world, True, as_patient=True).json()["state"] == "granted"


def test_a_doctor_can_record_their_own_refusal_and_then_a_verbal_yes(world):
    assert _consent(world, False, as_patient=False).json()["state"] == "declined"
    assert _consent(world, True, as_patient=False).json()["state"] == "granted"


def test_the_newest_decision_wins(world):
    _consent(world, True)
    assert _consent(world, False).json()["state"] == "declined"
    assert _consent(world, True).json()["state"] == "granted"


def test_consent_is_not_possible_on_a_cancelled_appointment(world):
    patient_session = client.get(f"/api/v1/appointments/session/x").json()
    db = _db()
    from app.models.ai_appointment import AIAppointment, AppointmentStatus
    db.query(AIAppointment).filter(AIAppointment.appointment_id == world["appointment_id"]).update(
        {"status": AppointmentStatus.cancelled}
    )
    db.commit()
    db.close()
    assert _consent(world).status_code == 409


def test_the_consent_message_comes_in_three_languages_and_falls_back_to_english():
    english = client.get("/api/v1/consent-message", params={"language": "en"}).json()
    assert english["version"] and "record" in english["message"].lower()
    hindi = client.get("/api/v1/consent-message", params={"language": "hi"}).json()
    assert any("ऀ" <= ch <= "ॿ" for ch in hindi["message"])
    kannada = client.get("/api/v1/consent-message", params={"language": "kn"}).json()
    assert any("ಀ" <= ch <= "೿" for ch in kannada["message"])
    assert client.get("/api/v1/consent-message", params={"language": "zz"}).json()["language"] == "en"


def test_only_the_patient_or_their_doctor_can_read_the_consent(world):
    url = f"/api/v1/appointments/{world['appointment_id']}/consent"
    _consent(world, True)
    assert client.get(url, params={"auth_id": world["patient_auth_id"]}).json()["state"] == "granted"
    assert client.get(url, headers=world["doctor"]["headers"]).json()["state"] == "granted"
    assert client.get(url).status_code == 403
    assert client.get(url, headers=world["other"]["headers"]).status_code == 403


# ---------------------------------------------------------------------------
# starting a consultation
# ---------------------------------------------------------------------------

def test_recording_cannot_start_without_consent(world):
    response = client.post(
        "/api/v1/consultations", json={"appointment_id": world["appointment_id"]}, headers=world["doctor"]["headers"]
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "consent_required"


def test_a_refusal_blocks_recording(world):
    _consent(world, False)
    response = client.post(
        "/api/v1/consultations", json={"appointment_id": world["appointment_id"]}, headers=world["doctor"]["headers"]
    )
    assert response.status_code == 409


def test_starting_a_consultation_is_repeatable(world):
    first = _start(world)
    again = client.post(
        "/api/v1/consultations", json={"appointment_id": world["appointment_id"]}, headers=world["doctor"]["headers"]
    )
    assert again.json()["consultation_id"] == first
    assert again.json()["consent_state"] == "granted"


def test_only_the_treating_doctor_can_use_the_consultation(world):
    cid = _start(world)
    for headers in (world["other"]["headers"],):
        assert client.get(f"/api/v1/consultations/{cid}", headers=headers).status_code == 403
        assert _upload(world, cid, headers={**headers, "Content-Type": "audio/wav"}).status_code == 403
        assert client.post(
            "/api/v1/consultations", json={"appointment_id": world["appointment_id"]}, headers=headers
        ).status_code == 403
    assert client.get(f"/api/v1/consultations/{cid}").status_code == 401
    # A patient token-less call and a non-doctor staff member are refused too.
    db = _db()
    desk = Authentication(name="Desk", phone_no=f"+9100{uuid.uuid4().int % 10**8:08d}",
                          password_hash=hash_password("x" * 8), role="staff")
    db.add(desk)
    db.commit()
    token = create_access_token(desk)
    db.close()
    assert client.get(
        f"/api/v1/consultations/{cid}", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 403


# ---------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------

def test_audio_is_stored_encrypted_and_decrypts_back(world, storage):
    cid = _start(world)
    wav = _wav(3.0)
    response = _upload(world, cid, wav)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "audio_uploaded" and body["has_recording"] is True
    assert body["audio_seconds"] == pytest.approx(3.0, abs=0.01)

    files = list(storage.iterdir())
    assert len(files) == 1
    on_disk = files[0].read_bytes()
    assert on_disk.startswith(b"ZNV1")
    assert b"RIFF" not in on_disk and wav[100:200] not in on_disk

    db = _db()
    consultation = db.get(Consultation, cid)
    assert svc.load_audio(consultation) == wav
    db.close()


def test_a_recording_copied_under_another_name_does_not_decrypt(world, storage):
    cid = _start(world)
    _upload(world, cid)
    blob = next(storage.iterdir()).read_bytes()
    with pytest.raises(DecryptionFailed):
        decrypt_bytes(blob, "some-other-consultation")
    with pytest.raises(DecryptionFailed):
        decrypt_bytes(blob[:-1] + bytes([blob[-1] ^ 1]), cid)


def test_bad_uploads_are_refused(world, storage):
    cid = _start(world)
    assert _upload(world, cid, b"").status_code == 422
    assert _upload(world, cid, b"this is not a wav file").status_code == 422
    assert list(storage.glob("*")) == []
    assert client.get(f"/api/v1/consultations/{cid}", headers=world["doctor"]["headers"]).json()["status"] == "created"


def test_a_second_recording_needs_the_first_to_be_deleted(world):
    cid = _start(world)
    assert _upload(world, cid).status_code == 200
    again = _upload(world, cid)
    assert again.status_code == 409 and again.json()["detail"] == "recording_exists"


def test_a_failed_recording_can_be_replaced_without_deleting_it_first(world, storage):
    cid = _start(world)
    _upload(world, cid, _wav(1.0))
    client.patch(f"/api/v1/consultations/{cid}/status",
                 json={"status": "failed", "failure_reason": "speech_to_text_failed"}, headers=world["doctor"]["headers"])
    retry = _upload(world, cid, _wav(2.0))
    assert retry.status_code == 200
    assert retry.json()["status"] == "audio_uploaded" and retry.json()["failure_reason"] is None
    assert len(list(storage.iterdir())) == 1  # replaced, not duplicated


def test_without_an_encryption_key_nothing_is_stored(world, storage, monkeypatch):
    monkeypatch.setattr(settings, "AUDIO_ENCRYPTION_KEY", None)
    cid = _start(world)
    response = _upload(world, cid)
    assert response.status_code == 503
    assert "AUDIO_ENCRYPTION_KEY" in response.json()["detail"]
    assert not storage.exists() or list(storage.iterdir()) == []


def test_a_wrong_sized_key_is_rejected(world, monkeypatch):
    monkeypatch.setattr(settings, "AUDIO_ENCRYPTION_KEY", base64.b64encode(b"too-short").decode())
    cid = _start(world)
    assert _upload(world, cid).status_code == 503


def test_audio_is_refused_once_consent_is_withdrawn(world):
    cid = _start(world)
    _consent(world, False)
    assert _upload(world, cid).status_code == 409


def test_oversized_uploads_are_refused(world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_AUDIO_BYTES", 1000)
    cid = _start(world)
    assert _upload(world, cid, _wav(1.0)).status_code in (413, 422)


# ---------------------------------------------------------------------------
# transcript
# ---------------------------------------------------------------------------

TURNS = [
    {"speaker": "doctor", "text": "What brings you in today?", "start_seconds": 0.0, "end_seconds": 2.0, "language_code": "en-IN"},
    {"speaker": "patient", "text": "I have had a fever for three days.", "start_seconds": 3.0, "end_seconds": 6.0, "language_code": "en-IN"},
]


def _put_transcript(world, cid, turns=None):
    return client.put(
        f"/api/v1/consultations/{cid}/transcript",
        json={"turns": turns or TURNS, "language_code": "en-IN"}, headers=world["doctor"]["headers"],
    )


def test_the_transcript_is_stored_as_labelled_turns_in_order(world):
    cid = _start(world)
    body = _put_transcript(world, cid).json()
    assert body["status"] == "transcribed"
    assert [t["speaker"] for t in body["turns"]] == ["doctor", "patient"]
    assert [t["seq"] for t in body["turns"]] == [0, 1]
    assert body["language_code"] == "en-IN"


def test_a_new_transcript_replaces_the_old_one(world):
    cid = _start(world)
    _put_transcript(world, cid)
    body = _put_transcript(world, cid, [TURNS[1]]).json()
    assert len(body["turns"]) == 1


def test_the_doctor_can_correct_a_speaker_or_a_word(world):
    cid = _start(world)
    turn = _put_transcript(world, cid).json()["turns"][1]
    fixed = client.patch(
        f"/api/v1/consultations/{cid}/turns/{turn['turn_id']}",
        json={"speaker": "doctor", "text": "I have had a fever for four days."}, headers=world["doctor"]["headers"],
    )
    assert fixed.status_code == 200
    assert fixed.json()["speaker"] == "doctor" and fixed.json()["edited"] is True
    assert client.patch(
        f"/api/v1/consultations/{cid}/turns/{turn['turn_id']}", json={"speaker": "robot"}, headers=world["doctor"]["headers"]
    ).status_code == 422
    assert client.patch(
        f"/api/v1/consultations/{cid}/turns/{turn['turn_id']}", json={"speaker": "patient"}, headers=world["other"]["headers"]
    ).status_code == 403
    assert client.patch(
        f"/api/v1/consultations/{cid}/turns/missing", json={"speaker": "patient"}, headers=world["doctor"]["headers"]
    ).status_code == 404


def test_a_transcript_cannot_be_stored_after_consent_is_withdrawn(world):
    cid = _start(world)
    _consent(world, False)
    assert _put_transcript(world, cid).status_code == 409


def test_an_empty_transcript_is_rejected(world):
    cid = _start(world)
    assert client.put(
        f"/api/v1/consultations/{cid}/transcript", json={"turns": []}, headers=world["doctor"]["headers"]
    ).status_code == 422


def test_the_pipeline_can_report_progress_and_failure(world):
    cid = _start(world)
    headers = world["doctor"]["headers"]
    for status_name in ("transcribing", "summarising"):
        assert client.patch(f"/api/v1/consultations/{cid}/status", json={"status": status_name}, headers=headers).json()["status"] == status_name
    failed = client.patch(
        f"/api/v1/consultations/{cid}/status", json={"status": "failed", "failure_reason": "no_speech"}, headers=headers
    ).json()
    assert failed["status"] == "failed" and failed["failure_reason"] == "no_speech"
    # The pipeline cannot claim a note exists or that it was approved.
    assert client.patch(f"/api/v1/consultations/{cid}/status", json={"status": "note_approved"}, headers=headers).status_code == 422
    assert client.patch(f"/api/v1/consultations/{cid}/status", json={"status": "draft_ready"}, headers=headers).status_code == 422


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

def test_an_ai_draft_becomes_version_one_and_edits_become_new_versions(world):
    cid = _start(world)
    first = _note(world, cid, "ai")
    assert first.status_code == 201
    assert (first.json()["version"], first.json()["status"], first.json()["source"]) == (1, "draft", "ai")

    edited = _note(world, cid, "doctor_edit", plan="Paracetamol, review in two weeks")
    assert edited.json()["version"] == 2

    detail = client.get(f"/api/v1/consultations/{cid}", headers=world["doctor"]["headers"]).json()
    assert detail["status"] == "draft_ready"
    assert [n["version"] for n in detail["notes"]] == [1, 2]
    # The AI draft was not overwritten.
    assert detail["notes"][0]["plan"] == "Paracetamol, review in one week"
    assert detail["notes"][1]["plan"] == "Paracetamol, review in two weeks"


def test_approving_locks_the_note(world):
    cid = _start(world)
    note = _note(world, cid).json()
    approved = client.post(f"/api/v1/consultations/{cid}/notes/{note['note_id']}/approve", headers=world["doctor"]["headers"])
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved" and approved.json()["approved_at"]
    assert client.get(f"/api/v1/consultations/{cid}", headers=world["doctor"]["headers"]).json()["status"] == "note_approved"

    again = client.post(f"/api/v1/consultations/{cid}/notes/{note['note_id']}/approve", headers=world["doctor"]["headers"])
    assert again.status_code == 409 and again.json()["detail"] == "already_approved"


def test_only_the_newest_version_can_be_approved(world):
    cid = _start(world)
    old = _note(world, cid, "ai").json()
    _note(world, cid, "doctor_edit", assessment="Changed")
    stale = client.post(f"/api/v1/consultations/{cid}/notes/{old['note_id']}/approve", headers=world["doctor"]["headers"])
    assert stale.status_code == 409 and stale.json()["detail"] == "not_latest_version"


def test_editing_after_approval_makes_a_new_version_and_keeps_the_signed_one(world):
    cid = _start(world)
    v1 = _note(world, cid).json()
    client.post(f"/api/v1/consultations/{cid}/notes/{v1['note_id']}/approve", headers=world["doctor"]["headers"])
    v2 = _note(world, cid, "doctor_edit", plan="A different plan").json()
    assert v2["version"] == 2 and v2["status"] == "draft"

    detail = client.get(f"/api/v1/consultations/{cid}", headers=world["doctor"]["headers"]).json()
    assert detail["notes"][0]["status"] == "approved"
    assert detail["notes"][0]["plan"] == NOTE["plan"]  # signed text unchanged
    assert detail["status"] == "note_approved"


def test_approval_records_what_the_ai_drafted_against_what_the_doctor_signed(world):
    cid = _start(world)
    _note(world, cid, "ai")
    edited = _note(world, cid, "doctor_edit", plan="Review in two weeks", assessment="Viral fever, mild").json()
    client.post(f"/api/v1/consultations/{cid}/notes/{edited['note_id']}/approve", headers=world["doctor"]["headers"])

    db = _db()
    entry = db.query(AuditLog).filter(AuditLog.action == "consultation_note_approved", AuditLog.user_id == world["patient_auth_id"]).one()
    db.close()
    assert entry.actor == f"doctor:{world['doctor']['auth_id']}"
    assert entry.relevant_metadata["changed_fields"] == ["assessment", "plan"]
    assert entry.before_value["plan"] == NOTE["plan"]
    assert entry.after_value["plan"] == "Review in two weeks"


def test_note_validation(world):
    cid = _start(world)
    headers = world["doctor"]["headers"]
    assert client.post(f"/api/v1/consultations/{cid}/notes", json={}, headers=headers).status_code == 422
    assert _note(world, cid, plan="x" * 4001).status_code == 422
    assert _note(world, cid, discussion_points=["p"] * 31).status_code == 422
    assert _note(world, cid, source="made_up").status_code == 422
    assert client.post(f"/api/v1/consultations/{cid}/notes/missing/approve", headers=headers).status_code == 404
    assert client.post(f"/api/v1/consultations/{cid}/notes", json=NOTE, headers=world["other"]["headers"]).status_code == 403


# ---------------------------------------------------------------------------
# deleting and retention
# ---------------------------------------------------------------------------

def _recorded_consultation(world):
    cid = _start(world)
    _upload(world, cid)
    _put_transcript(world, cid)
    note = _note(world, cid).json()
    return cid, note


def test_deleting_a_recording_keeps_the_notes(world, storage):
    cid, note = _recorded_consultation(world)
    client.post(f"/api/v1/consultations/{cid}/notes/{note['note_id']}/approve", headers=world["doctor"]["headers"])

    response = client.delete(f"/api/v1/consultations/{cid}/recording", headers=world["doctor"]["headers"])
    assert response.status_code == 200
    assert response.json()["has_recording"] is False
    assert list(storage.iterdir()) == []

    detail = client.get(f"/api/v1/consultations/{cid}", headers=world["doctor"]["headers"]).json()
    assert detail["turns"] == []
    assert len(detail["notes"]) == 1 and detail["notes"][0]["status"] == "approved"
    assert detail["status"] == "note_approved"

    db = _db()
    actions = {a.action for a in db.query(AuditLog).filter(AuditLog.user_id == world["patient_auth_id"]).all()}
    db.close()
    assert {"consultation_audio_deleted", "consultation_transcript_deleted"} <= actions


def test_an_unsigned_consultation_is_marked_deleted_and_can_be_recorded_again(world, storage):
    cid, _ = _recorded_consultation(world)
    assert client.delete(f"/api/v1/consultations/{cid}/recording", headers=world["doctor"]["headers"]).json()["status"] == "recording_deleted"
    assert _put_transcript(world, cid).status_code == 409
    assert _upload(world, cid).status_code == 200  # a fresh recording is allowed


def test_only_the_doctor_can_delete(world):
    cid, _ = _recorded_consultation(world)
    assert client.delete(f"/api/v1/consultations/{cid}/recording", headers=world["other"]["headers"]).status_code == 403
    assert client.delete(f"/api/v1/consultations/{cid}/recording").status_code == 401


def test_retention_removes_audio_at_30_days_and_transcripts_at_90_but_never_notes(world, storage):
    cid, note = _recorded_consultation(world)
    db = _db()
    consultation = db.get(Consultation, cid)

    # Fresh: nothing to purge.
    assert svc.purge_expired(db) == {"audio": 0, "transcripts": 0}

    consultation.audio_uploaded_at = utcnow() - timedelta(days=31)
    db.commit()
    assert svc.purge_expired(db) == {"audio": 1, "transcripts": 0}
    assert list(storage.iterdir()) == []
    db.refresh(consultation)
    assert consultation.audio_deleted_at is not None and len(consultation.turns) == 2

    consultation.audio_uploaded_at = utcnow() - timedelta(days=91)
    db.commit()
    assert svc.purge_expired(db) == {"audio": 0, "transcripts": 1}
    db.refresh(consultation)
    assert consultation.turns == []
    assert db.query(ConsultationNote).filter(ConsultationNote.consultation_id == cid).count() == 1

    # Running it again changes nothing.
    assert svc.purge_expired(db) == {"audio": 0, "transcripts": 0}
    db.close()


# ---------------------------------------------------------------------------
# lists
# ---------------------------------------------------------------------------

def test_the_doctor_sees_their_own_appointments_with_consent_and_progress(world):
    cid, _ = _recorded_consultation(world)
    listing = client.get("/api/v1/doctor/appointments", headers=world["doctor"]["headers"]).json()
    mine = next(a for a in listing if a["appointment_id"] == world["appointment_id"])
    assert mine["consent_state"] == "granted" and mine["consent_recorded_by"] == "patient"
    assert mine["consultation_id"] == cid and mine["consultation_status"] == "draft_ready"
    assert mine["note_status"] == "draft"
    # The patient is shown masked, never as a full phone number.
    assert mine["patient_label"] == f"Patient ••••{world['phone'][-4:]}"
    assert world["phone"] not in json.dumps(listing)
    # Another doctor does not see it.
    others = client.get("/api/v1/doctor/appointments", headers=world["other"]["headers"]).json()
    assert world["appointment_id"] not in [a["appointment_id"] for a in others]
    assert client.get("/api/v1/doctor/appointments").status_code == 401


def test_cancelled_appointments_are_not_listed(world):
    session_id = client.get(f"/api/v1/appointments/session/none").json()
    db = _db()
    from app.models.ai_appointment import AIAppointment, AppointmentStatus
    appointment = db.query(AIAppointment).filter(AIAppointment.appointment_id == world["appointment_id"]).one()
    appointment.status = AppointmentStatus.cancelled
    db.commit()
    db.close()
    listing = client.get("/api/v1/doctor/appointments", headers=world["doctor"]["headers"]).json()
    assert world["appointment_id"] not in [a["appointment_id"] for a in listing]


def test_the_patient_sees_their_appointments_and_consent_state(world):
    url = f"/api/v1/patients/{world['patient_auth_id']}/appointments"
    before = client.get(url).json()
    mine = next(a for a in before if a["appointment_id"] == world["appointment_id"])
    assert mine["consent_state"] == "none" and mine["hospital_name"]
    _consent(world, True)
    after = next(a for a in client.get(url).json() if a["appointment_id"] == world["appointment_id"])
    assert after["consent_state"] == "granted"
    assert client.get("/api/v1/patients/no-such-patient/appointments").status_code == 404


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------

def test_no_phone_number_appears_in_the_audit_trail(world):
    cid, note = _recorded_consultation(world)
    client.post(f"/api/v1/consultations/{cid}/notes/{note['note_id']}/approve", headers=world["doctor"]["headers"])
    client.delete(f"/api/v1/consultations/{cid}/recording", headers=world["doctor"]["headers"])
    db = _db()
    rows = db.query(AuditLog).filter(AuditLog.user_id == world["patient_auth_id"]).all()
    db.close()
    assert len(rows) >= 6
    dump = json.dumps([[r.actor, r.relevant_metadata, r.before_value, r.after_value] for r in rows], default=str)
    assert world["phone"] not in dump
