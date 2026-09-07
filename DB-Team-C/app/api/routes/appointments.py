from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioRestException

from app.db.deps import get_db
from app.models.authentication import Authentication
from app.models.session import Session as SessionModel
from app.schemas.ai_appointment import AIAppointmentCreate, AIAppointmentResponse
from app.services.appointment_service import create_appointment, get_session_appointments
from app.services.whatsapp_service import send_appointment_notification

router = APIRouter(prefix="/api/v1/appointments", tags=["appointments"])


@router.post("", response_model=AIAppointmentResponse, status_code=status.HTTP_201_CREATED)
def create_appointment_endpoint(payload: AIAppointmentCreate, db: Session = Depends(get_db)):
    session = db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    if not payload.patient_uhid.strip() or not payload.doctor_name.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="patient_uhid and doctor_name are required",
        )

    # The session's user_id may hold either the patient's auth_id (set at
    # login) or, for older/anonymous sessions, their raw phone_no directly --
    # match on either so we can look up their phone number for the WhatsApp
    # confirmation below. No match just means no notification is sent.
    authentication = (
        db.query(Authentication)
        .filter(
            (Authentication.auth_id == session.user_id)
            | (Authentication.phone_no == session.user_id)
        )
        .first()
    )
    appointment = create_appointment(
        db,
        payload,
        patient_phone_no=authentication.phone_no if authentication is not None else None,
    )

    # The appointment is already committed by this point, so a failed WhatsApp
    # send must NOT turn into an error status: the gateway's orchestrator
    # treats any non-2xx from here as "booking failed", tells the patient to
    # try again, and they end up double-booked while the first appointment sits
    # in the database. Report the booking as created and surface the
    # notification outcome in the body instead, loudly logged for staff.
    if authentication is None:
        notification_status = "skipped"
    else:
        try:
            send_appointment_notification(authentication.phone_no, appointment)
            notification_status = "sent"
        except (RuntimeError, TwilioRestException) as exc:
            notification_status = "failed"
            print(
                "[appointments] WhatsApp confirmation FAILED for appointment "
                f"{appointment.appointment_id}: {exc}. The appointment IS "
                "booked -- the patient has not been notified."
            )

    response = AIAppointmentResponse.model_validate(appointment)
    response.notification_status = notification_status
    return response


@router.get("/session/{session_id}", response_model=list[AIAppointmentResponse])
def list_appointments(session_id: str, db: Session = Depends(get_db)):
    return get_session_appointments(db, session_id)
