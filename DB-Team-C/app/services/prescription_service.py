"""Prescriptions: draft -> doctor signs (locked) -> doses are scheduled -> the patient marks them taken.

* The AI may DRAFT a medicine list from the transcript, but a draft is never shown to the patient and
  schedules nothing. Only a prescription the doctor signed counts.
* A signed prescription is never edited. A change is a new version; signing it replaces the old one
  and cancels that version's doses that have not happened yet.
* Dose times are fixed clock times (IST) chosen from the frequency ("1-0-1" -> 08:00 and 21:00) and
  can be edited by the doctor. Nothing here gives medical advice: every number comes from the doctor.
"""

import re
import uuid
from collections import Counter
from datetime import datetime, time, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.ai_appointment import AIAppointment
from app.models.authentication import Authentication
from app.models.consultation import Consultation
from app.models.doctor import Doctor
from app.models.prescription import DoseStatus, MedicationDose, Prescription, PrescriptionItem, PrescriptionStatus
from app.models.reminder import Reminder, ReminderKind, ReminderStatus
from app.services import agent_service, reminder_service
from app.services.slot_service import IST, as_ist, as_utc, utcnow

MAX_ITEMS = 20
MAX_DAYS = 90
MAX_TIMES_PER_DAY = 6
TAKE_EARLY_MINUTES = 60          # a dose can be marked taken up to an hour before it is due
TAKE_LATE_HOURS = 12             # ... and up to half a day after
FOODS = ("before", "after", "with", "any")
CLOCK = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DOTS = re.compile(r"^\s*([01])\s*[-–]\s*([01])\s*[-–]\s*([01])(?:\s*[-–]\s*([01]))?\s*$")

# 1-0-1 means morning-afternoon-night; the four-part form is morning-noon-evening-night.
THREE_PART = ("08:00", "14:00", "21:00")
FOUR_PART = ("08:00", "12:00", "16:00", "21:00")
PER_DAY = {
    1: ["08:00"],
    2: ["08:00", "21:00"],
    3: ["08:00", "14:00", "21:00"],
    4: ["08:00", "12:00", "16:00", "20:00"],
}
# Most specific first: "thrice daily" contains "daily", which alone would mean once.
WORD_COUNTS = (
    (("qid", "four times", "4 times"), 4),
    (("thrice", "tds", "tid", "three times", "3 times"), 3),
    (("twice", "bd", "bid", "two times", "2 times"), 2),
    (("once", "od", "daily", "one time", "1 time", "every day"), 1),
)


class PrescriptionError(Exception):
    """Something the doctor must fix. The message is shown to them."""


class NothingToSign(PrescriptionError):
    pass


class DoseError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# cleaning what the doctor (or the AI) wrote
# ---------------------------------------------------------------------------

def times_for(frequency_text: str | None) -> list[str]:
    """Clock times for "1-0-1", "twice a day", "TDS"... or [] when it cannot be told."""
    text = (frequency_text or "").strip().lower()
    match = DOTS.match(text)
    if match:
        flags = [int(g) for g in match.groups() if g is not None]
        slots = THREE_PART if len(flags) == 3 else FOUR_PART
        return [clock for flag, clock in zip(flags, slots) if flag]
    for words, count in WORD_COUNTS:
        if any(re.search(rf"\b{re.escape(word)}\b", text) for word in words):
            return list(PER_DAY[count])
    return []


def _text(value, limit: int) -> str | None:
    value = " ".join(str(value).split()) if value not in (None, "") else ""
    return value[:limit] or None


