"""
Tests for UserService.

Covers: top_up_balance (happy path, additive, user not found).
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.user import UserService


def _service(db) -> UserService:
    return UserService(db)


class TestTopUpBalance:
    @pytest.mark.asyncio
    async def test_top_up_increases_balance(self, db, user):
        original = user.balance
        svc = _service(db)
        updated = await svc.top_up_balance(user.id, Decimal("50.00"))
        assert updated.balance == original + Decimal("50.00")

    @pytest.mark.asyncio
    async def test_top_up_is_additive(self, db, user):
        original_balance = user.balance  # capture before any mutations
        svc = _service(db)
        await svc.top_up_balance(user.id, Decimal("10.00"))
        result = await svc.top_up_balance(user.id, Decimal("20.00"))
        assert result.balance == original_balance + Decimal("30.00")

    @pytest.mark.asyncio
    async def test_top_up_small_amount(self, db, user):
        svc = _service(db)
        original = user.balance
        updated = await svc.top_up_balance(user.id, Decimal("0.01"))
        assert updated.balance == original + Decimal("0.01")

    @pytest.mark.asyncio
    async def test_top_up_user_not_found(self, db):
        svc = _service(db)
        with pytest.raises(HTTPException) as exc:
            await svc.top_up_balance(uuid4(), Decimal("50.00"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_top_up_returns_updated_user(self, db, user):
        svc = _service(db)
        result = await svc.top_up_balance(user.id, Decimal("100.00"))
        assert result.id == user.id
        assert result.username == user.username
