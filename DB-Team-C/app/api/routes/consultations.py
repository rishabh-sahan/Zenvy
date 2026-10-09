"""Consent, consultations, transcripts and notes.

Access rules:
* patient consent    - the patient, proven by their auth_id (pilot-level: the
                       web login is phone-only), or the treating doctor on their behalf
* everything clinical - only the doctor the appointment was booked with
                       (a staff token whose account is linked to that doctor)
"""

from datetime import datetime, time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_optional_staff, require_doctor
from app.db.deps import get_db
from app.models.ai_appointment import AIAppointment, AppointmentStatus
from app.models.authentication import Authentication
from app.models.consultation import Consultation, ConsultationNote, ConsultationStatus, ConsultationTurn
from app.models.doctor import Doctor
from app.models.follow_up import FollowUp
from app.models.reminder import Reminder
from app.schemas.consultation import (
    AppointmentHistoryOut,
    ConsentMessageResponse,
    ConsentRequest,
    ConsentResponse,
    ConsultationCreate,
    ConsultationDetailResponse,
    ConsultationResponse,
    DoctorAppointmentResponse,
    FollowUpIn,
    FollowUpOut,
    HistoryEvent,
    NoteApproveOut,
    NoteIn,
    NoteOut,
    PatientAppointmentResponse,
    StatusPatch,
    TranscriptIn,
    TurnOut,
    TurnPatch,
)
from app.services import consultation_service as svc
from app.services import followup_service
from app.services import prescription_service
from app.services.appointment_service import change_blocker
from app.services.crypto_service import EncryptionNotConfigured
from app.services.slot_service import IST, as_ist, as_utc, utcnow

router = APIRouter(prefix="/api/v1", tags=["consultations"])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _appointment(db: Session, appointment_id: str) -> AIAppointment:
    appointment = db.query(AIAppointment).filter(AIAppointment.appointment_id == appointment_id).first()
    if appointment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    return appointment


def _own_appointment(db: Session, appointment_id: str, doctor: Doctor) -> AIAppointment:
    appointment = _appointment(db, appointment_id)
    if appointment.doctor_id != doctor.doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your appointment")
    return appointment


def _own_consultation(db: Session, consultation_id: str, doctor: Doctor) -> Consultation:
    consultation = db.query(Consultation).filter(Consultation.consultation_id == consultation_id).first()
    if consultation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consultation not found")
    if consultation.doctor_id != doctor.doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your consultation")
    return consultation


def _consultation_response(db: Session, consultation: Consultation, detail: bool = False):
    consent = svc.current_consent(db, consultation.appointment_id)
    data = dict(
        consultation_id=consultation.consultation_id,
        appointment_id=consultation.appointment_id,
        doctor_id=consultation.doctor_id,
        mode=consultation.mode,
        status=consultation.status,
        failure_reason=consultation.failure_reason,
        audio_seconds=consultation.audio_seconds,
        has_recording=bool(consultation.audio_path and consultation.audio_deleted_at is None),
        language_code=consultation.language_code,
        consent_state=svc.consent_state(consent),
        created_at=consultation.created_at,
    )
    if not detail:
        return ConsultationResponse(**data)
    return ConsultationDetailResponse(
        **data,
        turns=[TurnOut.model_validate(t) for t in consultation.turns],
        notes=[NoteOut.model_validate(n) for n in consultation.notes],
    )


def _patient_label(appointment: AIAppointment) -> str:
    digits = "".join(ch for ch in (appointment.patient_phone_no or "") if ch.isdigit())
    return f"Patient ••••{digits[-4:]}" if len(digits) >= 4 else "Patient"


def _start_of_today_utc() -> datetime:
    today = utcnow().astimezone(IST).date()
    return as_utc(datetime.combine(today, time.min, tzinfo=IST))


def _consent_response(appointment_id: str, consent) -> ConsentResponse:
    return ConsentResponse(
        appointment_id=appointment_id,
        state=svc.consent_state(consent),
        recorded_by=consent.recorded_by if consent else None,
        message_version=consent.message_version if consent else None,
        recorded_at=consent.created_at if consent else None,
    )


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------

@router.get("/consent-message", response_model=ConsentMessageResponse)
def consent_message(language: str = Query("en")):
    """The text the patient is shown before agreeing to be recorded."""
    lang = language if language in svc.CONSENT_MESSAGES else "en"
    return ConsentMessageResponse(
        version=svc.CONSENT_MESSAGE_VERSION, language=lang, message=svc.CONSENT_MESSAGES[lang]
    )