def clean_item(raw: dict, position: int) -> dict:
    name = _text(raw.get("drug_name"), 120)
    if not name:
        raise PrescriptionError("Every medicine needs a name.")
    as_needed = bool(raw.get("as_needed"))

    duration = raw.get("duration_days")
    if duration in (None, ""):
        duration = None
    else:
        try:
            duration = int(duration)
        except (TypeError, ValueError) as exc:
            raise PrescriptionError(f"{name}: the number of days must be a number.") from exc
        if not 1 <= duration <= MAX_DAYS:
            raise PrescriptionError(f"{name}: the number of days must be between 1 and {MAX_DAYS}.")

    food = str(raw.get("food") or "any").strip().lower()
    food = food if food in FOODS else "any"

    frequency = _text(raw.get("frequency_text"), 60)
    times: list[str] = []
    for value in raw.get("dose_times") or []:
        value = str(value).strip()
        if not CLOCK.match(value):
            raise PrescriptionError(f"{name}: '{value}' is not a time like 08:00.")
        if value not in times:
            times.append(value)
    if not times and not as_needed:
        times = times_for(frequency)
    if len(times) > MAX_TIMES_PER_DAY:
        raise PrescriptionError(f"{name}: at most {MAX_TIMES_PER_DAY} doses a day.")

    return {
        "position": position,
        "drug_name": name,
        "strength": _text(raw.get("strength"), 40),
        "form": _text(raw.get("form"), 40),
        "dose_text": _text(raw.get("dose_text"), 60),
        "frequency_text": frequency,
        "dose_times": [] if as_needed else sorted(times),
        "food": food,
        "duration_days": duration,
        "as_needed": as_needed,
        "instructions": _text(raw.get("instructions"), 300),
        "from_transcript": bool(raw.get("from_transcript")),
    }


def item_dict(item: PrescriptionItem) -> dict:
    return {
        "item_id": item.item_id,
        "drug_name": item.drug_name,
        "strength": item.strength,
        "form": item.form,
        "dose_text": item.dose_text,
        "frequency_text": item.frequency_text,
        "dose_times": list(item.dose_times or []),
        "food": item.food,
        "duration_days": item.duration_days,
        "as_needed": item.as_needed,
        "instructions": item.instructions,
        "from_transcript": item.from_transcript,
    }


# ---------------------------------------------------------------------------
# versions
# ---------------------------------------------------------------------------

def latest(db: Session, consultation_id: str) -> Prescription | None:
    return (
        db.query(Prescription)
        .filter(Prescription.consultation_id == consultation_id)
        .order_by(Prescription.version.desc())
        .first()
    )


def latest_signed(db: Session, consultation_id: str) -> Prescription | None:
    return (
        db.query(Prescription)
        .filter(Prescription.consultation_id == consultation_id, Prescription.status == PrescriptionStatus.signed.value)
        .order_by(Prescription.version.desc())
        .first()
    )


def save_draft(
    db: Session, consultation: Consultation, items: list[dict], source: str, actor_auth_id: str | None
) -> Prescription:
    """Replace the draft with these medicines, or start a new version if the newest one is signed."""
    if source not in ("transcript", "doctor_edit", "carried_forward"):
        raise PrescriptionError("Unknown source.")
    if len(items) > MAX_ITEMS:
        raise PrescriptionError(f"At most {MAX_ITEMS} medicines.")
    cleaned = [clean_item(raw, position) for position, raw in enumerate(items)]

    current = latest(db, consultation.consultation_id)
    if current is not None and current.status == PrescriptionStatus.draft.value:
        prescription = current
        prescription.items.clear()
        db.flush()
        prescription.source = source
    else:
        prescription = Prescription(
            prescription_id=str(uuid.uuid4()),
            consultation_id=consultation.consultation_id,
            appointment_id=consultation.appointment_id,
            doctor_id=consultation.doctor_id,
            patient_auth_id=consultation.patient_auth_id,
            version=(current.version if current else 0) + 1,
            status=PrescriptionStatus.draft.value,
            source=source,
            created_by_auth_id=actor_auth_id,
        )
        db.add(prescription)
    for values in cleaned:
        prescription.items.append(PrescriptionItem(item_id=str(uuid.uuid4()), **values))
    db.commit()
    db.refresh(prescription)
    agent_service.record_action(
        db, "doctor", "save_prescription_draft", f"{len(cleaned)} medicine(s), source={source}",
        actor_auth_id=actor_auth_id, appointment_id=consultation.appointment_id,
    )
    return prescription


