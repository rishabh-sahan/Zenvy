from datetime import date, time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.doctor import Doctor
from app.models.doctor_slot import DoctorSlot
from app.models.session import Session as SessionModel
from app.schemas.doctor import (
    DoctorResponse,
    SlotHoldRequest,
    SlotReleaseResponse,
    SlotResponse,
)
from app.services.doctor_service import get_doctor, search_doctors
from app.services.slot_service import (
    SlotNotFoundError,
    SlotUnavailableError,
    as_ist,
    hold_slot,
    list_free_slots,
    nearest_free_slots,
    release_slot,
)

router = APIRouter(prefix="/api/v1", tags=["doctors", "slots"])


def _doctor_response(doctor: Doctor) -> DoctorResponse:
    return DoctorResponse(
        doctor_id=doctor.doctor_id,
        name=doctor.name,
        specialty=doctor.specialty,
        hospital_id=doctor.hospital_id,
        hospital_name=doctor.hospital.name,
        city=doctor.hospital.city,
        slot_minutes=doctor.slot_minutes,
    )


def _slot_response(slot: DoctorSlot) -> SlotResponse:
    return SlotResponse(
        slot_id=slot.slot_id,
        doctor_id=slot.doctor_id,
        slot_start=as_ist(slot.slot_start),
        slot_end=as_ist(slot.slot_end),
        status=slot.status,
        held_until=as_ist(slot.held_until) if slot.held_until else None,
    )


@router.get("/doctors", response_model=list[DoctorResponse])
def list_doctors(
    query: str | None = Query(None, description="Doctor name or department, e.g. 'Arjun Rao' or 'Cardiology'"),
    hospital_id: str | None = None,
    city: str | None = None,
    db: Session = Depends(get_db),
):
    return [_doctor_response(d) for d in search_doctors(db, query, hospital_id, city)]


@router.get("/doctors/{doctor_id}", response_model=DoctorResponse)
def read_doctor(doctor_id: str, db: Session = Depends(get_db)):
    doctor = get_doctor(db, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    return _doctor_response(doctor)


@router.get("/doctors/{doctor_id}/slots", response_model=list[SlotResponse])
def list_doctor_slots(
    doctor_id: str,
    day: date = Query(..., alias="date", description="IST date, YYYY-MM-DD"),
    near: time | None = Query(None, description="HH:MM - return only the closest free slots to this time"),
    limit: int = Query(3, ge=1, le=50, description="Used with 'near'"),
    db: Session = Depends(get_db),
):
    """Free slots on one day (IST). Held-but-expired slots count as free."""
    doctor = get_doctor(db, doctor_id)
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found")
    if near is not None:
        slots = nearest_free_slots(db, doctor, day, near, limit)
    else:
        slots = list_free_slots(db, doctor, day)
    return [_slot_response(s) for s in slots]


@router.post("/slots/{slot_id}/hold", response_model=SlotResponse)
def hold_slot_endpoint(slot_id: str, payload: SlotHoldRequest, db: Session = Depends(get_db)):
    """Reserve a slot for this conversation while the patient confirms (default 5 min)."""
    session = db.query(SessionModel).filter(SessionModel.session_id == payload.session_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    try:
        slot = hold_slot(db, slot_id, payload.session_id)
    except SlotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Slot not found") from exc
    except SlotUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="slot_unavailable") from exc
    return _slot_response(slot)


@router.post("/slots/{slot_id}/release", response_model=SlotReleaseResponse)
def release_slot_endpoint(slot_id: str, payload: SlotHoldRequest, db: Session = Depends(get_db)):
    """Give a held slot back (patient said no or changed their mind). Safe to call twice."""
    return SlotReleaseResponse(released=release_slot(db, slot_id, payload.session_id))
