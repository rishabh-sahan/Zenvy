"""Prescriptions, doses and the agents' messages.

Access rules (same as the rest of the clinical API)
* the doctor      - only for their own consultations / appointments (staff token linked to a doctor)
* the patient     - only their own medicines, proven by their auth_id (pilot level: the login is phone-only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import get_optional_staff, require_doctor
from app.db.deps import get_db
from app.models.ai_appointment import AIAppointment
from app.models.authentication import Authentication
from app.models.consultation import Consultation
from app.models.doctor import Doctor
from app.models.prescription import PrescriptionItem
from app.schemas.prescription import (
    ActionIn,
    AgentMessageOut,
    MedicationsOut,
    PrescriptionIn,
    PrescriptionOut,
    PrescriptionStateOut,
    ReadIn,
    TakenOut,
    VisitOut,
)
from app.services import agent_service
from app.services import prescription_service as svc

router = APIRouter(prefix="/api/v1", tags=["prescriptions"])


def _own_consultation(db: Session, consultation_id: str, doctor: Doctor) -> Consultation:
    consultation = db.query(Consultation).filter(Consultation.consultation_id == consultation_id).first()
    if consultation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consultation not found")
    if consultation.doctor_id != doctor.doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your consultation")
    return consultation


def _patient(db: Session, auth_id: str) -> Authentication:
    patient = db.query(Authentication).filter(Authentication.auth_id == auth_id).first()
    if patient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return patient


def _can_carry_forward(db: Session, consultation: Consultation) -> bool:
    appointment = db.get(AIAppointment, consultation.appointment_id)
    if appointment is None or not appointment.parent_appointment_id:
        return False
    parent = db.query(Consultation).filter(Consultation.appointment_id == appointment.parent_appointment_id).first()
    return bool(parent and parent.doctor_id == consultation.doctor_id and svc.latest_signed(db, parent.consultation_id))


# ---------------------------------------------------------------------------
# doctor: the Prescription card
# ---------------------------------------------------------------------------

@router.get("/consultations/{consultation_id}/prescription", response_model=PrescriptionStateOut)
def read_prescription(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    consultation = _own_consultation(db, consultation_id, doctor)
    current = svc.latest(db, consultation.consultation_id)
    signed = svc.latest_signed(db, consultation.consultation_id)
    return PrescriptionStateOut(
        current=svc.prescription_dict(db, current, with_adherence=True),
        signed=svc.prescription_dict(db, signed, with_adherence=True),
        can_carry_forward=_can_carry_forward(db, consultation),
    )


@router.put("/consultations/{consultation_id}/prescription", response_model=PrescriptionOut)
def save_prescription(
    consultation_id: str, payload: PrescriptionIn, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)
):
    """Replace the draft with these medicines (or start the next version if the newest is signed)."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        prescription = svc.save_draft(
            db, consultation, [i.model_dump() for i in payload.items], payload.source, doctor.auth_id
        )
    except svc.PrescriptionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return svc.prescription_dict(db, prescription)


@router.post("/consultations/{consultation_id}/prescription/sign", response_model=PrescriptionOut)
def sign_prescription(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """Lock the draft. The coordinator then schedules the doses and tells the patient."""
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        prescription = svc.sign(db, consultation, doctor.auth_id)
    except svc.NothingToSign as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except svc.PrescriptionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return svc.prescription_dict(db, prescription)


@router.post("/consultations/{consultation_id}/prescription/carry-forward", response_model=PrescriptionOut)
def carry_forward(consultation_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        prescription = svc.carry_forward(db, consultation, doctor.auth_id)
    except svc.PrescriptionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return svc.prescription_dict(db, prescription)


# ---------------------------------------------------------------------------
# doctor agent: its inbox and what it may look up
# ---------------------------------------------------------------------------

@router.get("/agent/messages", response_model=list[AgentMessageOut])
def doctor_messages(unread: bool = True, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    return agent_service.messages_for(db, doctor.auth_id, unread_only=unread)


@router.post("/agent/messages/read")
def doctor_messages_read(payload: ReadIn, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    return {"marked": agent_service.mark_read(db, doctor.auth_id, payload.ids)}


@router.get("/appointments/{appointment_id}/patient-history", response_model=list[VisitOut])
def patient_history(appointment_id: str, db: Session = Depends(get_db), doctor: Doctor = Depends(require_doctor)):
    """This doctor's earlier visits with the same patient (the doctor agent summarises these)."""
    appointment = db.query(AIAppointment).filter(AIAppointment.appointment_id == appointment_id).first()
    if appointment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Appointment not found")
    if appointment.doctor_id != doctor.doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your appointment")
    return agent_service.patient_history(db, appointment, doctor)


@router.post("/agents/actions", status_code=status.HTTP_204_NO_CONTENT)
def record_agent_action(
    payload: ActionIn, db: Session = Depends(get_db), staff: Authentication | None = Depends(get_optional_staff)
):
    """The gateway records what an agent did on someone's behalf (ids and short summaries only)."""
    if payload.agent == "doctor":
        if staff is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "A doctor token is required")
        actor = staff.auth_id
    else:
        actor = _patient(db, payload.auth_id).auth_id if payload.auth_id else None
    agent_service.record_action(
        db, payload.agent, payload.tool, payload.summary, actor_auth_id=actor,
        appointment_id=payload.appointment_id, result=payload.result,
    )


# ---------------------------------------------------------------------------
# patient: Your medicines
# ---------------------------------------------------------------------------

@router.get("/patients/{auth_id}/medications", response_model=MedicationsOut)
def patient_medications(auth_id: str, db: Session = Depends(get_db)):
    patient = _patient(db, auth_id)
    return MedicationsOut(
        prescriptions=svc.medications_for_patient(db, patient.auth_id),
        messages=[AgentMessageOut.model_validate(m) for m in agent_service.messages_for(db, patient.auth_id)],
    )


@router.post("/patients/{auth_id}/doses/taken", response_model=TakenOut)
def doses_taken_now(auth_id: str, db: Session = Depends(get_db)):
    """"I took my medicine": marks the doses of the latest dose time that has come."""
    patient = _patient(db, auth_id)
    doses = svc.mark_taken_now(db, patient.auth_id)
    names = []
    if doses:
        names = [
            row[0] for row in db.query(PrescriptionItem.drug_name).filter(
                PrescriptionItem.item_id.in_([d.item_id for d in doses])
            )
        ]
    return TakenOut(taken=len(doses), medicines=names)


@router.post("/patients/{auth_id}/doses/{dose_id}/taken", response_model=TakenOut)
def dose_taken(auth_id: str, dose_id: str, db: Session = Depends(get_db)):
    patient = _patient(db, auth_id)
    try:
        dose = svc.mark_taken(db, dose_id, patient.auth_id)
    except svc.DoseError as exc:
        code = status.HTTP_404_NOT_FOUND if exc.code == "not_found" else status.HTTP_409_CONFLICT
        raise HTTPException(code, exc.code) from exc
    item = db.get(PrescriptionItem, dose.item_id)
    return TakenOut(taken=1, medicines=[item.drug_name] if item else [])


@router.post("/patients/{auth_id}/messages/read")
def patient_messages_read(auth_id: str, payload: ReadIn, db: Session = Depends(get_db)):
    patient = _patient(db, auth_id)
    return {"marked": agent_service.mark_read(db, patient.auth_id, payload.ids)}
