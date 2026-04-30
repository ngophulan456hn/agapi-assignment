from datetime import datetime, timezone
from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flash_sale import FlashSale, FlashSalePurchase
from app.models.product import Product
from app.models.user import User
from app.schemas.flash_sale import FlashSaleCreate, FlashSaleUpdate, PurchaseRequest
from app.tasks.flash_sale_tasks import sync_product_stock_after_sale

_STOCK_KEY = "flash_sale:{sale_id}:stock"
_RATE_LIMIT_KEY = "rate_limit:purchase:{sale_id}:user:{user_id}"
_DAILY_PURCHASE_KEY = "daily_purchase:{sale_id}:user:{user_id}:{date}"
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_PURCHASES = 5


class FlashSaleService:
    def __init__(self, db: AsyncSession, redis: Redis) -> None:
        self.db = db
        self.redis = redis

    def _stock_key(self, sale_id: str) -> str:
        return _STOCK_KEY.format(sale_id=sale_id)

    def _daily_purchase_key(self, sale_id: str, user_id: str, date_str: str) -> str:
        return _DAILY_PURCHASE_KEY.format(
            sale_id=sale_id, user_id=user_id, date=date_str
        )

    def _seconds_until_midnight_utc(self, now: datetime) -> int:
        """Seconds remaining until 00:00 UTC — used as TTL for the daily key."""
        from datetime import timedelta

        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return max(int((tomorrow - now).total_seconds()), 1)

    # ------------------------------------------------------------------ #
    #  Admin operations
    # ------------------------------------------------------------------ #

    async def create_flash_sale(
        self, data: FlashSaleCreate, created_by: UUID
    ) -> FlashSale:
        # Validate product exists and has enough stock
        product_result = await self.db.execute(
            select(Product).where(Product.id == data.product_id)
        )
        product = product_result.scalar_one_or_none()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )
        if data.total_stock > product.stock:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Flash sale stock ({data.total_stock}) cannot exceed "
                    f"product stock ({product.stock})"
                ),
            )

        # Prevent overlapping flash sales for the same product.
        # Two ranges overlap when: existing.start_time < new.end_time AND existing.end_time > new.start_time
        overlap_result = await self.db.execute(
            select(FlashSale).where(
                FlashSale.product_id == data.product_id,
                FlashSale.start_time < data.end_time,
                FlashSale.end_time > data.start_time,
            )
        )
        if overlap_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A flash sale for this product already exists in the requested time window",
            )

        flash_sale = FlashSale(
            product_id=data.product_id,
            name=data.name,
            description=data.description,
            original_price=product.price,
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

        # Schedule stock-sync task to fire at sale end_time
        sync_product_stock_after_sale.apply_async(
            args=[str(flash_sale.id)],
            eta=flash_sale.end_time,
        )

        return flash_sale

    async def update_flash_sale(
        self, sale_id: UUID, data: FlashSaleUpdate
    ) -> FlashSale:
        flash_sale = await self._get_or_404(sale_id)
        end_time_changed = (
            data.end_time is not None and data.end_time != flash_sale.end_time
        )
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(flash_sale, field, value)
        await self.db.flush()
        await self.db.refresh(flash_sale)

        # If end_time was moved, schedule a new sync task at the updated end time.
        # (The previously scheduled task will become a no-op if it fires and finds
        # the sale already inactive / stock already synced.)
        if end_time_changed:
            sync_product_stock_after_sale.apply_async(
                args=[str(flash_sale.id)],
                eta=flash_sale.end_time,
            )

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

        # One purchase per user per flash sale per day (UTC date)
        date_str = now.strftime("%Y-%m-%d")
        daily_key = self._daily_purchase_key(str(sale_id), str(user_id), date_str)
        already_purchased = await self.redis.exists(daily_key)
        if already_purchased:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You have already purchased this flash sale today",
            )

        # Per-user rate limiting (burst protection)
        rate_key = _RATE_LIMIT_KEY.format(sale_id=str(sale_id), user_id=str(user_id))
        count = await self.redis.incr(rate_key)
        if count == 1:
            await self.redis.expire(rate_key, RATE_LIMIT_WINDOW_SECONDS)
        if count > RATE_LIMIT_MAX_PURCHASES:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded. "
                    f"Max {RATE_LIMIT_MAX_PURCHASES} requests per minute."
                ),
            )

        # Load user and check balance
        user_result = await self.db.execute(select(User).where(User.id == user_id))
        user = user_result.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        total_price = flash_sale.sale_price * purchase_data.quantity
        if user.balance < total_price:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Insufficient balance. "
                    f"Required: {total_price}, available: {user.balance}"
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

        # Deduct balance
        user.balance = user.balance - total_price

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

        # Mark daily purchase in Redis (expires at midnight UTC)
        ttl = self._seconds_until_midnight_utc(now)
        await self.redis.setex(daily_key, ttl, "1")

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
