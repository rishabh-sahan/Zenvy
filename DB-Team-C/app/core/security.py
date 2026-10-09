import base64
import hashlib
import hmac
import json
import time

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.deps import get_db
from app.models.authentication import Authentication

bearer_scheme = HTTPBearer(auto_error=False)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_access_token(authentication: Authentication) -> str:
    payload = {
        "sub": authentication.auth_id,
        "role": authentication.role,
        "exp": int(time.time()) + settings.AUTH_TOKEN_TTL_SECONDS,
    }
    encoded_payload = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(
        settings.AUTH_TOKEN_SECRET.encode(), encoded_payload.encode(), hashlib.sha256
    ).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def get_current_authentication(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Authentication:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    try:
        encoded_payload, encoded_signature = credentials.credentials.split(".", 1)
        expected_signature = hmac.new(
            settings.AUTH_TOKEN_SECRET.encode(), encoded_payload.encode(), hashlib.sha256
        ).digest()
        if not hmac.compare_digest(_decode(encoded_signature), expected_signature):
            raise ValueError
        payload = json.loads(_decode(encoded_payload))
        if int(payload["exp"]) <= int(time.time()):
            raise ValueError
        authentication = db.query(Authentication).filter(Authentication.auth_id == payload["sub"]).first()
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        authentication = None
    if authentication is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return authentication


def require_staff(
    authentication: Authentication = Depends(get_current_authentication),
) -> Authentication:
    if authentication.role != "staff":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return authentication

def get_optional_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Authentication | None:
    """The logged-in staff member if a valid staff token was sent, else None.

    A token that is present but invalid still gets a 401.
    """
    if credentials is None:
        return None
    authentication = get_current_authentication(credentials, db)
    return authentication if authentication.role == "staff" else None


def require_doctor(
    staff: Authentication = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """The Doctor record of the logged-in staff member (403 if they are not a doctor)."""
    from app.models.doctor import Doctor

    doctor = db.query(Doctor).filter(Doctor.auth_id == staff.auth_id, Doctor.is_active.is_(True)).first()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="A doctor account is required")
    return doctor
