import base64
import hashlib
import hmac
import os

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
