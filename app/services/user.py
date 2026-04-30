from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def top_up_balance(self, user_id: UUID, amount: Decimal) -> User:
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        user.balance = (user.balance or Decimal("0.00")) + amount
        await self.db.flush()
        await self.db.refresh(user)
        return user
