import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from twilio.base.exceptions import TwilioRestException

from app.db.deps import get_db
from app.models.authentication import Authentication, UNUSABLE_PASSWORD_HASH
from app.schemas.authentication import LoginRequest, LoginResponse
from app.services.whatsapp_service import send_welcome_notification

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Phone-number-only login that registers the number on first use.

    A known number signs straight in; an unknown one gets a row created and is
    signed in as well, so patients never hit a dead end. No password is asked
    for or checked -- new rows get UNUSABLE_PASSWORD_HASH, which cannot verify
    against any password if password login is switched on later.
    """
    account = (
        db.query(Authentication)
        .filter(Authentication.phone_no == payload.phone_no)
        .first()
    )

    if account is not None:
        return LoginResponse(
            auth_id=account.auth_id,
            phone_no=account.phone_no,
            is_new=False,
        )

    account = Authentication(
        auth_id=str(uuid.uuid4()),
        phone_no=payload.phone_no,
        password_hash=UNUSABLE_PASSWORD_HASH,
    )
    db.add(account)

    try:
        db.commit()
    except IntegrityError:
        # Another request registered the same number in between the lookup and
        # the insert. The unique index on phone_no is what caught it, so fall
        # back to the row that won.
        db.rollback()
        account = (
            db.query(Authentication)
            .filter(Authentication.phone_no == payload.phone_no)
            .first()
        )
        if account is None:
            raise
        return LoginResponse(
            auth_id=account.auth_id,
            phone_no=account.phone_no,
            is_new=False,
        )

    db.refresh(account)

    # Best-effort only: a Twilio hiccup should never block a patient's first
    # login, so failures here are logged, not raised. (Contrast with the
    # appointment-booking notification, which does fail loudly -- a missed
    # appointment reminder is consequential enough that staff need to know.)
    try:
        send_welcome_notification(account.phone_no)
    except (RuntimeError, TwilioRestException) as exc:
        print(f"[auth] Welcome WhatsApp notification failed for new account: {exc}")

    return LoginResponse(
        auth_id=account.auth_id,
        phone_no=account.phone_no,
        is_new=True,
    )
