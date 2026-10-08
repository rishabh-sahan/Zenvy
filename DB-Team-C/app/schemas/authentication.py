import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthenticationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    phone_no: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)


class AuthenticationLogin(BaseModel):
    phone_no: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)


class AuthenticationResponse(BaseModel):
    auth_id: str
    name: str
    phone_no: str
    role: str
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class StaffLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def normalize_phone(raw: str) -> str:
    """
    Reduce anything a patient might type to the 10-digit form the phone-only
    login stores: '+91 98765 43210', '098765 43210' and '9876543210' all become
    '9876543210'.
    """
    digits = re.sub(r"\D", "", raw or "")

    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    return digits


class PhoneLoginRequest(BaseModel):
    phone_no: str = Field(..., min_length=1, max_length=20)

    @field_validator("phone_no")
    @classmethod
    def _ten_digits(cls, value: str) -> str:
        digits = normalize_phone(value)
        if len(digits) != 10:
            raise ValueError("Phone number must be 10 digits.")
        return digits


class PhoneLoginResponse(BaseModel):
    auth_id: str
    phone_no: str
    # True when this call registered the number, so the UI can greet a
    # first-time patient differently.
    is_new: bool = False
