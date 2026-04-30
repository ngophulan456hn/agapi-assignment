import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator

from app.core.otp import detect_identifier_type


def _validate_identifier(v: str) -> str:
    try:
        detect_identifier_type(v)
    except ValueError as e:
        raise ValueError(str(e))
    return v


class UserCreate(BaseModel):
    identifier: str  # email address or phone number (E.164, e.g. +84901234567)
    username: str
    password: str

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, v: str) -> str:
        return _validate_identifier(v)

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "Username must be alphanumeric (underscores and hyphens allowed)"
            )
        if len(v) < 3 or len(v) > 50:
            raise ValueError("Username must be between 3 and 50 characters")
        return v

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UserResponse(BaseModel):
    id: uuid.UUID
    email: Optional[str]
    phone_number: Optional[str]
    username: str
    is_active: bool
    is_admin: bool
    is_email_verified: bool
    is_phone_verified: bool
    balance: Decimal
    created_at: datetime

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    username: Optional[str] = None
    is_active: Optional[bool] = None


# ------------------------------------------------------------------ #
# OTP schemas
# ------------------------------------------------------------------ #


class OTPSendRequest(BaseModel):
    identifier: str  # email or phone number

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, v: str) -> str:
        return _validate_identifier(v)


class OTPSendResponse(BaseModel):
    message: str
    # Populated only when DEBUG=True — in production deliver via email/SMS
    otp: Optional[str] = None


class OTPVerifyRequest(BaseModel):
    identifier: str  # email or phone number
    otp: str

    @field_validator("identifier")
    @classmethod
    def validate_identifier(cls, v: str) -> str:
        return _validate_identifier(v)


# ------------------------------------------------------------------ #
# Balance schemas
# ------------------------------------------------------------------ #


class TopUpRequest(BaseModel):
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def amount_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Top-up amount must be greater than zero")
        return v


class BalanceResponse(BaseModel):
    balance: Decimal

    model_config = {"from_attributes": True}
