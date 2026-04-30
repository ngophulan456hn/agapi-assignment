import re
import secrets
from typing import Literal

# RFC-5321 simplified email pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# E.164-like phone: optional leading +, then 7–15 digits
PHONE_REGEX = re.compile(r"^\+?[1-9]\d{6,14}$")

OTP_LENGTH = 6
OTP_TTL_SECONDS = 300  # 5 minutes
OTP_KEY_PREFIX = "otp:"


def generate_otp() -> str:
    """Cryptographically secure 6-digit numeric OTP."""
    return str(secrets.randbelow(10**OTP_LENGTH)).zfill(OTP_LENGTH)


def detect_identifier_type(identifier: str) -> Literal["email", "phone"]:
    """
    Return 'email' or 'phone' based on the identifier format.
    Raises ValueError if neither matches.
    """
    if EMAIL_REGEX.match(identifier):
        return "email"
    if PHONE_REGEX.match(identifier):
        return "phone"
    raise ValueError(
        "identifier must be a valid email address or phone number "
        "(E.164 format, e.g. +84901234567)"
    )


def otp_redis_key(identifier_type: str, identifier: str) -> str:
    return f"{OTP_KEY_PREFIX}{identifier_type}:{identifier}"