def sign(db: Session, consultation: Consultation, doctor_auth_id: str, now: datetime | None = None) -> Prescription:
    """Lock the draft. From here the coordinator schedules the doses."""
    now = now or utcnow()
    prescription = latest(db, consultation.consultation_id)
    if prescription is None or prescription.status != PrescriptionStatus.draft.value:
        raise NothingToSign("There is no draft prescription to sign.")
    if not prescription.items:
        raise NothingToSign("Add at least one medicine before signing.")
    for item in prescription.items:
        if not item.as_needed and not item.dose_times:
            raise PrescriptionError(
                f"{item.drug_name}: set the dose times (for example 08:00 and 21:00), or mark it 'only when needed'."
            )

    previous = latest_signed(db, consultation.consultation_id)
    if previous is not None:
        previous.status = PrescriptionStatus.superseded.value
    prescription.status = PrescriptionStatus.signed.value
    prescription.signed_by_auth_id = doctor_auth_id
    prescription.signed_at = now
    db.commit()
    db.refresh(prescription)

    agent_service.emit(
        db, "prescription_signed", appointment_id=prescription.appointment_id,
        payload={"prescription_id": prescription.prescription_id,
                 "superseded_id": previous.prescription_id if previous else None},
        dedupe_key=f"rx-signed:{prescription.prescription_id}",
    )
    agent_service.record_action(
        db, "doctor", "sign_prescription", f"version {prescription.version}, {len(prescription.items)} medicine(s)",
        actor_auth_id=doctor_auth_id, appointment_id=prescription.appointment_id,
    )
    return prescription


def carry_forward(db: Session, consultation: Consultation, actor_auth_id: str | None) -> Prescription:
    """At a follow-up visit: start a draft from the medicines signed at the visit before."""
    appointment = db.get(AIAppointment, consultation.appointment_id)
    parent_id = appointment.parent_appointment_id if appointment else None
    if not parent_id:
        raise PrescriptionError("This visit is not a follow-up, so there is nothing to carry forward.")
    parent = db.query(Consultation).filter(Consultation.appointment_id == parent_id).first()
    signed = latest_signed(db, parent.consultation_id) if parent else None
    if signed is None or parent.doctor_id != consultation.doctor_id:
        raise PrescriptionError("There is no signed prescription from the earlier visit to carry forward.")
    items = [{**item_dict(i), "from_transcript": False} for i in signed.items]
    return save_draft(db, consultation, items, "carried_forward", actor_auth_id)


# ---------------------------------------------------------------------------
# doses (created by the coordinator once a prescription is signed)
# ---------------------------------------------------------------------------

def _due(day, hhmm: str) -> datetime:
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    return as_utc(datetime.combine(day, time(hour, minute), tzinfo=IST))


def schedule_doses(db: Session, prescription: Prescription, now: datetime | None = None) -> int:
    """A dose and a reminder for every dose time of every day of every scheduled medicine. Idempotent."""
    now = now or utcnow()
    if prescription.status != PrescriptionStatus.signed.value:
        return 0
    start_day = as_ist(now).date()
    created = 0
    for item in prescription.items:
        if item.as_needed or not item.dose_times:
            continue
        have = {as_utc(row[0]) for row in db.query(MedicationDose.due_at).filter(MedicationDose.item_id == item.item_id)}
        days = min(item.duration_days or settings.MEDICATION_DEFAULT_DAYS, MAX_DAYS)
        snapshot = {key: item_dict(item)[key] for key in ("drug_name", "strength", "dose_text", "food", "instructions")}
        for offset in range(days):
            for hhmm in item.dose_times:
                due = _due(start_day + timedelta(days=offset), hhmm)
                if due <= now or due in have:
                    continue
                dose = MedicationDose(
                    dose_id=str(uuid.uuid4()),
                    prescription_id=prescription.prescription_id,
                    item_id=item.item_id,
                    patient_auth_id=prescription.patient_auth_id,
                    appointment_id=prescription.appointment_id,
                    due_at=due,
                    status=DoseStatus.scheduled.value,
                )
                db.add(dose)
                db.flush()
                reminder_service._ensure(
                    db, prescription.appointment_id, "patient", prescription.patient_auth_id,
                    ReminderKind.medication.value, due, snapshot, dose_id=dose.dose_id,
                )
                created += 1
    db.commit()
    return created


