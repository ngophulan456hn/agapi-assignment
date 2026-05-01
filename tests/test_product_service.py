"""
Tests for ProductService.

Covers: create, list, get, update, delete.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.product import ProductCreate, ProductUpdate
from app.services.product import ProductService


def _service(db) -> ProductService:
    return ProductService(db)


def _create_data(**kwargs) -> ProductCreate:
    defaults = {"name": "Widget", "price": Decimal("9.99"), "stock": 50}
    defaults.update(kwargs)
    return ProductCreate(**defaults)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreateProduct:
    @pytest.mark.asyncio
    async def test_create_returns_product(self, db):
        svc = _service(db)
        p = await svc.create(
            _create_data(name="Gadget", price=Decimal("19.99"), stock=10)
        )
        assert p.id is not None
        assert p.name == "Gadget"
        assert p.price == Decimal("19.99")
        assert p.stock == 10

    @pytest.mark.asyncio
    async def test_create_with_description(self, db):
        svc = _service(db)
        p = await svc.create(_create_data(description="A very nice widget"))
        assert p.description == "A very nice widget"

    @pytest.mark.asyncio
    async def test_create_zero_stock_allowed(self, db):
        svc = _service(db)
        p = await svc.create(_create_data(stock=0))
        assert p.stock == 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestListProducts:
    @pytest.mark.asyncio
    async def test_list_returns_all(self, db):
        svc = _service(db)
        await svc.create(_create_data(name="A"))
        await svc.create(_create_data(name="B"))
        items = await svc.list()
        assert len(items) >= 2

    @pytest.mark.asyncio
    async def test_list_pagination(self, db):
        svc = _service(db)
        for i in range(5):
            await svc.create(_create_data(name=f"Item{i}"))
        page1 = await svc.list(skip=0, limit=2)
        page2 = await svc.list(skip=2, limit=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert {p.id for p in page1}.isdisjoint({p.id for p in page2})

    @pytest.mark.asyncio
    async def test_list_empty(self, db):
        svc = _service(db)
        items = await svc.list()
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGetProduct:
    @pytest.mark.asyncio
    async def test_get_existing(self, db, product):
        svc = _service(db)
        fetched = await svc.get(product.id)
        assert fetched.id == product.id

    @pytest.mark.asyncio
    async def test_get_not_found(self, db):
        svc = _service(db)
        with pytest.raises(HTTPException) as exc:
            await svc.get(uuid4())
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------


class TestUpdateProduct:
    @pytest.mark.asyncio
    async def test_update_name(self, db, product):
        svc = _service(db)
        updated = await svc.update(product.id, ProductUpdate(name="Updated Name"))
        assert updated.name == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_stock(self, db, product):
        svc = _service(db)
        updated = await svc.update(product.id, ProductUpdate(stock=200))
        assert updated.stock == 200

    @pytest.mark.asyncio
    async def test_update_price(self, db, product):
        svc = _service(db)
        updated = await svc.update(product.id, ProductUpdate(price=Decimal("99.99")))
        assert updated.price == Decimal("99.99")

    @pytest.mark.asyncio
    async def test_update_partial_leaves_other_fields(self, db, product):
        svc = _service(db)
        original_price = product.price
        updated = await svc.update(product.id, ProductUpdate(name="New Name"))
        assert updated.price == original_price

    @pytest.mark.asyncio
    async def test_update_not_found(self, db):
        svc = _service(db)
        with pytest.raises(HTTPException) as exc:
            await svc.update(uuid4(), ProductUpdate(name="X"))
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDeleteProduct:
    @pytest.mark.asyncio
    async def test_delete_removes_product(self, db):
        svc = _service(db)
        p = await svc.create(_create_data(name="ToDelete"))
        await svc.delete(p.id)
        with pytest.raises(HTTPException) as exc:
            await svc.get(p.id)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db):
        svc = _service(db)
        with pytest.raises(HTTPException) as exc:
            await svc.delete(uuid4())
        assert exc.value.status_code == 404
