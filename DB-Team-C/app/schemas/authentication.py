from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
