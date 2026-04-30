from typing import List
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate


class ProductService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, data: ProductCreate) -> Product:
        product = Product(
            name=data.name,
            description=data.description,
            price=data.price,
            stock=data.stock,
        )
        self.db.add(product)
        await self.db.flush()
        await self.db.refresh(product)
        return product

    async def list(self, skip: int = 0, limit: int = 20) -> List[Product]:
        result = await self.db.execute(
            select(Product)
            .offset(skip)
            .limit(limit)
            .order_by(Product.created_at.desc())
        )
        return list(result.scalars().all())

    async def get(self, product_id: UUID) -> Product:
        return await self._get_or_404(product_id)

    async def update(self, product_id: UUID, data: ProductUpdate) -> Product:
        product = await self._get_or_404(product_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(product, field, value)
        await self.db.flush()
        await self.db.refresh(product)
        return product

    async def delete(self, product_id: UUID) -> None:
        product = await self._get_or_404(product_id)
        await self.db.delete(product)

    async def _get_or_404(self, product_id: UUID) -> Product:
        result = await self.db.execute(select(Product).where(Product.id == product_id))
        product = result.scalar_one_or_none()
        if not product:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Product not found",
            )
        return product
