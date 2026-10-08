"""Doctor lookup used by the booking flow.

The patient may say a doctor's name ("Dr. Arjun Rao") or a department
("heart doctor" -> the NLU returns "Cardiology"). Both are resolved here.
"""

import re
from difflib import SequenceMatcher

from sqlalchemy.orm import Session, joinedload

from app.models.doctor import Doctor
from app.models.hospital import Hospital

# Each group holds different spellings of one specialty. A query matches a
# doctor when the query and the doctor's specialty are in the same group.
_SPECIALTY_GROUPS: list[set[str]] = [
    {"cardiologist", "cardiology", "cardiac", "heart"},
    {"orthopedist", "orthopedics", "orthopaedics", "orthopedic", "orthopaedic", "ortho", "bone"},
    {"dermatologist", "dermatology", "skin"},
    {"neurologist", "neurology", "neuro", "brain"},
    {"pediatrician", "pediatrics", "paediatrics", "paediatrician", "child", "children"},
    {"gynecologist", "gynecology", "gynaecology", "gynaecologist"},
    {"ent specialist", "ent"},
    {"general physician", "general medicine", "physician", "general"},
    {"ophthalmologist", "ophthalmology", "eye"},
    {"psychiatrist", "psychiatry"},
    {"dentist", "dentistry", "dental"},
]

_FUZZY_THRESHOLD = 0.85


def normalize(text: str) -> str:
    """Lower-case, drop the 'Dr' title and punctuation, collapse spaces."""
    text = text.lower()
    text = re.sub(r"\bdr\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return " ".join(text.split())


def _specialty_group(text: str) -> set[str] | None:
    key = normalize(text)
    for group in _SPECIALTY_GROUPS:
        if key in group:
            return group
    return None


def _base_query(db: Session):
    return (
        db.query(Doctor)
        .options(joinedload(Doctor.hospital), joinedload(Doctor.schedules))
        .join(Hospital, Doctor.hospital_id == Hospital.hospital_id)
        .filter(Doctor.is_active.is_(True))
    )


def get_doctor(db: Session, doctor_id: str) -> Doctor | None:
    return (
        db.query(Doctor)
        .options(joinedload(Doctor.hospital), joinedload(Doctor.schedules))
        .filter(Doctor.doctor_id == doctor_id, Doctor.is_active.is_(True))
        .first()
    )


def search_doctors(
    db: Session,
    query: str | None = None,
    hospital_id: str | None = None,
    city: str | None = None,
) -> list[Doctor]:
    """Find active doctors by name or specialty, optionally narrowed by hospital/city.

    Order of matching: exact name, then name containing every word of the
    query, then specialty (including aliases), then a close-spelling match on
    the name (speech-to-text often misspells names).
    """
    doctors = _base_query(db)
    if hospital_id:
        doctors = doctors.filter(Doctor.hospital_id == hospital_id)
    if city:
        doctors = doctors.filter(Hospital.city.ilike(city))
    candidates = doctors.order_by(Doctor.name, Hospital.name).all()
    # joinedload on a collection can return duplicates in old SQLAlchemy; keep order, drop repeats.
    seen: set[str] = set()
    candidates = [d for d in candidates if not (d.doctor_id in seen or seen.add(d.doctor_id))]

    if not query or not query.strip():
        return candidates

    wanted = normalize(query)
    if not wanted:
        return candidates

    exact = [d for d in candidates if normalize(d.name) == wanted]
    if exact:
        return exact

    words = wanted.split()
    by_words = [d for d in candidates if all(w in normalize(d.name).split() for w in words)]
    if by_words:
        return by_words

    group = _specialty_group(wanted)
    if group is not None:
        by_specialty = [d for d in candidates if normalize(d.specialty) in group]
        if by_specialty:
            return by_specialty

    return [
        d
        for d in candidates
        if SequenceMatcher(None, wanted, normalize(d.name)).ratio() >= _FUZZY_THRESHOLD
    ]
