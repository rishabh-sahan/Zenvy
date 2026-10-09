"""Slot generation and locking.

How double-booking is prevented
-------------------------------
Every bookable time is a row in ``doctor_slots`` (unique per doctor + start).
A patient claims it in two steps, and each step is ONE conditional UPDATE, so
the database decides the winner and two simultaneous requests can never both
succeed:

1. ``hold_slot``  - available (or expired hold) -> held by this session, for
   SLOT_HOLD_SECONDS, while the patient confirms.
2. ``claim_held_slot_for_booking`` - held by this session -> booked. This runs
   in the same transaction as the appointment INSERT, so a slot is never
   "booked" without an appointment or vice versa.

A partial unique index on ``ai_appointments(slot_id)`` is a last safety net.

All datetimes handed to SQL are converted to UTC first so comparisons behave
the same on PostgreSQL (timestamptz) and SQLite (naive, used in tests).
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.doctor import Doctor
from app.models.doctor_slot import DoctorSlot, SlotStatus

# The hospital runs on IST; schedules and patient-facing times are IST.
IST = timezone(timedelta(hours=5, minutes=30))


class SlotNotFoundError(Exception):
    """The slot id does not exist."""


class SlotUnavailableError(Exception):
    """The slot exists but is held or booked by someone else (or is in the past)."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalise a datetime to aware-UTC. Naive values are assumed to be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def as_ist(value: datetime) -> datetime:
    return as_utc(value).astimezone(IST)


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------

def _horizon_bounds() -> tuple[date, date]:
    today = utcnow().astimezone(IST).date()
    return today, today + timedelta(days=settings.BOOKING_HORIZON_DAYS - 1)


