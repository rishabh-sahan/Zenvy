from sqlalchemy import Column, String, DateTime
from sqlalchemy.sql import func

from app.db.database import Base


# password_hash is NOT NULL, but the phone-only login has no password to hash.
# Rows created by that flow get this sentinel instead. It contains no "$", so
# werkzeug.security.check_password_hash returns False rather than matching
# anything -- these accounts fail closed if password login is ever enabled.
UNUSABLE_PASSWORD_HASH = "!phone-only-no-password"


class Authentication(Base):
    """
    Registered patient logins.

    Rows come from two places: externally created accounts that carry a real
    scrypt hash, and phone-only web logins, which self-register with
    UNUSABLE_PASSWORD_HASH.
    """

    __tablename__ = "authentication"

    auth_id = Column(String, primary_key=True, index=True)
    phone_no = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