def _who_is_asking(db: Session, appointment: AIAppointment, staff, auth_id: str | None):
    """Return (recorded_by, auth_id of the person) or raise 403."""
    if staff is not None:
        doctor = db.query(Doctor).filter(Doctor.auth_id == staff.auth_id).first()
        if doctor is None or doctor.doctor_id != appointment.doctor_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your appointment")
        return "doctor_on_behalf", staff.auth_id
    patient = svc.patient_for_appointment(db, appointment)
    if not auth_id or patient is None or patient.auth_id != auth_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This appointment does not belong to you")
    return "patient", patient.auth_id


@router.post("/appointments/{appointment_id}/consent", response_model=ConsentResponse)
def set_consent(
    appointment_id: str,
    payload: ConsentRequest,
    db: Session = Depends(get_db),
    staff: Authentication | None = Depends(get_optional_staff),
):
    """Record that the patient agrees (or refuses) to the consultation being recorded."""
    appointment = _appointment(db, appointment_id)
    recorded_by, by_id = _who_is_asking(db, appointment, staff, payload.auth_id)
    if appointment.status == AppointmentStatus.cancelled:
        raise HTTPException(status.HTTP_409_CONFLICT, "This appointment is cancelled")
    try:
        consent = svc.record_consent(db, appointment, payload.consent_given, recorded_by, by_id, payload.language)
    except svc.PatientDeclined as exc:
        # Only the patient can change their own refusal.
        raise HTTPException(status.HTTP_409_CONFLICT, "patient_declined") from exc
    return _consent_response(appointment_id, consent)


@router.get("/appointments/{appointment_id}/consent", response_model=ConsentResponse)
def get_consent(
    appointment_id: str,
    auth_id: str | None = None,
    db: Session = Depends(get_db),
    staff: Authentication | None = Depends(get_optional_staff),
):
    appointment = _appointment(db, appointment_id)
    _who_is_asking(db, appointment, staff, auth_id)
    return _consent_response(appointment_id, svc.current_consent(db, appointment_id))


# ---------------------------------------------------------------------------
# appointment lists
# ---------------------------------------------------------------------------

@router.get("/doctor/appointments", response_model=list[DoctorAppointmentResponse])
def doctor_appointments(db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """The logged-in doctor's appointments from today on, soonest first."""
    appointments = (
        db.query(AIAppointment)
        .filter(
            AIAppointment.doctor_id == doctor.doctor_id,
            AIAppointment.status != AppointmentStatus.cancelled,
            AIAppointment.appointment_datetime >= _start_of_today_utc(),
        )
        .order_by(AIAppointment.appointment_datetime)
        .limit(200)
        .all()
    )
    result = []
    for appointment in appointments:
        consent = svc.current_consent(db, appointment.appointment_id)
        consultation = svc.get_consultation_for_appointment(db, appointment.appointment_id)
        last_note = svc.latest_note(consultation) if consultation else None
        prescription = prescription_service.latest(db, consultation.consultation_id) if consultation else None
        result.append(
            DoctorAppointmentResponse(
                appointment_id=appointment.appointment_id,
                appointment_datetime=as_ist(appointment.appointment_datetime),
                status=appointment.status.value,
                patient_label=_patient_label(appointment),
                consent_state=svc.consent_state(consent),
                consent_recorded_by=consent.recorded_by if consent else None,
                appointment_type=appointment.appointment_type,
                consultation_id=consultation.consultation_id if consultation else None,
                consultation_status=consultation.status if consultation else None,
                note_status=last_note.status if last_note else None,
                prescription_status=prescription.status if prescription else None,
            )
        )
    return result


@router.get("/patients/{auth_id}/appointments", response_model=list[PatientAppointmentResponse])
def patient_appointments(auth_id: str, db: Session = Depends(get_db)):
    """A patient's upcoming appointments and whether they agreed to recording."""
    patient = db.query(Authentication).filter(Authentication.auth_id == auth_id).first()
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    appointments = (
        db.query(AIAppointment)
        .filter(
            AIAppointment.patient_phone_no == patient.phone_no,
            AIAppointment.status != AppointmentStatus.cancelled,
            AIAppointment.appointment_datetime >= _start_of_today_utc(),
        )
        .order_by(AIAppointment.appointment_datetime)
        .limit(50)
        .all()
    )
    result = []
    for appointment in appointments:
        doctor = db.query(Doctor).filter(Doctor.doctor_id == appointment.doctor_id).first() if appointment.doctor_id else None
        blocker = change_blocker(db, appointment)
        result.append(
            PatientAppointmentResponse(
                appointment_id=appointment.appointment_id,
                doctor_id=appointment.doctor_id,
                appointment_type=appointment.appointment_type,
                can_change=blocker is None and bool(appointment.slot_id),
                change_blocker=blocker or (None if appointment.slot_id else "not_reschedulable"),
                doctor_name=appointment.doctor_name,
                hospital_name=doctor.hospital.name if doctor else None,
                appointment_datetime=as_ist(appointment.appointment_datetime),
                status=appointment.status.value,
                consent_state=svc.consent_state(svc.current_consent(db, appointment.appointment_id)),
            )
        )
    return result


# ---------------------------------------------------------------------------
# consultation
# ---------------------------------------------------------------------------

@router.post("/consultations", response_model=ConsultationResponse, status_code=status.HTTP_201_CREATED)
def create_consultation(
    payload: ConsultationCreate, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)
):
    """Start a consultation for one of your appointments. Needs the patient's consent."""
    appointment = _own_appointment(db, payload.appointment_id, doctor)
    if appointment.status == AppointmentStatus.cancelled:
        raise HTTPException(status.HTTP_409_CONFLICT, "This appointment is cancelled")
    try:
        consultation = svc.create_consultation(db, appointment, payload.mode)
    except svc.ConsentRequired as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "consent_required") from exc
    return _consultation_response(db, consultation)


