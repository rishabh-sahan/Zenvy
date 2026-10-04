from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class Authentication(Base):
    __tablename__ = "authentication"

    auth_id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    name = Column(String, nullable=False, default="Zenvy user")
    phone_no = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="patient", server_default="patient", index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    appointments = relationship("AIAppointment", back_populates="authentication")
