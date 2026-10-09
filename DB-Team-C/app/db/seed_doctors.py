"""Seed hospitals, doctors and weekly schedules (dev / pilot data).

The hospitals and doctors are the ones the web dashboard already shows
(10 hospitals, 25 doctors in Mysuru and 25 in Bengaluru), so a name the
patient sees on screen is a name the booking flow can find.

Run after applying the migrations:

    python -m app.db.seed_doctors

It is safe to run more than once: anything that already exists is left alone.

Doctor staff logins are optional. They are only created if you set
SEED_DOCTOR_PASSWORD (there is deliberately no default password). Each login
uses a dummy phone number that cannot belong to a real person
(+91 0000 0000NN), so no real WhatsApp numbers are involved:

    SEED_DOCTOR_PASSWORD='choose-a-password' python -m app.db.seed_doctors
"""

import os
import uuid
from datetime import time

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.authentication import Authentication
from app.models.doctor import Doctor, DoctorSchedule
from app.models.hospital import Hospital
from app.services.authentication_service import hash_password

HOSPITALS = [
    # (id, city, name, lat, lng, timings)
    ("hos-mys-01", "mysore", "Zenvy Care Hospital", 12.2958, 76.6394, "Open 24 hours"),
    ("hos-mys-02", "mysore", "Mysuru Multispeciality Hospital", 12.302, 76.650, "Open 8:00 AM - 10:00 PM"),
    ("hos-mys-03", "mysore", "Chamundi Medical Centre", 12.286, 76.635, "Open 24 hours"),
    ("hos-mys-04", "mysore", "Royal Mysore Hospital", 12.310, 76.620, "Open 7:00 AM - 11:00 PM"),
    ("hos-mys-05", "mysore", "Kaveri Specialty Hospital", 12.279, 76.646, "Open 9:00 AM - 9:00 PM"),
    ("hos-blr-01", "bangalore", "Zenvy Bengaluru Care", 12.9716, 77.5946, "Open 24 hours"),
    ("hos-blr-02", "bangalore", "Bengaluru Multispeciality Centre", 12.982, 77.603, "Open 8:00 AM - 10:00 PM"),
    ("hos-blr-03", "bangalore", "Metro Health Hospital", 12.961, 77.585, "Open 24 hours"),
    ("hos-blr-04", "bangalore", "Namma Care Medical Centre", 12.955, 77.610, "Open 7:00 AM - 11:00 PM"),
    ("hos-blr-05", "bangalore", "Silicon City Specialty Hospital", 12.985, 77.575, "Open 9:00 AM - 9:00 PM"),
]

DOCTOR_NAMES = [
    "Arjun Rao", "Priya Sharma", "Vikram Shetty", "Ananya Iyer", "Rahul Gowda",
    "Sneha Patil", "Karthik Reddy", "Meera Nair", "Aditya Kulkarni", "Divya Joshi",
    "Rohan Desai", "Kavya Hegde", "Nikhil Bhat", "Pooja Menon", "Sanjay Kumar",
    "Neha Rao", "Manoj Shetty", "Aishwarya Das", "Akshay Naik", "Shreya Rao",
    "Harish Gowda", "Nandini Iyer", "Vivek Patil", "Riya Sharma", "Suresh Reddy",
]

SPECIALTIES = [
    "Cardiologist", "Orthopedist", "Dermatologist", "Neurologist", "Pediatrician",
    "Gynecologist", "ENT Specialist", "General Physician", "Ophthalmologist", "Psychiatrist",
]

# Monday-Saturday, 09:00-13:00 and 14:00-17:00 IST.
WORKING_WEEKDAYS = range(0, 6)
WORKING_BLOCKS = [(time(9, 0), time(13, 0)), (time(14, 0), time(17, 0))]

SLOT_MINUTES = 30


def _doctor_rows():
    """Yield (doctor_id, hospital_id, name, specialty) in a fixed order."""
    for city_code, hospital_offset in (("mys", 0), ("blr", 5)):
        for index, name in enumerate(DOCTOR_NAMES):
            hospital_id = HOSPITALS[hospital_offset + (index % 5)][0]
            yield (
                f"doc-{city_code}-{index + 1:02d}",
                hospital_id,
                f"Dr. {name}",
                SPECIALTIES[index % len(SPECIALTIES)],
            )


def _dummy_phone(sequence: int) -> str:
    # Country code 91 followed by a leading 0 is not a valid Indian mobile
    # number, so this can never reach a real person.
    return f"+910000{sequence:06d}"


def seed(db: Session, doctor_password: str | None = None) -> dict:
    counts = {"hospitals": 0, "doctors": 0, "schedules": 0, "logins": 0}

    for hospital_id, city, name, lat, lng, timings in HOSPITALS:
        if db.get(Hospital, hospital_id) is None:
            db.add(
                Hospital(
                    hospital_id=hospital_id,
                    name=name,
                    city=city,
                    latitude=lat,
                    longitude=lng,
                    timings=timings,
                )
            )
            counts["hospitals"] += 1
    db.commit()

    for sequence, (doctor_id, hospital_id, name, specialty) in enumerate(_doctor_rows(), start=1):
        doctor = db.get(Doctor, doctor_id)
        if doctor is None:
            doctor = Doctor(
                doctor_id=doctor_id,
                hospital_id=hospital_id,
                name=name,
                specialty=specialty,
                slot_minutes=SLOT_MINUTES,
            )
            db.add(doctor)
            counts["doctors"] += 1

        if doctor_password and doctor.auth_id is None:
            phone = _dummy_phone(sequence)
            login = db.query(Authentication).filter(Authentication.phone_no == phone).first()
            if login is None:
                login = Authentication(
                    name=name,
                    phone_no=phone,
                    password_hash=hash_password(doctor_password),
                    role="staff",
                )
                db.add(login)
                db.flush()
                counts["logins"] += 1
            doctor.auth_id = login.auth_id

        has_schedule = db.query(DoctorSchedule).filter(DoctorSchedule.doctor_id == doctor_id).first()
        if has_schedule is None:
            for weekday in WORKING_WEEKDAYS:
                for start, end in WORKING_BLOCKS:
                    db.add(
                        DoctorSchedule(
                            schedule_id=str(uuid.uuid4()),
                            doctor_id=doctor_id,
                            weekday=weekday,
                            start_time=start,
                            end_time=end,
                        )
                    )
                    counts["schedules"] += 1
    db.commit()
    return counts


if __name__ == "__main__":
    password = os.getenv("SEED_DOCTOR_PASSWORD") or None
    session = SessionLocal()
    try:
        result = seed(session, password)
    finally:
        session.close()
    print(f"Seeded: {result}")
    if not password:
        print("No doctor logins created (SEED_DOCTOR_PASSWORD is not set).")
