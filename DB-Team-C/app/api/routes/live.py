"""Live Assist: the suggestions shown to the doctor while a consultation is going on.

Only the doctor the appointment was booked with can read or change them. The gateway's
Live Assist engine writes the cards; the doctor page reads them and records what the doctor did.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.security import require_doctor
from app.db.deps import get_db
from app.models.consultation import Consultation
from app.models.doctor import Doctor
from app.models.live_suggestion import LiveSuggestion
from app.services import live_service

router = APIRouter(prefix="/api/v1", tags=["live-assist"])

Kind = Literal["ask", "red_flag", "history", "wrap_up", "consider"]


class CardIn(BaseModel):
    kind: Kind
    text: str = Field(min_length=1, max_length=400)
    evidence: str | None = Field(default=None, max_length=300)
    source: Literal["model", "rule"] = "model"


class CardsIn(BaseModel):
    cards: list[CardIn] = Field(min_length=1, max_length=10)


class CardOut(BaseModel):
    suggestion_id: str
    seq: int
    kind: str
    text: str
    evidence: str | None = None
    source: str
    status: str


class StatusIn(BaseModel):
    status: Literal["dismissed", "pinned", "added", "asked"]


def _own_consultation(db: Session, consultation_id: str, doctor: Doctor) -> Consultation:
    consultation = db.query(Consultation).filter(Consultation.consultation_id == consultation_id).first()
    if consultation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Consultation not found")
    if consultation.doctor_id != doctor.doctor_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This is not your consultation")
    return consultation


def _out(row: LiveSuggestion) -> CardOut:
    return CardOut(
        suggestion_id=row.suggestion_id, seq=row.seq, kind=row.kind, text=row.text,
        evidence=row.evidence, source=row.source, status=row.status,
    )


@router.post("/consultations/{consultation_id}/live/suggestions", response_model=list[CardOut],
             status_code=status.HTTP_201_CREATED)
def add_cards(consultation_id: str, payload: CardsIn, db: Session = Depends(get_db),
              doctor: Doctor = Depends(require_doctor)):
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        rows = live_service.add_suggestions(db, consultation, [c.model_dump() for c in payload.cards])
    except live_service.ConsentRequired as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "consent_required") from exc
    except live_service.InvalidSuggestion as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    return [_out(r) for r in rows]


@router.get("/consultations/{consultation_id}/live/suggestions", response_model=list[CardOut])
def read_cards(consultation_id: str, after: int = Query(0, ge=0), db: Session = Depends(get_db),
               doctor: Doctor = Depends(require_doctor)):
    consultation = _own_consultation(db, consultation_id, doctor)
    return [_out(r) for r in live_service.list_suggestions(db, consultation, after)]


@router.patch("/consultations/{consultation_id}/live/suggestions/{suggestion_id}", response_model=CardOut)
def act_on_card(consultation_id: str, suggestion_id: str, payload: StatusIn, db: Session = Depends(get_db),
                doctor: Doctor = Depends(require_doctor)):
    consultation = _own_consultation(db, consultation_id, doctor)
    try:
        return _out(live_service.set_status(db, consultation, suggestion_id, payload.status))
    except live_service.SuggestionNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suggestion not found") from exc
    except live_service.InvalidSuggestion as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
