"""Consent, recording, transcript and note rules for consultations.

Who may do what is decided by the routes; this module holds the rules about the
data itself:

* recording is only possible once consent is on record (newest decision wins)
* audio is encrypted before it touches the disk
* notes are versioned: a row is never edited, a change is a new version, and an
  approved version can never be changed
* deleting a recording removes the audio and the transcript but keeps the notes
"""

import hashlib
import io
import os
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_appointment import AIAppointment
from app.models.authentication import Authentication
from app.models.consultation import (
    Consultation,
    ConsultationConsent,
    ConsultationNote,
    ConsultationStatus,
    ConsultationTurn,
    NoteStatus,
)
from app.services.audit_service import write_audit_log
from app.services.crypto_service import decrypt_bytes, encrypt_bytes

CONSENT_MESSAGE_VERSION = "v1"

# Shown to the patient. The Hindi and Kannada are first drafts and need a
# native-speaker review before real patients see them.
CONSENT_MESSAGES = {
    "en": (
        "To help your doctor, Zenvy would like to record your consultation and turn it into "
        "written notes. The recording is stored securely, only your doctor can see the notes, "
        "and you can say no at any time. Saying no will not affect your care."
    ),
    "hi": (
        "आपके डॉक्टर की मदद के लिए, Zenvy आपकी परामर्श को रिकॉर्ड करके लिखित नोट्स में बदलना चाहता है। "
        "रिकॉर्डिंग सुरक्षित रखी जाती है, केवल आपके डॉक्टर ही नोट्स देख सकते हैं, और आप किसी भी समय मना कर सकते हैं। "
        "मना करने से आपके इलाज पर कोई असर नहीं पड़ेगा।"
    ),
    "kn": (
        "ನಿಮ್ಮ ವೈದ್ಯರಿಗೆ ಸಹಾಯ ಮಾಡಲು, Zenvy ನಿಮ್ಮ ಸಮಾಲೋಚನೆಯನ್ನು ರೆಕಾರ್ಡ್ ಮಾಡಿ ಲಿಖಿತ ಟಿಪ್ಪಣಿಗಳಾಗಿ ಪರಿವರ್ತಿಸಲು ಬಯಸುತ್ತದೆ. "
        "ರೆಕಾರ್ಡಿಂಗ್ ಅನ್ನು ಸುರಕ್ಷಿತವಾಗಿ ಇಡಲಾಗುತ್ತದೆ, ನಿಮ್ಮ ವೈದ್ಯರು ಮಾತ್ರ ಟಿಪ್ಪಣಿಗಳನ್ನು ನೋಡಬಹುದು, ಮತ್ತು ನೀವು ಯಾವಾಗ ಬೇಕಾದರೂ ಬೇಡ ಎನ್ನಬಹುದು. "
        "ಬೇಡ ಎಂದರೆ ನಿಮ್ಮ ಚಿಕಿತ್ಸೆಗೆ ಯಾವುದೇ ಪರಿಣಾಮ ಬೀರುವುದಿಲ್ಲ."
    ),
}

# The note fields a doctor can edit. Anything else is not part of a version.
NOTE_FIELDS = ("chief_complaint", "discussion_points", "assessment", "plan")

MAX_NOTE_TEXT = 4000
MAX_DISCUSSION_POINTS = 30
MAX_DISCUSSION_POINT_LENGTH = 1000

PIPELINE_STATUSES = {
    ConsultationStatus.transcribing.value,
    ConsultationStatus.transcribed.value,
    ConsultationStatus.summarising.value,
    ConsultationStatus.failed.value,
}


class ConsentRequired(Exception):
    """Recording needs the patient's consent and there is none (or it was refused)."""


class PatientDeclined(Exception):
    """The patient refused recording themselves; a doctor cannot overrule that."""


class InvalidAudio(Exception):
    """The upload is not a readable WAV file."""


class RecordingAlreadyExists(Exception):
    """A recording is already stored; delete it before uploading another."""


class NoteLocked(Exception):
    """The note version is approved, or not the latest, so it cannot be approved/changed."""


