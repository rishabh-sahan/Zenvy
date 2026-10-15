"""Live Assist storage: the cards shown to the doctor during a consultation, and what they did with them.

Rules
* only the treating doctor reads or changes them (the routes check that);
* nothing is stored unless the patient's recording consent is "granted";
* a card is a suggestion, never a medical record: it is not part of any note;
* it is deleted with the transcript (consent withdrawal, deleting the recording, the retention purge).
"""

import uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.consultation import Consultation
from app.models.live_suggestion import LiveSuggestion, SuggestionKind, SuggestionStatus
from app.services.slot_service import utcnow

MAX_PER_CONSULTATION = 300      # a runaway client cannot fill the table
MAX_TEXT = 400
MAX_EVIDENCE = 300
KINDS = {k.value for k in SuggestionKind}
STATUSES = {s.value for s in SuggestionStatus}


class ConsentRequired(Exception):
    pass


class InvalidSuggestion(Exception):
    pass


class SuggestionNotFound(Exception):
    pass


def _consent_granted(db: Session, consultation: Consultation) -> bool:
    from app.services import consultation_service as svc   # (imported here: that module imports this one)

    return svc.consent_state(svc.current_consent(db, consultation.appointment_id)) == "granted"


def add_suggestions(db: Session, consultation: Consultation, cards: list[dict]) -> list[LiveSuggestion]:
    if not _consent_granted(db, consultation):
        raise ConsentRequired("The patient has not agreed to recording.")
    current = db.query(func.count(LiveSuggestion.suggestion_id)).filter(
        LiveSuggestion.consultation_id == consultation.consultation_id
    ).scalar() or 0
    if current + len(cards) > MAX_PER_CONSULTATION:
        raise InvalidSuggestion("Too many suggestions for one consultation.")

    cleaned = []
    for card in cards:
        kind = str(card.get("kind") or "")
        text = " ".join(str(card.get("text") or "").split())
        if kind not in KINDS:
            raise InvalidSuggestion(f"Unknown kind: {kind!r}")
        if not text:
            raise InvalidSuggestion("A suggestion needs text.")
        evidence = " ".join(str(card.get("evidence") or "").split())[:MAX_EVIDENCE] or None
        source = card.get("source") if card.get("source") in ("model", "rule") else "model"
        cleaned.append((kind, text[:MAX_TEXT], evidence, source))

    last = db.query(func.max(LiveSuggestion.seq)).filter(
        LiveSuggestion.consultation_id == consultation.consultation_id
    ).scalar() or 0
    rows = []
    for offset, (kind, text, evidence, source) in enumerate(cleaned, start=1):
        row = LiveSuggestion(
            suggestion_id=str(uuid.uuid4()), consultation_id=consultation.consultation_id, seq=last + offset,
            kind=kind, text=text, evidence=evidence, source=source, status=SuggestionStatus.shown.value,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def list_suggestions(db: Session, consultation: Consultation, after: int = 0) -> list[LiveSuggestion]:
    return (
        db.query(LiveSuggestion)
        .filter(LiveSuggestion.consultation_id == consultation.consultation_id, LiveSuggestion.seq > after)
        .order_by(LiveSuggestion.seq)
        .all()
    )


def set_status(db: Session, consultation: Consultation, suggestion_id: str, status: str) -> LiveSuggestion:
    if status not in STATUSES - {SuggestionStatus.shown.value}:
        raise InvalidSuggestion(f"Unknown status: {status!r}")
    row = (
        db.query(LiveSuggestion)
        .filter(LiveSuggestion.consultation_id == consultation.consultation_id,
                LiveSuggestion.suggestion_id == suggestion_id)
        .first()
    )
    if row is None:
        raise SuggestionNotFound(suggestion_id)
    row.status = status
    row.acted_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def delete_for_consultation(db: Session, consultation_id: str) -> int:
    """Remove every suggestion of a consultation. Used when the transcript is deleted."""
    count = (
        db.query(LiveSuggestion).filter(LiveSuggestion.consultation_id == consultation_id)
        .delete(synchronize_session=False)
    )
    db.commit()
    return count


def delete_older_than(db: Session, cutoff: datetime) -> int:
    """The retention purge: suggestions follow the transcript's retention."""
    count = db.query(LiveSuggestion).filter(LiveSuggestion.created_at <= cutoff).delete(synchronize_session=False)
    db.commit()
    return count
