from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_active_user, get_current_admin_user
from app.models.user import User
from app.schemas.product import ProductCreate, ProductResponse, ProductUpdate
from app.services.product import ProductService

router = APIRouter(prefix="/products", tags=["Products"])


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    service = ProductService(db)
    return await service.create(data)


@router.get("/", response_model=List[ProductResponse])
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> List[ProductResponse]:
    service = ProductService(db)
    return await service.list(skip=skip, limit=limit)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: UUID,
    _: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    service = ProductService(db)
    return await service.get(product_id)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: UUID,
    data: ProductUpdate,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    service = ProductService(db)
    return await service.update(product_id, data)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: UUID,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    service = ProductService(db)
    await service.delete(product_id)
