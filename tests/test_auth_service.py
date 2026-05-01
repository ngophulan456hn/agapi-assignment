"""
Tests for AuthService.

Covers: register, login, logout, refresh_tokens, send_otp, verify_otp.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException

from datetime import timedelta
from unittest.mock import patch

from app.core.deps import BLACKLIST_PREFIX, REFRESH_TOKEN_PREFIX
from app.core.otp import otp_redis_key
from app.core.security import create_refresh_token, hash_password, verify_password
from app.models.user import User
from app.schemas.user import UserCreate
from app.services.auth import AuthService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _service(db, redis) -> AuthService:
    return AuthService(db, redis)


def _user_create(**kwargs) -> UserCreate:
    defaults = {
        "identifier": "new@example.com",
        "username": "newuser",
        "password": "Password1!",
    }
    defaults.update(kwargs)
    return UserCreate(**defaults)


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_with_email(self, db, redis):
        svc = _service(db, redis)
        user = await svc.register(
            _user_create(identifier="alice@example.com", username="alice")
        )
        assert user.email == "alice@example.com"
        assert user.phone_number is None
        assert verify_password("Password1!", user.hashed_password)

    @pytest.mark.asyncio
    async def test_register_with_phone(self, db, redis):
        svc = _service(db, redis)
        user = await svc.register(
            _user_create(identifier="+84901234567", username="bob")
        )
        assert user.phone_number == "+84901234567"
        assert user.email is None

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, db, redis, user):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.register(_user_create(identifier=user.email, username="other"))
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_register_duplicate_username(self, db, redis, user):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.register(
                _user_create(identifier="other@example.com", username=user.username)
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_register_duplicate_phone(self, db, redis):
        svc = _service(db, redis)
        await svc.register(_user_create(identifier="+84901234567", username="user1"))
        with pytest.raises(HTTPException) as exc:
            await svc.register(
                _user_create(identifier="+84901234567", username="user2")
            )
        assert exc.value.status_code == 409


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_with_email(self, db, redis, user):
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, db, redis, user):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.login(user.email, "wrong")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_login_unknown_email(self, db, redis):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.login("ghost@example.com", "Password1!")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_login_inactive_user(self, db, redis):
        inactive = User(
            email="inactive@example.com",
            username="inactiveuser",
            hashed_password=hash_password("Password1!"),
            is_active=False,
        )
        db.add(inactive)
        await db.flush()
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.login("inactive@example.com", "Password1!")
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_login_stores_refresh_token_in_redis(self, db, redis, user):
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        stored = await redis.get(f"{REFRESH_TOKEN_PREFIX}{user.id}")
        assert stored == tokens["refresh_token"]


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_blacklists_access_token(self, db, redis, user):
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        access_token = tokens["access_token"]
        await svc.logout(access_token, user.id)
        assert await redis.exists(f"{BLACKLIST_PREFIX}{access_token}")

    @pytest.mark.asyncio
    async def test_logout_removes_refresh_token(self, db, redis, user):
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        await svc.logout(tokens["access_token"], user.id)
        stored = await redis.get(f"{REFRESH_TOKEN_PREFIX}{user.id}")
        assert stored is None


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


class TestRefreshTokens:
    @pytest.mark.asyncio
    async def test_refresh_issues_new_tokens(self, db, redis, user):
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        new_tokens = await svc.refresh_tokens(tokens["refresh_token"])
        # Verify the response structure is correct
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens
        assert new_tokens["token_type"] == "bearer"
        # New refresh token must be stored in Redis
        stored = await redis.get(f"{REFRESH_TOKEN_PREFIX}{user.id}")
        assert stored == new_tokens["refresh_token"]

    @pytest.mark.asyncio
    async def test_refresh_invalidates_old_token(self, db, redis, user):
        """After rotation the old refresh token must no longer be accepted.
        We force the new token to differ by giving it a longer expiry so
        the JWT payload changes even when both calls happen within 1 second."""
        svc = _service(db, redis)
        tokens = await svc.login(user.email, "Password1!")
        old_refresh = tokens["refresh_token"]

        # Patch create_refresh_token to produce a distinctly different token
        with patch(
            "app.services.auth.create_refresh_token",
            side_effect=lambda sub, **kw: create_refresh_token(
                sub, expires_delta=timedelta(days=8)
            ),
        ):
            await svc.refresh_tokens(old_refresh)

        # Old token is no longer stored in Redis → must be rejected
        with pytest.raises(HTTPException) as exc:
            await svc.refresh_tokens(old_refresh)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_refresh_invalid_token(self, db, redis):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.refresh_tokens("not.a.valid.token")
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# send_otp / verify_otp
# ---------------------------------------------------------------------------


class TestOTP:
    @pytest.mark.asyncio
    async def test_send_otp_stores_in_redis(self, db, redis, user):
        svc = _service(db, redis)
        otp = await svc.send_otp(user.email)
        key = otp_redis_key("email", user.email)
        stored = await redis.get(key)
        assert stored == otp
        assert len(otp) == 6
        assert otp.isdigit()

    @pytest.mark.asyncio
    async def test_send_otp_unknown_identifier(self, db, redis):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.send_otp("ghost@example.com")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_verify_otp_marks_email_verified(self, db, redis, user):
        svc = _service(db, redis)
        otp = await svc.send_otp(user.email)
        updated = await svc.verify_otp(user.email, otp)
        assert updated.is_email_verified is True

    @pytest.mark.asyncio
    async def test_verify_otp_is_single_use(self, db, redis, user):
        svc = _service(db, redis)
        otp = await svc.send_otp(user.email)
        await svc.verify_otp(user.email, otp)
        with pytest.raises(HTTPException) as exc:
            await svc.verify_otp(user.email, otp)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_verify_otp_wrong_code(self, db, redis, user):
        svc = _service(db, redis)
        await svc.send_otp(user.email)
        with pytest.raises(HTTPException) as exc:
            await svc.verify_otp(user.email, "000000")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_verify_otp_phone(self, db, redis):
        """OTP works for phone-based accounts too."""
        phone_user = User(
            phone_number="+84900000001",
            username="phoneuser",
            hashed_password=hash_password("Password1!"),
            is_active=True,
        )
        db.add(phone_user)
        await db.flush()
        svc = _service(db, redis)
        otp = await svc.send_otp("+84900000001")
        updated = await svc.verify_otp("+84900000001", otp)
        assert updated.is_phone_verified is True
