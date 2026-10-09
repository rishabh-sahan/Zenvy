import enum

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.database import Base


class SlotStatus(str, enum.Enum):
    available = "available"
    held = "held"
    booked = "booked"


class DoctorSlot(Base):
    """One bookable time slot of one doctor.

    A slot moves available -> held (a patient is confirming) -> booked.
    All moves are made with a single conditional UPDATE in slot_service, so two
    patients can never both get the same slot.
    """

    __tablename__ = "doctor_slots"

    slot_id = Column(String, primary_key=True, index=True)
    doctor_id = Column(String, ForeignKey("doctors.doctor_id"), nullable=False, index=True)
    slot_start = Column(DateTime(timezone=True), nullable=False, index=True)
    slot_end = Column(DateTime(timezone=True), nullable=False)
    # Stored as plain strings (values of SlotStatus) so the conditional UPDATEs
    # in slot_service behave the same on PostgreSQL and SQLite.
    status = Column(String, nullable=False, default=SlotStatus.available.value, index=True)
    held_by_session = Column(String, nullable=True)
    held_until = Column(DateTime(timezone=True), nullable=True)
    appointment_id = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("doctor_id", "slot_start", name="uq_doctor_slots_doctor_start"),
    )

    doctor = relationship("Doctor", back_populates="slots")
