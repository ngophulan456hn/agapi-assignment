from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_current_admin_user
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.flash_sale import (
    FlashSaleCreate,
    FlashSaleResponse,
    FlashSaleUpdate,
    PurchaseRequest,
    PurchaseResponse,
)
from app.services.flash_sale import FlashSaleService

router = APIRouter(prefix="/flash-sales", tags=["Flash Sales"])


# NOTE: /purchases must be declared before /{sale_id} so FastAPI routes it correctly.
@router.get("/purchases", response_model=List[PurchaseResponse])
async def get_my_purchases(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> List[PurchaseResponse]:
    """Return all purchases made by the current user."""
    service = FlashSaleService(db, redis)
    return await service.get_user_purchases(
        user_id=current_user.id, skip=skip, limit=limit
    )


@router.post("/", response_model=FlashSaleResponse, status_code=status.HTTP_201_CREATED)
async def create_flash_sale(
    data: FlashSaleCreate,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> FlashSaleResponse:
    service = FlashSaleService(db, redis)
    return await service.create_flash_sale(data, created_by=current_user.id)


@router.get("/", response_model=List[FlashSaleResponse])
async def list_flash_sales(
    active_only: bool = Query(False, description="Show only currently active sales"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> List[FlashSaleResponse]:
    service = FlashSaleService(db, redis)
    return await service.list_flash_sales(
        active_only=active_only, skip=skip, limit=limit
    )


@router.get("/{sale_id}", response_model=FlashSaleResponse)
async def get_flash_sale(
    sale_id: UUID,
    _: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> FlashSaleResponse:
    service = FlashSaleService(db, redis)
    return await service.get_flash_sale(sale_id)


@router.patch("/{sale_id}", response_model=FlashSaleResponse)
async def update_flash_sale(
    sale_id: UUID,
    data: FlashSaleUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> FlashSaleResponse:
    service = FlashSaleService(db, redis)
    return await service.update_flash_sale(sale_id, data)


@router.delete("/{sale_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flash_sale(
    sale_id: UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    service = FlashSaleService(db, redis)
    await service.delete_flash_sale(sale_id)


@router.post(
    "/{sale_id}/purchase",
    response_model=PurchaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def purchase_flash_sale(
    sale_id: UUID,
    purchase_data: PurchaseRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> PurchaseResponse:
    service = FlashSaleService(db, redis)
    return await service.purchase(
        sale_id=sale_id,
        user_id=current_user.id,
        purchase_data=purchase_data,
    )
