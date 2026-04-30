import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator, model_validator


class FlashSaleCreate(BaseModel):
    product_id: uuid.UUID
    name: str
    description: Optional[str] = None
    sale_price: Decimal
    total_stock: int
    start_time: datetime
    end_time: datetime

    @field_validator("total_stock")
    @classmethod
    def stock_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Total stock must be positive")
        return v

    @field_validator("sale_price")
    @classmethod
    def price_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("Price must be positive")
        return v

    @model_validator(mode="after")
    def end_after_start(self) -> "FlashSaleCreate":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class FlashSaleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sale_price: Optional[Decimal] = None
    is_active: Optional[bool] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


class FlashSaleResponse(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    description: Optional[str]
    original_price: Decimal
    sale_price: Decimal
    total_stock: int
    remaining_stock: int
    start_time: datetime
    end_time: datetime
    is_active: bool
    created_by: uuid.UUID
    created_at: datetime

    model_config = {"from_attributes": True}


class PurchaseRequest(BaseModel):
    quantity: int = 1

    @field_validator("quantity")
    @classmethod
    def quantity_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Quantity must be positive")
        return v


class PurchaseResponse(BaseModel):
    id: uuid.UUID
    flash_sale_id: uuid.UUID
    user_id: uuid.UUID
    quantity: int
    unit_price: Decimal
    total_price: Decimal
    purchased_at: datetime

    model_config = {"from_attributes": True}