def cancel_doses(db: Session, prescription_id: str) -> int:
    """The prescription was replaced: doses that have not happened yet are cancelled, with their reminders."""
    dose_ids = [
        row[0]
        for row in db.query(MedicationDose.dose_id).filter(
            MedicationDose.prescription_id == prescription_id, MedicationDose.status == DoseStatus.scheduled.value
        )
    ]
    if not dose_ids:
        return 0
    db.execute(
        update(MedicationDose).where(MedicationDose.dose_id.in_(dose_ids)).values(status=DoseStatus.cancelled.value)
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Reminder)
        .where(Reminder.dose_id.in_(dose_ids), Reminder.status == ReminderStatus.pending.value)
        .values(status=ReminderStatus.cancelled.value)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return len(dose_ids)


def can_mark(dose: MedicationDose, now: datetime) -> bool:
    if dose.status not in (DoseStatus.scheduled.value, DoseStatus.missed.value):
        return False
    due = as_utc(dose.due_at)
    return now - timedelta(hours=TAKE_LATE_HOURS) <= due <= now + timedelta(minutes=TAKE_EARLY_MINUTES)


def _mark(db: Session, dose: MedicationDose, now: datetime) -> None:
    dose.status = DoseStatus.taken.value
    dose.taken_at = now
    db.execute(
        update(Reminder)
        .where(Reminder.dose_id == dose.dose_id, Reminder.status == ReminderStatus.pending.value)
        .values(status=ReminderStatus.cancelled.value)
        .execution_options(synchronize_session=False)
    )


def mark_taken(db: Session, dose_id: str, patient_auth_id: str, now: datetime | None = None) -> MedicationDose:
    now = now or utcnow()
    dose = db.get(MedicationDose, dose_id)
    if dose is None or dose.patient_auth_id != patient_auth_id:
        raise DoseError("not_found")
    if dose.status == DoseStatus.taken.value:
        return dose
    if dose.status == DoseStatus.cancelled.value:
        raise DoseError("cancelled")
    if not can_mark(dose, now):
        raise DoseError("too_early" if as_utc(dose.due_at) > now else "too_late")
    _mark(db, dose, now)
    db.commit()
    agent_service.record_action(db, "patient", "mark_dose_taken", "dose taken", actor_auth_id=patient_auth_id,
                                appointment_id=dose.appointment_id)
    return dose


def mark_taken_now(db: Session, patient_auth_id: str, now: datetime | None = None) -> list[MedicationDose]:
    """"I took my medicine": the doses of the latest dose time that has come (or is about to)."""
    now = now or utcnow()
    open_doses = (
        db.query(MedicationDose)
        .filter(
            MedicationDose.patient_auth_id == patient_auth_id,
            MedicationDose.status.in_([DoseStatus.scheduled.value, DoseStatus.missed.value]),
            MedicationDose.due_at <= now + timedelta(minutes=TAKE_EARLY_MINUTES),
            MedicationDose.due_at >= now - timedelta(hours=TAKE_LATE_HOURS),
        )
        .order_by(MedicationDose.due_at.desc())
        .all()
    )
    if not open_doses:
        return []
    newest = as_utc(open_doses[0].due_at)
    group = [d for d in open_doses if newest - as_utc(d.due_at) <= timedelta(minutes=20)]
    for dose in group:
        _mark(db, dose, now)
    db.commit()
    agent_service.record_action(db, "patient", "mark_dose_taken", f"{len(group)} dose(s) taken",
                                actor_auth_id=patient_auth_id, appointment_id=group[0].appointment_id)
    return group


