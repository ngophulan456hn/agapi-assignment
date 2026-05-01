"""
Tests for FlashSaleService.

Covers: create_flash_sale (stock validation, overlap guard),
        list_flash_sales, get_flash_sale, update_flash_sale,
        delete_flash_sale, purchase (happy path, out-of-stock,
        insufficient balance, daily limit, inactive sale).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.flash_sale import FlashSale
from app.models.product import Product
from app.models.user import User
from app.schemas.flash_sale import FlashSaleCreate, FlashSaleUpdate, PurchaseRequest
from app.services.flash_sale import FlashSaleService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now():
    # SQLite strips tzinfo on storage; use naive UTC so round-trip is consistent
    return datetime.utcnow()


class _NaiveDatetime:
    """Drop-in for `datetime` that always returns naive UTC from `.now()`."""

    @staticmethod
    def now(tz=None):
        return datetime.utcnow()

    # Delegate everything else to the real datetime class
    def __getattr__(self, item):
        return getattr(datetime, item)


@pytest.fixture
def naive_datetime(monkeypatch):
    """Patch datetime in flash_sale service so naive DB values compare cleanly."""
    monkeypatch.setattr("app.services.flash_sale.datetime", _NaiveDatetime)


def _service(db, redis) -> FlashSaleService:
    return FlashSaleService(db, redis)


def _sale_create(product_id, **kwargs) -> FlashSaleCreate:
    now = _now()
    defaults = {
        "product_id": product_id,
        "name": "Flash Deal",
        "sale_price": Decimal("19.99"),
        "total_stock": 10,
        "start_time": now - timedelta(minutes=1),
        "end_time": now + timedelta(hours=1),
    }
    defaults.update(kwargs)
    return FlashSaleCreate(**defaults)


# Patch out Celery task so no broker is needed
_PATCH_TASK = patch(
    "app.services.flash_sale.sync_product_stock_after_sale.apply_async",
    return_value=None,
)


# ---------------------------------------------------------------------------
# create_flash_sale
# ---------------------------------------------------------------------------


class TestCreateFlashSale:
    @pytest.mark.asyncio
    async def test_create_success(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            sale = await svc.create_flash_sale(_sale_create(product.id), admin.id)
        assert sale.id is not None
        assert sale.product_id == product.id
        assert sale.original_price == product.price
        assert sale.total_stock == 10

    @pytest.mark.asyncio
    async def test_create_seeds_redis_stock(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            sale = await svc.create_flash_sale(_sale_create(product.id), admin.id)
        stock = await redis.get(f"flash_sale:{sale.id}:stock")
        assert int(stock) == 10

    @pytest.mark.asyncio
    async def test_create_stock_exceeds_product_stock(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            with pytest.raises(HTTPException) as exc:
                await svc.create_flash_sale(
                    _sale_create(product.id, total_stock=product.stock + 1), admin.id
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_product_not_found(self, db, redis, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            with pytest.raises(HTTPException) as exc:
                await svc.create_flash_sale(_sale_create(uuid4()), admin.id)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_create_overlap_rejected(self, db, redis, product, admin):
        """A second sale for the same product that overlaps in time must be rejected."""
        with _PATCH_TASK:
            svc = _service(db, redis)
            await svc.create_flash_sale(_sale_create(product.id), admin.id)
            with pytest.raises(HTTPException) as exc:
                await svc.create_flash_sale(_sale_create(product.id), admin.id)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_non_overlapping_allowed(self, db, redis, product, admin):
        """Two sales for the same product at different times are allowed."""
        with _PATCH_TASK:
            svc = _service(db, redis)
            now = _now()
            await svc.create_flash_sale(
                _sale_create(
                    product.id,
                    start_time=now - timedelta(hours=3),
                    end_time=now - timedelta(hours=2),
                ),
                admin.id,
            )
            sale2 = await svc.create_flash_sale(
                _sale_create(
                    product.id,
                    start_time=now + timedelta(hours=1),
                    end_time=now + timedelta(hours=2),
                ),
                admin.id,
            )
        assert sale2.id is not None


# ---------------------------------------------------------------------------
# list / get
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("naive_datetime")
class TestReadFlashSales:
    @pytest_asyncio.fixture
    async def sale(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            return await svc.create_flash_sale(_sale_create(product.id), admin.id)

    @pytest.mark.asyncio
    async def test_list_returns_sale(self, db, redis, sale):
        svc = _service(db, redis)
        items = await svc.list_flash_sales()
        assert any(s.id == sale.id for s in items)

    @pytest.mark.asyncio
    async def test_list_active_only_filters(self, db, redis, product, admin):
        now = _now()
        with _PATCH_TASK:
            svc = _service(db, redis)
            # Inactive (past)
            past = await svc.create_flash_sale(
                _sale_create(
                    product.id,
                    start_time=now - timedelta(hours=3),
                    end_time=now - timedelta(hours=2),
                ),
                admin.id,
            )
            past.is_active = False
            await db.flush()
            # Active (current)
            active = await svc.create_flash_sale(
                _sale_create(
                    product.id,
                    start_time=now - timedelta(minutes=1),
                    end_time=now + timedelta(hours=1),
                ),
                admin.id,
            )

        svc2 = _service(db, redis)
        items = await svc2.list_flash_sales(active_only=True)
        ids = {s.id for s in items}
        assert active.id in ids
        assert past.id not in ids

    @pytest.mark.asyncio
    async def test_get_syncs_redis_stock(self, db, redis, sale):
        await redis.set(f"flash_sale:{sale.id}:stock", 7)
        svc = _service(db, redis)
        fetched = await svc.get_flash_sale(sale.id)
        assert fetched.remaining_stock == 7

    @pytest.mark.asyncio
    async def test_get_not_found(self, db, redis):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.get_flash_sale(uuid4())
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# update / delete
# ---------------------------------------------------------------------------


class TestMutateFlashSales:
    @pytest_asyncio.fixture
    async def sale(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            return await svc.create_flash_sale(_sale_create(product.id), admin.id)

    @pytest.mark.asyncio
    async def test_update_name(self, db, redis, sale):
        with _PATCH_TASK:
            svc = _service(db, redis)
            updated = await svc.update_flash_sale(
                sale.id, FlashSaleUpdate(name="New Name")
            )
        assert updated.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_not_found(self, db, redis):
        with _PATCH_TASK:
            svc = _service(db, redis)
            with pytest.raises(HTTPException) as exc:
                await svc.update_flash_sale(uuid4(), FlashSaleUpdate(name="X"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_clears_redis(self, db, redis, sale):
        stock_key = f"flash_sale:{sale.id}:stock"
        await redis.set(stock_key, 10)
        svc = _service(db, redis)
        await svc.delete_flash_sale(sale.id)
        assert await redis.exists(stock_key) == 0

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db, redis):
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.delete_flash_sale(uuid4())
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# purchase
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("naive_datetime")
class TestPurchase:
    @pytest_asyncio.fixture
    async def sale(self, db, redis, product, admin):
        with _PATCH_TASK:
            svc = _service(db, redis)
            s = await svc.create_flash_sale(
                _sale_create(product.id, total_stock=5), admin.id
            )
            await redis.set(f"flash_sale:{s.id}:stock", 5)
            return s

    @pytest.mark.asyncio
    async def test_purchase_success(self, db, redis, sale, user):
        svc = _service(db, redis)
        purchase = await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        assert purchase.id is not None
        assert purchase.user_id == user.id
        remaining = await redis.get(f"flash_sale:{sale.id}:stock")
        assert int(remaining) == 4

    @pytest.mark.asyncio
    async def test_purchase_deducts_balance(self, db, redis, sale, user):
        original_balance = user.balance
        svc = _service(db, redis)
        await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        await db.refresh(user)
        assert user.balance == original_balance - sale.sale_price

    @pytest.mark.asyncio
    async def test_purchase_out_of_stock(self, db, redis, sale, user):
        await redis.set(f"flash_sale:{sale.id}:stock", 0)
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_purchase_insufficient_balance(self, db, redis, product, admin):
        broke_user = User(
            email="broke@example.com",
            username="brokeuser",
            hashed_password="x",
            is_active=True,
            balance=Decimal("0.00"),
        )
        db.add(broke_user)
        await db.flush()
        with _PATCH_TASK:
            svc = _service(db, redis)
            s = await svc.create_flash_sale(
                _sale_create(product.id, sale_price=Decimal("999.00"), total_stock=5),
                admin.id,
            )
            await redis.set(f"flash_sale:{s.id}:stock", 5)
        svc2 = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc2.purchase(s.id, broke_user.id, PurchaseRequest(quantity=1))
        assert exc.value.status_code == 402

    @pytest.mark.asyncio
    async def test_purchase_inactive_sale(self, db, redis, sale, user):
        sale.is_active = False
        await db.flush()
        svc = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_purchase_daily_limit_enforced(self, db, redis, sale, user):
        svc = _service(db, redis)
        await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        with pytest.raises(HTTPException) as exc:
            await svc.purchase(sale.id, user.id, PurchaseRequest(quantity=1))
        assert exc.value.status_code == 400
        assert "already purchased" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_purchase_sale_ended(self, db, redis, product, admin, user):
        now = _now()
        with _PATCH_TASK:
            svc = _service(db, redis)
            ended_sale = await svc.create_flash_sale(
                _sale_create(
                    product.id,
                    start_time=now - timedelta(hours=2),
                    end_time=now - timedelta(minutes=1),
                ),
                admin.id,
            )
            await redis.set(f"flash_sale:{ended_sale.id}:stock", 5)
        svc2 = _service(db, redis)
        with pytest.raises(HTTPException) as exc:
            await svc2.purchase(ended_sale.id, user.id, PurchaseRequest(quantity=1))
        assert exc.value.status_code == 400