def ensure_slots(db: Session, doctor: Doctor, start_day: date, end_day: date) -> int:
    """Create any missing slots for ``doctor`` between two IST dates (inclusive).

    Slots are only generated inside the booking horizon (today .. today + N days),
    and only from the doctor's weekly schedule. Safe to call repeatedly and
    from several requests at once. Returns how many slots were created.
    """
    horizon_start, horizon_end = _horizon_bounds()
    start_day = max(start_day, horizon_start)
    end_day = min(end_day, horizon_end)
    if start_day > end_day or not doctor.schedules:
        return 0

    step = timedelta(minutes=doctor.slot_minutes)
    wanted: dict[datetime, datetime] = {}
    day = start_day
    while day <= end_day:
        for block in doctor.schedules:
            if block.weekday != day.weekday():
                continue
            cursor = datetime.combine(day, block.start_time, tzinfo=IST)
            block_end = datetime.combine(day, block.end_time, tzinfo=IST)
            while cursor + step <= block_end:
                wanted[as_utc(cursor)] = as_utc(cursor + step)
                cursor += step
        day += timedelta(days=1)
    if not wanted:
        return 0

    range_start = as_utc(datetime.combine(start_day, time.min, tzinfo=IST))
    range_end = as_utc(datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=IST))
    existing = {
        as_utc(row[0])
        for row in db.query(DoctorSlot.slot_start)
        .filter(
            DoctorSlot.doctor_id == doctor.doctor_id,
            DoctorSlot.slot_start >= range_start,
            DoctorSlot.slot_start < range_end,
        )
        .all()
    }
    missing = [(start, end) for start, end in sorted(wanted.items()) if start not in existing]
    if not missing:
        return 0

    for start, end in missing:
        db.add(
            DoctorSlot(
                slot_id=str(uuid.uuid4()),
                doctor_id=doctor.doctor_id,
                slot_start=start,
                slot_end=end,
                status=SlotStatus.available.value,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        # Another request generated the same slots first - that is fine.
        db.rollback()
        return 0
    return len(missing)


def list_free_slots(db: Session, doctor: Doctor, day: date) -> list[DoctorSlot]:
    """Free slots for one IST day, soonest first.

    A slot is free if it is available, or if its hold has expired. Slots in the
    past are never offered.
    """
    ensure_slots(db, doctor, day, day)
    now = utcnow()
    day_start = as_utc(datetime.combine(day, time.min, tzinfo=IST))
    day_end = as_utc(datetime.combine(day + timedelta(days=1), time.min, tzinfo=IST))
    return (
        db.query(DoctorSlot)
        .filter(
            DoctorSlot.doctor_id == doctor.doctor_id,
            DoctorSlot.slot_start >= max(day_start, now),
            DoctorSlot.slot_start < day_end,
            or_(
                DoctorSlot.status == SlotStatus.available.value,
                and_(
                    DoctorSlot.status == SlotStatus.held.value,
                    DoctorSlot.held_until <= now,
                ),
            ),
        )
        .order_by(DoctorSlot.slot_start)
        .all()
    )


def nearest_free_slots(
    db: Session, doctor: Doctor, day: date, around: time, limit: int = 3
) -> list[DoctorSlot]:
    """Up to ``limit`` free slots on ``day`` closest to the wanted time, in time order."""
    free = list_free_slots(db, doctor, day)
    wanted_minutes = around.hour * 60 + around.minute

    def distance(slot: DoctorSlot) -> int:
        local = as_ist(slot.slot_start)
        return abs(local.hour * 60 + local.minute - wanted_minutes)

    closest = sorted(free, key=distance)[:limit]
    return sorted(closest, key=lambda slot: as_utc(slot.slot_start))


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------

def hold_slot(db: Session, slot_id: str, session_id: str) -> DoctorSlot:
    """Hold a slot for ``session_id`` for SLOT_HOLD_SECONDS.

    Succeeds if the slot is available, its previous hold has expired, or it is
    already held by this same session (which just refreshes the hold). The
    check and the update are one statement, so only one session can win.
    """
    now = utcnow()
    held_until = now + timedelta(seconds=settings.SLOT_HOLD_SECONDS)
    result = db.execute(
        update(DoctorSlot)
        .where(
            DoctorSlot.slot_id == slot_id,
            DoctorSlot.slot_start > now,
            or_(
                DoctorSlot.status == SlotStatus.available.value,
                and_(
                    DoctorSlot.status == SlotStatus.held.value,
                    or_(
                        DoctorSlot.held_until <= now,
                        DoctorSlot.held_by_session == session_id,
                    ),
                ),
            ),
        )
        .values(
            status=SlotStatus.held.value,
            held_by_session=session_id,
            held_until=held_until,
        ).execution_options(synchronize_session=False)
    )
    db.commit()
    if result.rowcount != 1:
        if db.get(DoctorSlot, slot_id) is None:
            raise SlotNotFoundError(slot_id)
        raise SlotUnavailableError(slot_id)
    slot = db.get(DoctorSlot, slot_id)
    db.refresh(slot)
    return slot


def release_slot(db: Session, slot_id: str, session_id: str) -> bool:
    """Give back a slot this session is holding. Idempotent; returns True if freed."""
    result = db.execute(
        update(DoctorSlot)
        .where(
            DoctorSlot.slot_id == slot_id,
            DoctorSlot.status == SlotStatus.held.value,
            DoctorSlot.held_by_session == session_id,
        )
        .values(status=SlotStatus.available.value, held_by_session=None, held_until=None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def claim_held_slot_for_booking(
    db: Session, slot_id: str, session_id: str, appointment_id: str
) -> None:
    """Turn this session's held slot into a booked slot. Does NOT commit.

    The caller inserts the appointment and commits both together. Only the
    session that holds the slot can book it. If the hold has expired but nobody
    else has taken the slot since, the original session may still book it:
    taking it over would have changed ``held_by_session``, so this statement
    would match nothing.
    """
    result = db.execute(
        update(DoctorSlot)
        .where(
            DoctorSlot.slot_id == slot_id,
            DoctorSlot.status == SlotStatus.held.value,
            DoctorSlot.held_by_session == session_id,
        ).execution_options(synchronize_session=False)
        .values(
            status=SlotStatus.booked.value,
            held_by_session=None,
            held_until=None,
            appointment_id=appointment_id,
        )
    )
    if result.rowcount != 1:
        if db.get(DoctorSlot, slot_id) is None:
            raise SlotNotFoundError(slot_id)
        raise SlotUnavailableError(slot_id)


def free_booked_slot(db: Session, slot_id: str, appointment_id: str) -> None:
    """Put a cancelled appointment's slot back on offer. Does NOT commit."""
    db.execute(
        update(DoctorSlot)
        .where(
            DoctorSlot.slot_id == slot_id,
            DoctorSlot.appointment_id == appointment_id,
        )
        .values(
            status=SlotStatus.available.value,
            held_by_session=None,
            held_until=None,
            appointment_id=None,
        ).execution_options(synchronize_session=False)
    )


def claim_slot_for_booking(db: Session, slot_id: str, session_id: str, appointment_id: str) -> None:
    """Book a slot in one step, without a separate hold. Does NOT commit.

    Used when the caller books in one go (reschedule, follow-up). The slot may be
    free, may carry a lapsed hold, or may be held by this same session. One
    conditional UPDATE, so two callers can never both get it.
    """
    now = utcnow()
    result = db.execute(
        update(DoctorSlot)
        .where(
            DoctorSlot.slot_id == slot_id,
            DoctorSlot.slot_start > now,
            or_(
                DoctorSlot.status == SlotStatus.available.value,
                and_(
                    DoctorSlot.status == SlotStatus.held.value,
                    or_(
                        DoctorSlot.held_until <= now,
                        DoctorSlot.held_by_session == session_id,
                    ),
                ),
            ),
        )
        .values(
            status=SlotStatus.booked.value,
            held_by_session=None,
            held_until=None,
            appointment_id=appointment_id,
        ).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        if db.get(DoctorSlot, slot_id) is None:
            raise SlotNotFoundError(slot_id)
        raise SlotUnavailableError(slot_id)