def sweep_missed(db: Session, now: datetime | None = None) -> Counter:
    """Doses nobody marked taken in time become 'missed'. Returns {prescription_id: newly missed}."""
    now = now or utcnow()
    cutoff = now - timedelta(hours=settings.MISSED_AFTER_HOURS)
    rows = (
        db.query(MedicationDose.dose_id, MedicationDose.prescription_id)
        .filter(MedicationDose.status == DoseStatus.scheduled.value, MedicationDose.due_at < cutoff)
        .limit(2000)
        .all()
    )
    if not rows:
        return Counter()
    db.execute(
        update(MedicationDose)
        .where(MedicationDose.dose_id.in_([r[0] for r in rows]), MedicationDose.status == DoseStatus.scheduled.value)
        .values(status=DoseStatus.missed.value)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return Counter(r[1] for r in rows)


# ---------------------------------------------------------------------------
# what people see
# ---------------------------------------------------------------------------

def adherence(db: Session, prescription: Prescription) -> dict[str, dict[str, int]]:
    """{item_id: {taken, missed, scheduled, cancelled}} - shown to the doctor."""
    counts: dict[str, dict[str, int]] = {
        i.item_id: {"taken": 0, "missed": 0, "scheduled": 0, "cancelled": 0} for i in prescription.items
    }
    for item_id, status in db.query(MedicationDose.item_id, MedicationDose.status).filter(
        MedicationDose.prescription_id == prescription.prescription_id
    ):
        if item_id in counts:
            counts[item_id][status] += 1
    return counts


def prescription_dict(db: Session, prescription: Prescription | None, with_adherence: bool = False) -> dict | None:
    if prescription is None:
        return None
    result = {
        "prescription_id": prescription.prescription_id,
        "consultation_id": prescription.consultation_id,
        "version": prescription.version,
        "status": prescription.status,
        "source": prescription.source,
        "signed_at": prescription.signed_at,
        "items": [item_dict(i) for i in prescription.items],
    }
    if with_adherence and prescription.status == PrescriptionStatus.signed.value:
        counts = adherence(db, prescription)
        for item in result["items"]:
            item["adherence"] = counts.get(item["item_id"])
    return result


def medications_for_patient(db: Session, patient_auth_id: str, now: datetime | None = None) -> list[dict]:
    """The patient's signed prescriptions: each medicine, its next dose, and today's doses."""
    now = now or utcnow()
    today = as_ist(now).date()
    start = as_utc(datetime.combine(today, time.min, tzinfo=IST))
    end = start + timedelta(days=1)

    prescriptions = (
        db.query(Prescription)
        .filter(Prescription.patient_auth_id == patient_auth_id, Prescription.status == PrescriptionStatus.signed.value)
        .order_by(Prescription.signed_at.desc())
        .limit(10)
        .all()
    )
    result = []
    for prescription in prescriptions:
        doctor = db.get(Doctor, prescription.doctor_id)
        items = []
        for item in prescription.items:
            doses = (
                db.query(MedicationDose)
                .filter(MedicationDose.item_id == item.item_id, MedicationDose.status != DoseStatus.cancelled.value)
                .order_by(MedicationDose.due_at)
                .all()
            )
            upcoming = [d for d in doses if d.status == DoseStatus.scheduled.value and as_utc(d.due_at) >= now - timedelta(hours=1)]
            if not item.as_needed and not upcoming:
                continue  # the course is finished
            today_doses = [d for d in doses if start <= as_utc(d.due_at) < end]
            items.append({
                **item_dict(item),
                "next_dose_at": as_ist(upcoming[0].due_at) if upcoming else None,
                "remaining_doses": len([d for d in doses if d.status == DoseStatus.scheduled.value]),
                "today": [
                    {"dose_id": d.dose_id, "due_at": as_ist(d.due_at), "status": d.status, "can_mark": can_mark(d, now)}
                    for d in today_doses
                ],
            })
        if items:
            result.append({
                "prescription_id": prescription.prescription_id,
                "doctor_name": doctor.name if doctor else None,
                "signed_at": prescription.signed_at,
                "items": items,
            })
    return result