@router.get("/consultations/{consultation_id}", response_model=ConsultationDetailResponse)
def read_consultation(
    consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)
):
    return _consultation_response(db, _own_consultation(db, consultation_id, doctor), detail=True)


@router.post("/consultations/{consultation_id}/audio", response_model=ConsultationResponse)
async def upload_audio(
    consultation_id: str,
    request: Request,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Store the recording (raw WAV in the request body), encrypted."""
    consultation = _own_consultation(db, consultation_id, doctor)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.MAX_AUDIO_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The recording is too large")
    body = await request.body()
    try:
        consultation = svc.store_audio(db, consultation, body)
    except svc.ConsentRequired as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "consent_required") from exc
    except svc.RecordingAlreadyExists as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "recording_exists") from exc
    except svc.InvalidAudio as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    except EncryptionNotConfigured as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    return _consultation_response(db, consultation)


@router.put("/consultations/{consultation_id}/transcript", response_model=ConsultationDetailResponse)
def put_transcript(
    consultation_id: str,
    payload: TranscriptIn,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Store the labelled transcript (used by the scribe pipeline)."""
    consultation = _own_consultation(db, consultation_id, doctor)
    if consultation.status == ConsultationStatus.recording_deleted.value:
        raise HTTPException(status.HTTP_409_CONFLICT, "The recording was deleted")
    try:
        # Consent can be withdrawn after the recording was made.
        svc.require_consent(db, consultation.appointment_id)
    except svc.ConsentRequired as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "consent_required") from exc
    consultation = svc.replace_transcript(
        db, consultation, [t.model_dump() for t in payload.turns], payload.language_code
    )
    return _consultation_response(db, consultation, detail=True)