class InvalidNote(Exception):
    """A note field is missing, too long or the wrong shape."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------

def patient_for_appointment(db: Session, appointment: AIAppointment) -> Authentication | None:
    """The registered patient an appointment belongs to (matched by phone)."""
    if not appointment.patient_phone_no:
        return None
    return (
        db.query(Authentication)
        .filter(Authentication.phone_no == appointment.patient_phone_no)
        .first()
    )


def current_consent(db: Session, appointment_id: str) -> ConsultationConsent | None:
    return (
        db.query(ConsultationConsent)
        .filter(ConsultationConsent.appointment_id == appointment_id)
        .order_by(ConsultationConsent.created_at.desc(), ConsultationConsent.consent_id.desc())
        .first()
    )


def consent_state(consent: ConsultationConsent | None) -> str:
    if consent is None:
        return "none"
    return "granted" if consent.consent_given else "declined"


def record_consent(
    db: Session,
    appointment: AIAppointment,
    consent_given: bool,
    recorded_by: str,
    recorded_by_auth_id: str | None,
    language: str = "en",
) -> ConsultationConsent:
    if recorded_by == "doctor_on_behalf" and consent_given:
        latest = current_consent(db, appointment.appointment_id)
        if latest is not None and not latest.consent_given and latest.recorded_by == "patient":
            raise PatientDeclined(appointment.appointment_id)
    patient = patient_for_appointment(db, appointment)
    consent = ConsultationConsent(
        consent_id=str(uuid.uuid4()),
        appointment_id=appointment.appointment_id,
        patient_auth_id=patient.auth_id if patient else None,
        consent_given=consent_given,
        recorded_by=recorded_by,
        recorded_by_auth_id=recorded_by_auth_id,
        message_version=CONSENT_MESSAGE_VERSION,
        language=language if language in CONSENT_MESSAGES else "en",
        # Stamped here, to the microsecond, because "the newest decision wins":
        # the database's own now() can be identical for two quick decisions
        # (SQLite only has one-second resolution), which made "newest" a guess.
        created_at=utcnow(),
    )
    db.add(consent)
    db.commit()
    db.refresh(consent)
    if not consent_given:
        _delete_on_withdrawal(db, appointment, recorded_by, recorded_by_auth_id)
    write_audit_log(
        db,
        action="consultation_consent_recorded",
        actor=f"{recorded_by}:{recorded_by_auth_id}" if recorded_by_auth_id else recorded_by,
        session_id=appointment.session_id,
        user_id=consent.patient_auth_id,
        after_value={
            "appointment_id": appointment.appointment_id,
            "consent_given": consent_given,
            "recorded_by": recorded_by,
            "message_version": CONSENT_MESSAGE_VERSION,
        },
    )
    return consent


def _delete_on_withdrawal(db: Session, appointment: AIAppointment, who: str, who_id: str | None) -> None:
    """Consent was withdrawn: delete any recording and transcript right away.

    The notes (draft or approved) are kept. Nothing happens if nothing was recorded.
    """
    consultation = get_consultation_for_appointment(db, appointment.appointment_id)
    if consultation is None:
        return
    has_recording = bool(consultation.audio_path and consultation.audio_deleted_at is None)
    if not has_recording and not consultation.turns:
        return
    delete_recording(
        db, consultation, actor=f"{who}:{who_id}" if who_id else who, reason="consent_withdrawn"
    )


def carry_over_consent(db: Session, old: AIAppointment, new: AIAppointment) -> ConsultationConsent | None:
    """A rescheduled visit is the same visit: the patient's decision comes with it."""
    latest = current_consent(db, old.appointment_id)
    if latest is None:
        return None
    copy = ConsultationConsent(
        consent_id=str(uuid.uuid4()),
        appointment_id=new.appointment_id,
        patient_auth_id=latest.patient_auth_id,
        consent_given=latest.consent_given,
        recorded_by=latest.recorded_by,
        recorded_by_auth_id=latest.recorded_by_auth_id,
        message_version=latest.message_version,
        language=latest.language,
        created_at=utcnow(),
    )
    db.add(copy)
    db.commit()
    write_audit_log(
        db,
        action="consultation_consent_carried_over",
        actor="system",
        session_id=new.session_id,
        user_id=latest.patient_auth_id,
        after_value={
            "from_appointment_id": old.appointment_id,
            "to_appointment_id": new.appointment_id,
            "consent_given": latest.consent_given,
        },
    )
    return copy


def require_consent(db: Session, appointment_id: str) -> ConsultationConsent:
    consent = current_consent(db, appointment_id)
    if consent is None or not consent.consent_given:
        raise ConsentRequired(appointment_id)
    return consent


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------

