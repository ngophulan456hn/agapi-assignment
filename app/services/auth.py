from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from jose import JWTError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import BLACKLIST_PREFIX, REFRESH_TOKEN_PREFIX
from app.core.otp import (
    OTP_TTL_SECONDS,
    detect_identifier_type,
    generate_otp,
    otp_redis_key,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from app.schemas.user import UserCreate


class AuthService:
    def __init__(self, db: AsyncSession, redis: Redis) -> None:
        self.db = db
        self.redis = redis

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #

    async def register(self, user_data: UserCreate) -> User:
        identifier_type = detect_identifier_type(user_data.identifier)

        # Username uniqueness
        result = await self.db.execute(
            select(User).where(User.username == user_data.username)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username already taken",
            )

        # Identifier uniqueness + build user
        if identifier_type == "email":
            result = await self.db.execute(
                select(User).where(User.email == user_data.identifier)
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered",
                )
            user = User(
                email=user_data.identifier,
                username=user_data.username,
                hashed_password=hash_password(user_data.password),
            )
        else:
            result = await self.db.execute(
                select(User).where(User.phone_number == user_data.identifier)
            )
            if result.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Phone number already registered",
                )
            user = User(
                phone_number=user_data.identifier,
                username=user_data.username,
                hashed_password=hash_password(user_data.password),
            )

        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        return user

    # ------------------------------------------------------------------ #
    # Login
    # ------------------------------------------------------------------ #

    async def login(self, identifier: str, password: str) -> dict:
        try:
            identifier_type = detect_identifier_type(identifier)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )

        if identifier_type == "email":
            result = await self.db.execute(select(User).where(User.email == identifier))
        else:
            result = await self.db.execute(
                select(User).where(User.phone_number == identifier)
            )
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect identifier or password",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

        access_token = create_access_token(str(user.id))
        refresh_token = create_refresh_token(str(user.id))

        await self.redis.setex(
            f"{REFRESH_TOKEN_PREFIX}{user.id}",
            timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            refresh_token,
        )

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
        }

    # ------------------------------------------------------------------ #
    # Logout / Refresh
    # ------------------------------------------------------------------ #

    async def logout(self, access_token: str, user_id: UUID) -> None:
        # Blacklist the access token for its remaining TTL
        try:
            payload = decode_token(access_token)
            exp = payload.get("exp")
            now = datetime.now(timezone.utc).timestamp()
            ttl = max(int(exp - now), 1)
            await self.redis.setex(f"{BLACKLIST_PREFIX}{access_token}", ttl, "1")
        except JWTError:
            pass

        # Remove stored refresh token
        await self.redis.delete(f"{REFRESH_TOKEN_PREFIX}{user_id}")

    async def refresh_tokens(self, refresh_token: str) -> dict:
        try:
            payload = decode_token(refresh_token)
            user_id: str = payload.get("sub")
            token_type: str = payload.get("type")
            if user_id is None or token_type != "refresh":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token",
                )
        except JWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token",
            )

        stored_token = await self.redis.get(f"{REFRESH_TOKEN_PREFIX}{user_id}")
        if stored_token != refresh_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Refresh token has been revoked",
            )

        result = await self.db.execute(select(User).where(User.id == UUID(user_id)))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        new_access_token = create_access_token(str(user.id))
        new_refresh_token = create_refresh_token(str(user.id))

        await self.redis.setex(
            f"{REFRESH_TOKEN_PREFIX}{user.id}",
            timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
            new_refresh_token,
        )

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    # ------------------------------------------------------------------ #
    # OTP
    # ------------------------------------------------------------------ #

    async def send_otp(self, identifier: str) -> str:
        """
        Generate a 6-digit OTP, store it in Redis (TTL: 5 min), and return it.
        In production: deliver via email (SMTP/SendGrid) or SMS (Twilio) instead
        of returning the value in the API response.
        """
        identifier_type = detect_identifier_type(identifier)

        if identifier_type == "email":
            result = await self.db.execute(select(User).where(User.email == identifier))
        else:
            result = await self.db.execute(
                select(User).where(User.phone_number == identifier)
            )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found for this identifier",
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account is inactive",
            )

        otp = generate_otp()
        key = otp_redis_key(identifier_type, identifier)
        await self.redis.setex(key, OTP_TTL_SECONDS, otp)

        # TODO (production): send `otp` via email or SMS — do NOT return it here
        return otp

    async def verify_otp(self, identifier: str, otp: str) -> User:
        """
        Validate OTP (single-use) and mark the user's email or phone as verified.
        """
        identifier_type = detect_identifier_type(identifier)
        key = otp_redis_key(identifier_type, identifier)

        stored_otp = await self.redis.get(key)
        if stored_otp is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP expired or not found. Please request a new one.",
            )
        if stored_otp != otp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP",
            )

        # Consume OTP immediately (single-use)
        await self.redis.delete(key)

        if identifier_type == "email":
            result = await self.db.execute(select(User).where(User.email == identifier))
        else:
            result = await self.db.execute(
                select(User).where(User.phone_number == identifier)
            )
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No account found for this identifier",
            )

        if identifier_type == "email":
            user.is_email_verified = True
        else:
            user.is_phone_verified = True

        await self.db.flush()
        await self.db.refresh(user)
        return user
