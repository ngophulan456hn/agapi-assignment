"""
flash_sale_tasks.py
-------------------
Celery tasks related to flash sale lifecycle.

sync_product_stock_after_sale
  - Reads the final remaining stock from Redis (source of truth during the sale).
  - Adds that remaining count back onto product.stock so unsold units are restored.
  - Marks the flash sale as inactive (is_active = False) and persists
    remaining_stock on the flash_sale row.
  - Cleans up the Redis stock key.

The task is scheduled with eta=flash_sale.end_time when a flash sale is created
or when end_time is updated.
"""

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.celery_app import celery_app
from app.core.config import settings

logger = logging.getLogger(__name__)

_STOCK_KEY = "flash_sale:{sale_id}:stock"


def _make_session() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


@celery_app.task(
    name="flash_sale.sync_product_stock_after_sale",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def sync_product_stock_after_sale(self, flash_sale_id: str) -> dict:
    """
    Synchronise unsold flash-sale stock back to the product table.

    This task runs synchronously inside Celery (standard workers), but
    uses asyncio internally to reuse the existing SQLAlchemy async engine.
    """
    import asyncio

    try:
        result = asyncio.run(_sync_stock(flash_sale_id))
        return result
    except Exception as exc:
        logger.exception(
            "sync_product_stock_after_sale failed for sale %s: %s",
            flash_sale_id,
            exc,
        )
        raise self.retry(exc=exc)


async def _sync_stock(flash_sale_id: str) -> dict:
    import redis as sync_redis

    from app.models.flash_sale import FlashSale
    from app.models.product import Product

    sale_uuid = UUID(flash_sale_id)
    stock_key = _STOCK_KEY.format(sale_id=flash_sale_id)

    # --- Redis: read & clean up stock counter ---
    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        redis_stock_raw = r.get(stock_key)
        remaining_in_redis = int(redis_stock_raw) if redis_stock_raw is not None else 0
        remaining_in_redis = max(remaining_in_redis, 0)  # guard against negatives
        r.delete(stock_key)
    finally:
        r.close()

    # --- PostgreSQL: sync remaining stock back to product ---
    session_factory = _make_session()
    async with session_factory() as session:
        async with session.begin():
            # Fetch flash sale
            sale_result = await session.execute(
                select(FlashSale).where(FlashSale.id == sale_uuid)
            )
            flash_sale = sale_result.scalar_one_or_none()
            if not flash_sale:
                logger.warning(
                    "sync_product_stock_after_sale: flash sale %s not found, skipping",
                    flash_sale_id,
                )
                return {"status": "skipped", "reason": "flash sale not found"}

            # Fetch product
            product_result = await session.execute(
                select(Product).where(Product.id == flash_sale.product_id)
            )
            product = product_result.scalar_one_or_none()
            if not product:
                logger.warning(
                    "sync_product_stock_after_sale: product %s not found, skipping",
                    flash_sale.product_id,
                )
                return {"status": "skipped", "reason": "product not found"}

            # Units sold = total allocated - remaining
            sold = flash_sale.total_stock - remaining_in_redis

            # Restore unsold units to the product
            product.stock = product.stock + remaining_in_redis

            # Persist final state on the flash sale row
            flash_sale.remaining_stock = remaining_in_redis
            flash_sale.is_active = False

    logger.info(
        "sync_product_stock_after_sale: sale=%s product=%s sold=%d restored=%d new_product_stock=%d",
        flash_sale_id,
        str(flash_sale.product_id),
        sold,
        remaining_in_redis,
        product.stock,
    )
    return {
        "status": "ok",
        "flash_sale_id": flash_sale_id,
        "sold": sold,
        "restored_to_product": remaining_in_redis,
    }
