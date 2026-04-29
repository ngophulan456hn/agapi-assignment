from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flash_sale import FlashSale, FlashSalePurchase
from app.schemas.flash_sale import FlashSaleCreate, FlashSaleUpdate, PurchaseRequest

_STOCK_KEY = "flash_sale:{sale_id}:stock"
_RATE_LIMIT_KEY = "rate_limit:purchase:{sale_id}:user:{user_id}"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_PURCHASES = 5


class FlashSaleService:
    def __init__(self, db: AsyncSession, redis: Redis) -> None:
        self.db = db
        self.redis = redis

    def _stock_key(self, sale_id: str) -> str:
        return _STOCK_KEY.format(sale_id=sale_id)

    # ------------------------------------------------------------------ #
    #  Admin operations
    # ------------------------------------------------------------------ #

    async def create_flash_sale(
        self, data: FlashSaleCreate, created_by: UUID
    ) -> FlashSale:
        flash_sale = FlashSale(
            name=data.name,
            description=data.description,
            original_price=data.original_price,
            sale_price=data.sale_price,
            total_stock=data.total_stock,
            remaining_stock=data.total_stock,
            start_time=data.start_time,
            end_time=data.end_time,
            created_by=created_by,
        )
        self.db.add(flash_sale)
        await self.db.flush()
        await self.db.refresh(flash_sale)

        # Seed Redis stock counter
        await self.redis.set(self._stock_key(str(flash_sale.id)), data.total_stock)
        return flash_sale

    async def update_flash_sale(
        self, sale_id: UUID, data: FlashSaleUpdate
    ) -> FlashSale:
        flash_sale = await self._get_or_404(sale_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(flash_sale, field, value)
        await self.db.flush()
        await self.db.refresh(flash_sale)
        return flash_sale

    async def delete_flash_sale(self, sale_id: UUID) -> None:
        flash_sale = await self._get_or_404(sale_id)
        await self.db.delete(flash_sale)
        await self.redis.delete(self._stock_key(str(sale_id)))

    # ------------------------------------------------------------------ #
    #  Read operations
    # ------------------------------------------------------------------ #

    async def list_flash_sales(
        self,
        active_only: bool = False,
        skip: int = 0,
        limit: int = 20,
    ) -> List[FlashSale]:
        query = select(FlashSale)
        if active_only:
            now = datetime.now(timezone.utc)
            query = query.where(
                FlashSale.is_active == True,  # noqa: E712
                FlashSale.start_time <= now,
                FlashSale.end_time >= now,
            )
        query = query.offset(skip).limit(limit).order_by(FlashSale.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_flash_sale(self, sale_id: UUID) -> FlashSale:
        flash_sale = await self._get_or_404(sale_id)
        # Sync remaining_stock from Redis (source of truth during an active sale)
        redis_stock = await self.redis.get(self._stock_key(str(sale_id)))
        if redis_stock is not None:
            flash_sale.remaining_stock = int(redis_stock)
        return flash_sale

    # ------------------------------------------------------------------ #
    #  Purchase
    # ------------------------------------------------------------------ #

    async def purchase(
        self,
        sale_id: UUID,
        user_id: UUID,
        purchase_data: PurchaseRequest,
    ) -> FlashSalePurchase:
        flash_sale = await self._get_or_404(sale_id)

        # Validate sale window
        now = datetime.now(timezone.utc)
        if not flash_sale.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Flash sale is not active",
            )
        if now < flash_sale.start_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Flash sale has not started yet",
            )
        if now > flash_sale.end_time:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Flash sale has ended",
            )

        # Per-user rate limiting
        rate_key = _RATE_LIMIT_KEY.format(sale_id=str(sale_id), user_id=str(user_id))
        count = await self.redis.incr(rate_key)
        if count == 1:
            await self.redis.expire(rate_key, RATE_LIMIT_WINDOW_SECONDS)
        if count > RATE_LIMIT_MAX_PURCHASES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded. "
                    f"Max {RATE_LIMIT_MAX_PURCHASES} purchases per minute."
                ),
            )

        # Atomically decrement stock in Redis
        stock_key = self._stock_key(str(sale_id))
        remaining = await self.redis.decrby(stock_key, purchase_data.quantity)
        if remaining < 0:
            # Rollback the decrement — no stock available
            await self.redis.incrby(stock_key, purchase_data.quantity)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Insufficient stock",
            )

        total_price = flash_sale.sale_price * purchase_data.quantity
        purchase = FlashSalePurchase(
            user_id=user_id,
            flash_sale_id=sale_id,
            quantity=purchase_data.quantity,
            unit_price=flash_sale.sale_price,
            total_price=total_price,
        )
        self.db.add(purchase)

        # Sync remaining_stock back to the DB row
        flash_sale.remaining_stock = remaining
        await self.db.flush()
        await self.db.refresh(purchase)
        return purchase

    async def get_user_purchases(
        self, user_id: UUID, skip: int = 0, limit: int = 20
    ) -> List[FlashSalePurchase]:
        result = await self.db.execute(
            select(FlashSalePurchase)
            .where(FlashSalePurchase.user_id == user_id)
            .offset(skip)
            .limit(limit)
            .order_by(FlashSalePurchase.purchased_at.desc())
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    async def _get_or_404(self, sale_id: UUID) -> FlashSale:
        result = await self.db.execute(select(FlashSale).where(FlashSale.id == sale_id))
        flash_sale = result.scalar_one_or_none()
        if not flash_sale:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Flash sale not found",
            )
        return flash_sale
