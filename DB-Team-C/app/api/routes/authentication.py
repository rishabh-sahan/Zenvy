from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.authentication import Authentication
from app.schemas.authentication import (
    AuthenticationCreate,
    AuthenticationLogin,
    AuthenticationResponse,
    StaffLoginResponse,
)
from app.services.authentication_service import create_authentication, verify_password
from app.services.whatsapp_service import send_welcome_notification
from app.core.config import settings
from app.core.security import create_access_token
from app.services.audit_service import write_audit_log

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/register", response_model=AuthenticationResponse, status_code=status.HTTP_201_CREATED)
def register(auth_in: AuthenticationCreate, db: Session = Depends(get_db)):
    if db.query(Authentication).filter(Authentication.phone_no == auth_in.phone_no).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number already registered")
    authentication = create_authentication(db, auth_in)
    write_audit_log(
        db,
        action="register_user",
        actor="authentication-service",
        user_id=authentication.auth_id,
        after_value={
            "name": authentication.name,
            "phone_no": authentication.phone_no,
            "role": authentication.role,
        },
    )
    try:
        send_welcome_notification(authentication.phone_no, authentication.name)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Registration saved, but welcome WhatsApp notification failed",
        ) from exc
    return authentication


@router.post("/login", response_model=AuthenticationResponse)
def login(auth_in: AuthenticationLogin, db: Session = Depends(get_db)):
    authentication = db.query(Authentication).filter(Authentication.phone_no == auth_in.phone_no).first()
    if authentication is None or not verify_password(auth_in.password, authentication.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid phone number or password")
    return authentication


@router.post("/staff/login", response_model=StaffLoginResponse)
def staff_login(auth_in: AuthenticationLogin, db: Session = Depends(get_db)):
    authentication = db.query(Authentication).filter(Authentication.phone_no == auth_in.phone_no).first()
    if (
        authentication is None
        or authentication.role != "staff"
        or not verify_password(auth_in.password, authentication.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid staff credentials")
    return StaffLoginResponse(
        access_token=create_access_token(authentication),
        expires_in=settings.AUTH_TOKEN_TTL_SECONDS,
    )
