from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class Doctor(Base):
    __tablename__ = "doctors"

    doctor_id = Column(String, primary_key=True, index=True)
    hospital_id = Column(String, ForeignKey("hospitals.hospital_id"), nullable=False, index=True)
    # Staff login for this doctor (authentication.role == "staff"). Optional so a
    # doctor can exist before they have a login.
    auth_id = Column(String, ForeignKey("authentication.auth_id"), nullable=True, unique=True)
    name = Column(String, nullable=False, index=True)
    specialty = Column(String, nullable=False, index=True)
    slot_minutes = Column(Integer, nullable=False, default=30)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    hospital = relationship("Hospital", back_populates="doctors")
    schedules = relationship("DoctorSchedule", back_populates="doctor", cascade="all, delete-orphan")
    slots = relationship("DoctorSlot", back_populates="doctor")


class DoctorSchedule(Base):
    """One working block on a weekday, in hospital local time (IST)."""

    __tablename__ = "doctor_schedules"

    schedule_id = Column(String, primary_key=True)
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=False, index=True)
    weekday = Column(Integer, nullable=False)  # 0 = Monday ... 6 = Sunday
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    __table_args__ = (
        UniqueConstraint("doctor_id", "weekday", "start_time", name="uq_doctor_schedules_block"),
    )

    doctor = relationship("Doctor", back_populates="schedules")