def get_consultation_for_appointment(db: Session, appointment_id: str) -> Consultation | None:
    return db.query(Consultation).filter(Consultation.appointment_id == appointment_id).first()


def create_consultation(db: Session, appointment: AIAppointment, mode: str) -> Consultation:
    """Create (or return the existing) consultation. Needs consent on record."""
    existing = get_consultation_for_appointment(db, appointment.appointment_id)
    if existing is not None:
        return existing
    require_consent(db, appointment.appointment_id)
    patient = patient_for_appointment(db, appointment)
    consultation = Consultation(
        consultation_id=str(uuid.uuid4()),
        appointment_id=appointment.appointment_id,
        doctor_id=appointment.doctor_id,
        patient_auth_id=patient.auth_id if patient else None,
        mode=mode,
        status=ConsultationStatus.created.value,
    )
    db.add(consultation)
    db.commit()
    db.refresh(consultation)
    write_audit_log(
        db,
        action="consultation_created",
        actor=f"doctor:{appointment.doctor_id}",
        session_id=appointment.session_id,
        user_id=consultation.patient_auth_id,
        after_value={"consultation_id": consultation.consultation_id, "mode": mode},
    )
    return consultation


def set_status(
    db: Session, consultation: Consultation, status: str, failure_reason: str | None = None
) -> Consultation:
    consultation.status = status
    consultation.failure_reason = failure_reason if status == ConsultationStatus.failed.value else None
    db.commit()
    db.refresh(consultation)
    return consultation


# ---------------------------------------------------------------------------
# Audio (encrypted at rest)
# ---------------------------------------------------------------------------

def _audio_file(consultation_id: str) -> Path:
    return Path(settings.AUDIO_STORAGE_DIR) / f"{consultation_id}.wav.enc"


