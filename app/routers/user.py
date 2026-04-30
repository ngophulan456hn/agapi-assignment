from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.user import User
from app.schemas.user import BalanceResponse, TopUpRequest, UserResponse
from app.services.user import UserService

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me/balance", response_model=BalanceResponse)
async def get_balance(
    current_user: User = Depends(get_current_active_user),
) -> BalanceResponse:
    """Return the current user's balance."""
    return BalanceResponse(balance=current_user.balance)


@router.post("/me/balance/top-up", response_model=UserResponse)
async def top_up_balance(
    body: TopUpRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Add funds to the current user's balance."""
    service = UserService(db)
    return await service.top_up_balance(user_id=current_user.id, amount=body.amount)
