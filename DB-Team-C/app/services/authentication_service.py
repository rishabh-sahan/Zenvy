import base64
import hashlib
import hmac
import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.authentication import Authentication
from app.schemas.authentication import AuthenticationCreate


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1
    )
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(password_hash).decode()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, encoded_salt, encoded_hash = stored_hash.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode())
        expected_hash = base64.urlsafe_b64decode(encoded_hash.encode())
        actual_hash = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual_hash, expected_hash)


def create_authentication(db: Session, auth_in: AuthenticationCreate) -> Authentication:
    authentication = Authentication(
        name=auth_in.name.strip(),
        phone_no=auth_in.phone_no,
        password_hash=hash_password(auth_in.password),
    )
    db.add(authentication)
    db.commit()
    db.refresh(authentication)
    return authentication


# password_hash is NOT NULL, but the phone-only login has no password. Accounts
# it creates get this sentinel, which has no "$" so verify_password() always
# returns False: they can never be used with the password login or staff login.
UNUSABLE_PASSWORD_HASH = "!phone-only-no-password"


class StaffPhoneError(Exception):
    """The phone number belongs to a staff account, not a patient."""


def phone_login(db: Session, phone_no: str) -> tuple[Authentication, bool]:
    """
    Phone-number-only patient login that registers the number on first use.

    Returns (account, is_new). A known number signs straight in; an unknown one
    gets an account created, so patients never hit a dead end. Staff numbers are
    refused: staff sign in with a password at /auth/staff/login.
    """
    account = db.query(Authentication).filter(Authentication.phone_no == phone_no).first()

    if account is not None:
        if account.role != "patient":
            raise StaffPhoneError(phone_no)
        return account, False

    account = Authentication(phone_no=phone_no, password_hash=UNUSABLE_PASSWORD_HASH)
    db.add(account)
    try:
        db.commit()
    except IntegrityError:
        # Another request registered the same number between our lookup and
        # insert; the unique index caught it, so use the row that won.
        db.rollback()
        account = db.query(Authentication).filter(Authentication.phone_no == phone_no).first()
        if account is None:
            raise
        if account.role != "patient":
            raise StaffPhoneError(phone_no)
        return account, False

    db.refresh(account)
    return account, True
