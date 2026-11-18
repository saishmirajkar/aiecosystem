"""Master data contracts."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import EmailStr, Field, model_validator

from app.core.common import DecimalStr, InputSchema, Schema
from app.models import LocationType, ProductType, UomCategory

Code = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
NonNeg = Field(ge=0, max_digits=18, decimal_places=6)


# ---------------------------------------------------------------- units
class UnitOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    category: UomCategory
    decimals: int
    is_active: bool


class UnitIn(InputSchema):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9]+$")
    name: str = Field(min_length=1, max_length=60)
    category: UomCategory
    decimals: int = Field(3, ge=0, le=4)


class UnitUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=60)
    decimals: int | None = Field(None, ge=0, le=4)
    is_active: bool | None = None


class ConversionOut(Schema):
    id: uuid.UUID
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    from_uom_code: str
    to_uom_code: str
    factor: DecimalStr
    product_id: uuid.UUID | None


class ConversionIn(InputSchema):
    from_uom_id: uuid.UUID
    to_uom_id: uuid.UUID
    factor: Decimal = Field(gt=0, max_digits=24, decimal_places=10)
    product_id: uuid.UUID | None = None


# ---------------------------------------------------------------- categories
class CategoryOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    parent_id: uuid.UUID | None
    description: str | None
    is_active: bool
    product_count: int = 0


class CategoryIn(InputSchema):
    code: str = Code
    name: str = Field(min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=1000)


class CategoryUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=100)
    parent_id: uuid.UUID | None = None
    description: str | None = Field(None, max_length=1000)
    is_active: bool | None = None


# ---------------------------------------------------------------- suppliers
class SupplierIn(InputSchema):
    code: str = Code
    name: str = Field(min_length=1, max_length=200)
    contact_person: str | None = Field(None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    country: str | None = Field(None, max_length=100)
    tax_id: str | None = Field(None, max_length=100)
    payment_terms: str | None = Field(None, max_length=100)
    currency_code: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    lead_time_days: int | None = Field(None, ge=0, le=730)
    notes: str | None = Field(None, max_length=4000)


class SupplierUpdate(SupplierIn):
    code: str | None = None  # type: ignore[assignment]
    name: str | None = Field(None, min_length=1, max_length=200)  # type: ignore[assignment]
    is_active: bool | None = None


class SupplierOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    contact_person: str | None
    email: str | None
    phone: str | None
    address: str | None
    city: str | None
    country: str | None
    tax_id: str | None
    payment_terms: str | None
    currency_code: str | None
    lead_time_days: int | None
    notes: str | None
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------- products
class ProductBase(InputSchema):
    name: str = Field(min_length=1, max_length=200)
    internal_code: str | None = Field(None, max_length=60)
    barcode: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    description: str | None = Field(None, max_length=4000)
    category_id: uuid.UUID | None = None
    product_type: ProductType
    uom_id: uuid.UUID
    manufacturer: str | None = Field(None, max_length=150)
    image_attachment_id: uuid.UUID | None = None
    purchase_price: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=6)
    standard_cost: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=6)
    selling_price: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    min_stock_level: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    max_stock_level: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    reorder_level: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    reorder_quantity: Decimal = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)
    track_batches: bool = False
    track_serials: bool = False
    track_expiry: bool = False
    shelf_life_days: int | None = Field(None, ge=1, le=36500)
    attributes: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=50)


class ProductCreate(ProductBase):
    sku: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

    @model_validator(mode="after")
    def _levels(self) -> "ProductCreate":
        if self.max_stock_level is not None and self.max_stock_level < self.min_stock_level:
            raise ValueError("max_stock_level must be greater than or equal to min_stock_level")
        if self.track_expiry and not self.track_batches:
            raise ValueError("Expiry tracking requires batch tracking")
        return self


class ProductUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=200)
    internal_code: str | None = Field(None, max_length=60)
    barcode: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    description: str | None = Field(None, max_length=4000)
    category_id: uuid.UUID | None = None
    product_type: ProductType | None = None
    manufacturer: str | None = Field(None, max_length=150)
    image_attachment_id: uuid.UUID | None = None
    purchase_price: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    standard_cost: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    selling_price: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    min_stock_level: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    max_stock_level: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    reorder_level: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    reorder_quantity: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    track_expiry: bool | None = None
    shelf_life_days: int | None = Field(None, ge=1, le=36500)
    attributes: dict[str, str | int | float | bool | None] | None = None
    is_active: bool | None = None


class StockSummary(Schema):
    physical: DecimalStr = Decimal("0")
    available: DecimalStr = Decimal("0")
    reserved: DecimalStr = Decimal("0")
    in_transit: DecimalStr = Decimal("0")
    damaged: DecimalStr = Decimal("0")
    average_cost: DecimalStr = Decimal("0")
    value: DecimalStr = Decimal("0")
    status: str = "ok"  # ok | low | critical | out | overstock


class ProductOut(Schema):
    id: uuid.UUID
    sku: str
    internal_code: str | None
    barcode: str | None
    name: str
    description: str | None
    category_id: uuid.UUID | None
    category_name: str | None = None
    product_type: ProductType
    uom_id: uuid.UUID
    uom_code: str
    manufacturer: str | None
    image_attachment_id: uuid.UUID | None
    purchase_price: DecimalStr
    standard_cost: DecimalStr
    selling_price: DecimalStr | None
    min_stock_level: DecimalStr
    max_stock_level: DecimalStr | None
    reorder_level: DecimalStr
    reorder_quantity: DecimalStr
    track_batches: bool
    track_serials: bool
    track_expiry: bool
    shelf_life_days: int | None
    attributes: dict[str, Any]
    is_active: bool
    created_at: datetime
    updated_at: datetime
    stock: StockSummary | None = None


class ProductSupplierIn(InputSchema):
    supplier_id: uuid.UUID
    supplier_sku: str | None = Field(None, max_length=60)
    lead_time_days: int | None = Field(None, ge=0, le=730)
    last_purchase_price: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    is_preferred: bool = False


class ProductSupplierOut(Schema):
    id: uuid.UUID
    supplier_id: uuid.UUID
    supplier_code: str
    supplier_name: str
    supplier_sku: str | None
    lead_time_days: int | None
    last_purchase_price: DecimalStr | None
    is_preferred: bool


# ---------------------------------------------------------------- warehouses & locations
class WarehouseIn(InputSchema):
    code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9][A-Z0-9_-]*$")
    name: str = Field(min_length=1, max_length=150)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    manager_user_id: uuid.UUID | None = None


class WarehouseUpdate(InputSchema):
    name: str | None = Field(None, min_length=1, max_length=150)
    address: str | None = Field(None, max_length=1000)
    city: str | None = Field(None, max_length=100)
    manager_user_id: uuid.UUID | None = None
    is_active: bool | None = None


class WarehouseOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    address: str | None
    city: str | None
    manager_user_id: uuid.UUID | None
    manager_name: str | None = None
    is_active: bool
    location_count: int = 0
    stock_value: DecimalStr = Decimal("0")
    sku_count: int = 0
    created_at: datetime


class LocationIn(InputSchema):
    code: str = Field(min_length=1, max_length=30, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str | None = Field(None, max_length=100)
    parent_id: uuid.UUID | None = None
    location_type: LocationType = LocationType.BIN

    @model_validator(mode="after")
    def _no_transit(self) -> "LocationIn":
        if self.location_type == LocationType.TRANSIT:
            raise ValueError("Transit locations are system managed")
        return self


class LocationUpdate(InputSchema):
    name: str | None = Field(None, max_length=100)
    is_active: bool | None = None
    is_default: bool | None = None


class LocationOut(Schema):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    parent_id: uuid.UUID | None
    code: str
    name: str | None
    location_type: LocationType
    path: str
    is_default: bool
    is_system: bool
    is_active: bool
