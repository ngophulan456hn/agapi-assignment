from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_active_user, oauth2_scheme
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.token import RefreshTokenRequest, Token
from app.schemas.user import (
    OTPSendRequest,
    OTPSendResponse,
    OTPVerifyRequest,
    UserCreate,
    UserResponse,
)
from app.services.auth import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserResponse:
    service = AuthService(db, redis)
    return await service.register(user_data)


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Token:
    """Login with email address or phone number (in the `username` field) and password."""
    service = AuthService(db, redis)
    return await service.login(
        identifier=form_data.username, password=form_data.password
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    service = AuthService(db, redis)
    await service.logout(access_token=token, user_id=current_user.id)


@router.post("/refresh", response_model=Token)
async def refresh_tokens(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Token:
    service = AuthService(db, redis)
    return await service.refresh_tokens(body.refresh_token)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_active_user)) -> UserResponse:
    return current_user


# ------------------------------------------------------------------ #
# OTP
# ------------------------------------------------------------------ #


@router.post("/otp/send", response_model=OTPSendResponse)
async def send_otp(
    body: OTPSendRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> OTPSendResponse:
    """
    Request an OTP for the given email address or phone number.

    - The account must already exist.
    - OTP is valid for **5 minutes** and is **single-use**.
    - **DEBUG mode only**: the OTP is returned in the response so you can test
      without a mail/SMS provider. Set `DEBUG=false` in production.
    """
    service = AuthService(db, redis)
    otp = await service.send_otp(body.identifier)
    return OTPSendResponse(
        message="OTP sent successfully",
        otp=otp if settings.DEBUG else None,
    )


@router.post("/otp/verify", response_model=UserResponse)
async def verify_otp(
    body: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserResponse:
    """
    Verify the OTP for an email address or phone number.
    On success, marks `is_email_verified` or `is_phone_verified` as `true`.
    """
    service = AuthService(db, redis)
    return await service.verify_otp(body.identifier, body.otp)