@router.patch("/consultations/{consultation_id}/status", response_model=ConsultationResponse)
def patch_status(
    consultation_id: str,
    payload: StatusPatch,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Progress updates from the scribe pipeline (transcribing, summarising, failed)."""
    if payload.status not in svc.PIPELINE_STATUSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "That status cannot be set here")
    consultation = _own_consultation(db, consultation_id, doctor)
    # An approved or deleted consultation does not go backwards.
    if consultation.status not in (
        ConsultationStatus.note_approved.value,
        ConsultationStatus.recording_deleted.value,
    ):
        consultation = svc.set_status(db, consultation, payload.status, payload.failure_reason)
    return _consultation_response(db, consultation)


@router.patch("/consultations/{consultation_id}/turns/{turn_id}", response_model=TurnOut)
def patch_turn(
    consultation_id: str,
    turn_id: str,
    payload: TurnPatch,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Correct who said a line, or fix a mis-heard word."""
    consultation = _own_consultation(db, consultation_id, doctor)
    turn = (
        db.query(ConsultationTurn)
        .filter(ConsultationTurn.consultation_id == consultation.consultation_id, ConsultationTurn.turn_id == turn_id)
        .first()
    )
    if turn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Turn not found")
    return svc.update_turn(db, turn, payload.speaker, payload.text)


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------

@router.post("/consultations/{consultation_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_note(
    consultation_id: str,
    payload: NoteIn,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Save a new version of the note (an AI draft, a regenerated draft, or a doctor's edit)."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        return svc.add_note(
            db,
            consultation,
            payload.model_dump(exclude={"source"}),
            payload.source,
            doctor.auth_id,
        )
    except svc.InvalidNote as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


def _follow_up_out(db: Session, consultation: Consultation, follow_up: FollowUp | None) -> FollowUpOut | None:
    if follow_up is None:
        return None
    new_when = None
    if follow_up.new_appointment_id:
        booked = db.query(AIAppointment).filter(AIAppointment.appointment_id == follow_up.new_appointment_id).first()
        new_when = as_ist(booked.appointment_datetime) if booked else None
    preview = followup_service.preview_slot(db, consultation, follow_up)
    return FollowUpOut(
        status=follow_up.status,
        interval_days=follow_up.interval_days,
        source_text=follow_up.source_text,
        suggested_date=follow_up.suggested_date,
        suggested_time=follow_up.suggested_time.strftime("%H:%M") if follow_up.suggested_time else None,
        edited_by_doctor=follow_up.edited_by_doctor,
        failure_reason=follow_up.failure_reason,
        new_appointment_id=follow_up.new_appointment_id,
        new_appointment_datetime=new_when,
        preview_datetime=as_ist(preview.slot_start) if preview is not None else None,
    )


@router.post("/consultations/{consultation_id}/notes/{note_id}/approve", response_model=NoteApproveOut)
def approve_note(
    consultation_id: str,
    note_id: str,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """Sign off the newest version of the note. It is locked from then on.

    If the plan holds a follow-up the doctor has not removed, the same click books it.
    A follow-up that cannot be booked never undoes the approval; its status says why.
    """
    consultation = _own_consultation(db, consultation_id, doctor)
    note = (
        db.query(ConsultationNote)
        .filter(ConsultationNote.consultation_id == consultation.consultation_id, ConsultationNote.note_id == note_id)
        .first()
    )
    if note is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    try:
        approved = svc.approve_note(db, consultation, note, doctor.auth_id)
    except svc.NoteLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    follow_up, _ = followup_service.book_on_approval(db, consultation, doctor.auth_id)
    result = NoteApproveOut.model_validate(approved)
    result.follow_up = _follow_up_out(db, consultation, follow_up)
    return result


# ---------------------------------------------------------------------------
# follow-up
# ---------------------------------------------------------------------------

@router.get("/consultations/{consultation_id}/follow-up", response_model=FollowUpOut | None)
def read_follow_up(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """The follow-up found in the note's plan (or chosen by the doctor), if any."""
    consultation = _own_consultation(db, consultation_id, doctor)
    return _follow_up_out(db, consultation, followup_service.get_follow_up(db, consultation))


@router.put("/consultations/{consultation_id}/follow-up", response_model=FollowUpOut)
def change_follow_up(
    consultation_id: str,
    payload: FollowUpIn,
    db: Session = Depends(get_db),
    doctor: Doctor = Depends(require_doctor),
):
    """The doctor picks (or changes) the follow-up date and time."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        follow_up = followup_service.set_follow_up(db, consultation, payload.date, payload.time, doctor.auth_id)
    except followup_service.FollowUpNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.code) from exc
    return _follow_up_out(db, consultation, follow_up)


@router.delete("/consultations/{consultation_id}/follow-up", response_model=FollowUpOut | None)
def remove_follow_up(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """The doctor says there is no follow-up."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        follow_up = followup_service.decline_follow_up(db, consultation, doctor.auth_id)
    except followup_service.FollowUpNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.code) from exc
    return _follow_up_out(db, consultation, follow_up)


@router.post("/consultations/{consultation_id}/follow-up/book", response_model=FollowUpOut)
def book_follow_up(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """Book the follow-up now (used to retry after 'no free slot', or to book before approving)."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        followup_service.book_follow_up(db, consultation, doctor.auth_id)
    except followup_service.FollowUpUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "no_free_slot") from exc
    except followup_service.FollowUpNotAllowed as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.code) from exc
    return _follow_up_out(db, consultation, followup_service.get_follow_up(db, consultation))


# ---------------------------------------------------------------------------
# history of one appointment
# ---------------------------------------------------------------------------

@router.get("/appointments/{appointment_id}/history", response_model=AppointmentHistoryOut)
def appointment_history(appointment_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """Everything recorded about one of your appointments: booking, consent, consultation,
    follow-up, and every reminder or notice (with its status). No phone numbers."""
    appointment = _own_appointment(db, appointment_id, doctor)
    consent = svc.current_consent(db, appointment_id)
    consultation = svc.get_consultation_for_appointment(db, appointment_id)
    replaced_by = db.query(AIAppointment).filter(AIAppointment.rescheduled_from_id == appointment_id).first()
    follow_up_row = db.query(FollowUp).filter(FollowUp.appointment_id == appointment_id).first()
    reminders = (
        db.query(Reminder)
        .filter(Reminder.appointment_id == appointment_id, Reminder.kind != "medication")
        .order_by(Reminder.send_at)
        .all()
    )

    events: list[HistoryEvent] = [
        HistoryEvent(at=appointment.created_at, kind="booked", detail=f"{appointment.appointment_type} appointment booked"),
    ]
    if appointment.rescheduled_from_id:
        events.append(HistoryEvent(at=appointment.created_at, kind="rescheduled", detail="moved here from an earlier time"))
    if consent is not None:
        events.append(HistoryEvent(at=consent.created_at, kind="consent", detail=f"recording {svc.consent_state(consent)} ({consent.recorded_by})"))
    if consultation is not None:
        events.append(HistoryEvent(at=consultation.created_at, kind="consultation", detail=f"consultation {consultation.status}"))
    if follow_up_row is not None:
        events.append(HistoryEvent(at=follow_up_row.updated_at, kind="follow_up", detail=f"follow-up {follow_up_row.status}"))
    if appointment.cancelled_at:
        events.append(HistoryEvent(at=appointment.cancelled_at, kind="cancelled", detail=f"cancelled by {appointment.cancelled_by}: {appointment.cancel_reason}"))
    for reminder in reminders:
        events.append(HistoryEvent(at=reminder.sent_at or reminder.send_at, kind="reminder", detail=f"{reminder.kind} to {reminder.recipient_type}: {reminder.status}"))
    events.sort(key=lambda event: event.at.timestamp() if event.at else 0)

    return AppointmentHistoryOut(
        appointment_id=appointment.appointment_id,
        appointment_type=appointment.appointment_type,
        status=appointment.status.value,
        appointment_datetime=as_ist(appointment.appointment_datetime),
        cancelled_by=appointment.cancelled_by,
        cancel_reason=appointment.cancel_reason,
        rescheduled_from_id=appointment.rescheduled_from_id,
        rescheduled_to_id=replaced_by.appointment_id if replaced_by else None,
        parent_appointment_id=appointment.parent_appointment_id,
        follow_up_appointment_id=follow_up_row.new_appointment_id if follow_up_row else None,
        consent_state=svc.consent_state(consent),
        consultation_status=consultation.status if consultation else None,
        reminders=[
            {
                "kind": r.kind,
                "recipient": r.recipient_type,
                "status": r.status,
                "send_at": as_ist(r.send_at).isoformat(),
                "sent_at": as_ist(r.sent_at).isoformat() if r.sent_at else None,
                "attempts": r.attempts,
                "mode": r.mode,
                "message": r.message_text,
                "error": r.last_error,
            }
            for r in reminders
        ],
        events=events,
    )


# ---------------------------------------------------------------------------
# deleting
# ---------------------------------------------------------------------------

@router.delete("/consultations/{consultation_id}/recording", response_model=ConsultationResponse)
def delete_recording(
    consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)
):
    """Delete the audio and the transcript. Notes (including the approved one) are kept."""
    consultation = _own_consultation(db, consultation_id, doctor)
    consultation = svc.delete_recording(db, consultation, actor=f"doctor:{doctor.auth_id}")
    return _consultation_response(db, consultation)
