from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models.authentication import Authentication
from app.schemas.authentication import (
    AuthenticationCreate,
    AuthenticationLogin,
    AuthenticationResponse,
    PhoneLoginRequest,
    PhoneLoginResponse,
    StaffLoginResponse,
)
from app.services.authentication_service import (
    StaffPhoneError,
    create_authentication,
    phone_login,
    verify_password,
)
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


@router.post("/phone-login", response_model=PhoneLoginResponse)
def phone_login_endpoint(payload: PhoneLoginRequest, db: Session = Depends(get_db)):
    """
    Phone-number-only login for the patient web app; registers the number on
    first use. No password is asked or checked (demo/pilot behaviour). The
    password-based /login, /register and /staff/login are unchanged.
    """
    try:
        account, is_new = phone_login(db, payload.phone_no)
    except StaffPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This number belongs to a staff account. Staff sign in with a password.",
        ) from exc

    if is_new:
        write_audit_log(
            db,
            action="register_user",
            actor="authentication-service",
            user_id=account.auth_id,
            after_value={"role": account.role, "method": "phone-login"},
        )
        # Best effort only: a WhatsApp problem must never block a patient's
        # first login (unlike the booking confirmation, which does fail loudly).
        try:
            send_welcome_notification(account.phone_no, "there")
        except RuntimeError as exc:
            print(f"[auth] Welcome WhatsApp notification failed for new account: {exc}")

    return PhoneLoginResponse(auth_id=account.auth_id, phone_no=account.phone_no, is_new=is_new)


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