def wav_duration_seconds(wav_bytes: bytes) -> float:
    """Seconds of audio in a WAV file; raises InvalidAudio if it is not one."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            frames, rate = handle.getnframes(), handle.getframerate()
    except (wave.Error, EOFError) as exc:
        raise InvalidAudio("The recording is not a readable WAV file.") from exc
    if rate <= 0:
        raise InvalidAudio("The recording has no sample rate.")
    return frames / rate


def store_audio(db: Session, consultation: Consultation, wav_bytes: bytes) -> Consultation:
    """Encrypt and store the recording. Consent is re-checked at this moment."""
    require_consent(db, consultation.appointment_id)
    # A recording that failed to process may be replaced; a good one must be
    # deleted first so nothing is overwritten by accident.
    if (
        consultation.audio_path
        and consultation.audio_deleted_at is None
        and consultation.status != ConsultationStatus.failed.value
    ):
        raise RecordingAlreadyExists(consultation.consultation_id)
    if not wav_bytes:
        raise InvalidAudio("The recording is empty.")
    if len(wav_bytes) > settings.MAX_AUDIO_BYTES:
        raise InvalidAudio("The recording is too large.")
    seconds = wav_duration_seconds(wav_bytes)

    encrypted = encrypt_bytes(wav_bytes, consultation.consultation_id)
    target = _audio_file(consultation.consultation_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, target)

    consultation.audio_path = target.name
    consultation.audio_bytes = len(wav_bytes)
    consultation.audio_seconds = round(seconds, 2)
    consultation.audio_sha256 = hashlib.sha256(wav_bytes).hexdigest()
    consultation.audio_uploaded_at = utcnow()
    consultation.audio_deleted_at = None
    consultation.status = ConsultationStatus.audio_uploaded.value
    consultation.failure_reason = None
    db.commit()
    db.refresh(consultation)
    write_audit_log(
        db,
        action="consultation_audio_stored",
        actor=f"doctor:{consultation.doctor_id}",
        user_id=consultation.patient_auth_id,
        after_value={
            "consultation_id": consultation.consultation_id,
            "seconds": consultation.audio_seconds,
            "bytes": consultation.audio_bytes,
            "sha256": consultation.audio_sha256,
        },
    )
    return consultation


def load_audio(consultation: Consultation) -> bytes:
    """Decrypted recording (used by tests and tooling; there is no download route)."""
    if not consultation.audio_path or consultation.audio_deleted_at is not None:
        raise FileNotFoundError(consultation.consultation_id)
    return decrypt_bytes(_audio_file(consultation.consultation_id).read_bytes(), consultation.consultation_id)


# ---------------------------------------------------------------------------
# Transcript
# ---------------------------------------------------------------------------

def replace_transcript(
    db: Session, consultation: Consultation, turns: list[dict], language_code: str | None
) -> Consultation:
    """Store the labelled transcript, replacing any earlier one."""
    for old in list(consultation.turns):
        db.delete(old)
    db.flush()
    for seq, turn in enumerate(turns):
        db.add(
            ConsultationTurn(
                turn_id=str(uuid.uuid4()),
                consultation_id=consultation.consultation_id,
                seq=seq,
                speaker=turn.get("speaker") or "unknown",
                text=turn["text"],
                start_seconds=turn.get("start_seconds"),
                end_seconds=turn.get("end_seconds"),
                language_code=turn.get("language_code"),
            )
        )
    consultation.language_code = language_code
    consultation.transcript_deleted_at = None
    consultation.status = ConsultationStatus.transcribed.value
    consultation.failure_reason = None
    db.commit()
    db.refresh(consultation)
    return consultation


def update_turn(db: Session, turn: ConsultationTurn, speaker: str | None, text: str | None) -> ConsultationTurn:
    if speaker is not None:
        turn.speaker = speaker
    if text is not None:
        turn.text = text
    turn.edited = True
    db.commit()
    db.refresh(turn)
    return turn


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def clean_note_fields(data: dict) -> dict:
    """Validate and normalise the four note fields."""
    try:
        chief = str(data.get("chief_complaint") or "").strip()
        assessment = str(data.get("assessment") or "").strip()
        plan = str(data.get("plan") or "").strip()
        points_in = data.get("discussion_points") or []
        if isinstance(points_in, str):
            points_in = [line for line in points_in.splitlines()]
        points = [str(p).strip() for p in points_in if str(p).strip()]
    except (TypeError, ValueError) as exc:
        raise InvalidNote("The note fields are not in the expected shape.") from exc

    if len(chief) > MAX_NOTE_TEXT or len(assessment) > MAX_NOTE_TEXT or len(plan) > MAX_NOTE_TEXT:
        raise InvalidNote("A note field is too long.")
    if len(points) > MAX_DISCUSSION_POINTS or any(len(p) > MAX_DISCUSSION_POINT_LENGTH for p in points):
        raise InvalidNote("Too many or too long discussion points.")
    if not (chief or points or assessment or plan):
        raise InvalidNote("The note is empty.")
    return {"chief_complaint": chief, "discussion_points": points, "assessment": assessment, "plan": plan}


def note_content(note: ConsultationNote) -> dict:
    return {
        "chief_complaint": note.chief_complaint,
        "discussion_points": list(note.discussion_points or []),
        "assessment": note.assessment,
        "plan": note.plan,
    }


def changed_fields(before: dict, after: dict) -> list[str]:
    return [name for name in NOTE_FIELDS if before.get(name) != after.get(name)]


def latest_note(consultation: Consultation) -> ConsultationNote | None:
    return consultation.notes[-1] if consultation.notes else None


def add_note(
    db: Session, consultation: Consultation, data: dict, source: str, author_auth_id: str | None
) -> ConsultationNote:
    """Save a new version of the note. Earlier versions are left untouched."""
    fields = clean_note_fields(data)
    last = latest_note(consultation)
    note = ConsultationNote(
        note_id=str(uuid.uuid4()),
        consultation_id=consultation.consultation_id,
        version=(last.version + 1) if last else 1,
        status=NoteStatus.draft.value,
        source=source,
        created_by_auth_id=author_auth_id,
        **fields,
    )
    db.add(note)
    # AI drafts and edits both leave the consultation waiting for sign-off.
    if consultation.status != ConsultationStatus.note_approved.value:
        consultation.status = ConsultationStatus.draft_ready.value
        consultation.failure_reason = None
    db.commit()
    db.refresh(note)
    db.refresh(consultation)
    # A follow-up written in the plan ("come back next week") becomes a suggestion.
    try:
        from app.services import followup_service

        followup_service.refresh_suggestion(db, consultation, note)
    except Exception:  # noqa: BLE001 - the note is saved either way
        db.rollback()
    return note


def approve_note(
    db: Session, consultation: Consultation, note: ConsultationNote, doctor_auth_id: str
) -> ConsultationNote:
    """Sign off the newest version. It is locked from then on, and the audit log
    records what the AI drafted against what the doctor approved."""
    if note.status == NoteStatus.approved.value:
        raise NoteLocked("already_approved")
    if latest_note(consultation).note_id != note.note_id:
        raise NoteLocked("not_latest_version")

    first_ai = next((n for n in consultation.notes if n.source == "ai"), None)
    ai_draft = note_content(first_ai) if first_ai else None
    final = note_content(note)

    note.status = NoteStatus.approved.value
    note.approved_by_auth_id = doctor_auth_id
    note.approved_at = utcnow()
    consultation.status = ConsultationStatus.note_approved.value
    db.commit()
    db.refresh(note)

    write_audit_log(
        db,
        action="consultation_note_approved",
        actor=f"doctor:{doctor_auth_id}",
        user_id=consultation.patient_auth_id,
        relevant_metadata={
            "consultation_id": consultation.consultation_id,
            "approved_version": note.version,
            "source": note.source,
            "changed_fields": changed_fields(ai_draft, final) if ai_draft else list(NOTE_FIELDS),
        },
        before_value=ai_draft,
        after_value=final,
    )
    return note


# ---------------------------------------------------------------------------
# Deleting recordings
# ---------------------------------------------------------------------------

def _remove_audio_file(consultation: Consultation) -> bool:
    if not consultation.audio_path:
        return False
    path = _audio_file(consultation.consultation_id)
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def delete_audio(db: Session, consultation: Consultation, actor: str, reason: str) -> None:
    """Remove the recording only (transcript and notes stay)."""
    if consultation.audio_path and consultation.audio_deleted_at is None:
        _remove_audio_file(consultation)
        consultation.audio_deleted_at = utcnow()
        db.commit()
        write_audit_log(
            db,
            action="consultation_audio_deleted",
            actor=actor,
            user_id=consultation.patient_auth_id,
            relevant_metadata={"consultation_id": consultation.consultation_id, "reason": reason},
        )


def delete_transcript(db: Session, consultation: Consultation, actor: str, reason: str) -> None:
    """Remove the transcript only (notes stay)."""
    if consultation.transcript_deleted_at is None and consultation.turns:
        for turn in list(consultation.turns):
            db.delete(turn)
        consultation.transcript_deleted_at = utcnow()
        db.commit()
        write_audit_log(
            db,
            action="consultation_transcript_deleted",
            actor=actor,
            user_id=consultation.patient_auth_id,
            relevant_metadata={"consultation_id": consultation.consultation_id, "reason": reason},
        )


def delete_recording(db: Session, consultation: Consultation, actor: str, reason: str = "requested") -> Consultation:
    """Remove the audio AND the transcript. Approved (and draft) notes are kept."""
    delete_audio(db, consultation, actor, reason)
    delete_transcript(db, consultation, actor, reason)
    if consultation.status != ConsultationStatus.note_approved.value:
        consultation.status = ConsultationStatus.recording_deleted.value
    db.commit()
    db.refresh(consultation)
    return consultation


def purge_expired(db: Session, now: datetime | None = None) -> dict:
    """Delete recordings and transcripts that have outlived the retention policy.

    Audio goes after AUDIO_RETENTION_DAYS (default 30), transcripts after
    TRANSCRIPT_RETENTION_DAYS (default 90), both counted from the upload.
    Notes are never touched.
    """
    now = now or utcnow()
    audio_cutoff = now - timedelta(days=settings.AUDIO_RETENTION_DAYS)
    transcript_cutoff = now - timedelta(days=settings.TRANSCRIPT_RETENTION_DAYS)
    removed = {"audio": 0, "transcripts": 0}

    for consultation in db.query(Consultation).all():
        stamp = _aware(consultation.audio_uploaded_at) or _aware(consultation.created_at)
        if stamp is None:
            continue
        if consultation.audio_path and consultation.audio_deleted_at is None and stamp <= audio_cutoff:
            delete_audio(db, consultation, "retention-policy", "audio_retention_expired")
            removed["audio"] += 1
        if consultation.turns and stamp <= transcript_cutoff:
            delete_transcript(db, consultation, "retention-policy", "transcript_retention_expired")
            removed["transcripts"] += 1
    return removed
