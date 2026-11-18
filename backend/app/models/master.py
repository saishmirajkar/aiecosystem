"""Master data: units, categories, products, suppliers, warehouses, storage locations."""
from __future__ import annotations

import enum
import uuid
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Authored, Base, Factor, Money, Qty, Timestamps, UnitCost, UUIDPk


def str_enum(e: type[enum.Enum], name: str) -> Enum:
    """VARCHAR + CHECK constraint enum (portable, migration-friendly)."""
    return Enum(e, name=name, native_enum=False, create_constraint=True, length=30,
                values_callable=lambda x: [m.value for m in x], validate_strings=True)


class UomCategory(str, enum.Enum):
    MASS = "mass"
    VOLUME = "volume"
    LENGTH = "length"
    AREA = "area"
    COUNT = "count"
    PACKAGING = "packaging"
    OTHER = "other"


class ProductType(str, enum.Enum):
    RAW_MATERIAL = "raw_material"
    COMPONENT = "component"
    WIP = "work_in_progress"
    FINISHED_GOOD = "finished_good"
    SPARE_PART = "spare_part"
    CONSUMABLE = "consumable"


class LocationType(str, enum.Enum):
    ZONE = "zone"
    SECTION = "section"
    RACK = "rack"
    SHELF = "shelf"
    BIN = "bin"
    STORAGE = "storage"
    RECEIVING = "receiving"
    TRANSIT = "transit"


class UnitOfMeasure(UUIDPk, Timestamps, Base):
    __tablename__ = "units_of_measure"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(60))
    category: Mapped[UomCategory] = mapped_column(str_enum(UomCategory, "uom_category"))
    decimals: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class UnitConversion(UUIDPk, Timestamps, Base):
    """1 from_uom = factor × to_uom. product_id NULL → global conversion."""

    __tablename__ = "unit_conversions"
    __table_args__ = (
        UniqueConstraint("from_uom_id", "to_uom_id", "product_id", name="uq_unit_conversion", postgresql_nulls_not_distinct=True),
        CheckConstraint("factor > 0", name="factor_positive"),
        CheckConstraint("from_uom_id <> to_uom_id", name="distinct_units"),
    )

    from_uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"))
    to_uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"))
    factor: Mapped[Decimal] = mapped_column(Factor)
    product_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))

    from_uom: Mapped[UnitOfMeasure] = relationship(foreign_keys=[from_uom_id], lazy="joined")
    to_uom: Mapped[UnitOfMeasure] = relationship(foreign_keys=[to_uom_id], lazy="joined")


class ProductCategory(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "product_categories"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_categories.id"))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Supplier(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "suppliers"
    __table_args__ = (Index("ix_suppliers_name_lower", text("lower(name)")),)

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    contact_person: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    tax_id: Mapped[str | None] = mapped_column(String(100))
    payment_terms: Mapped[str | None] = mapped_column(String(100))
    currency_code: Mapped[str | None] = mapped_column(String(3))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Product(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_name_lower", text("lower(name)")),
        CheckConstraint("min_stock_level >= 0 AND reorder_level >= 0 AND reorder_quantity >= 0", name="levels_non_negative"),
        CheckConstraint("max_stock_level IS NULL OR max_stock_level >= min_stock_level", name="max_ge_min"),
        CheckConstraint("purchase_price >= 0 AND standard_cost >= 0", name="costs_non_negative"),
    )

    sku: Mapped[str] = mapped_column(String(60), unique=True)
    internal_code: Mapped[str | None] = mapped_column(String(60), unique=True)
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_categories.id"), index=True)
    product_type: Mapped[ProductType] = mapped_column(str_enum(ProductType, "product_type"))
    uom_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"))
    manufacturer: Mapped[str | None] = mapped_column(String(150))
    image_attachment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("attachments.id"))
    purchase_price: Mapped[Decimal] = mapped_column(UnitCost, default=Decimal("0"))
    standard_cost: Mapped[Decimal] = mapped_column(UnitCost, default=Decimal("0"))
    selling_price: Mapped[Decimal | None] = mapped_column(Money)
    min_stock_level: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    max_stock_level: Mapped[Decimal | None] = mapped_column(Qty)
    reorder_level: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    reorder_quantity: Mapped[Decimal] = mapped_column(Qty, default=Decimal("0"))
    track_batches: Mapped[bool] = mapped_column(Boolean, default=False)
    track_serials: Mapped[bool] = mapped_column(Boolean, default=False)
    track_expiry: Mapped[bool] = mapped_column(Boolean, default=False)
    shelf_life_days: Mapped[int | None] = mapped_column(Integer)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    category: Mapped[ProductCategory | None] = relationship(lazy="joined")
    uom: Mapped[UnitOfMeasure] = relationship(lazy="joined")


class ProductSupplier(UUIDPk, Timestamps, Base):
    __tablename__ = "product_suppliers"
    __table_args__ = (UniqueConstraint("product_id", "supplier_id", name="uq_product_supplier"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), index=True)
    supplier_sku: Mapped[str | None] = mapped_column(String(60))
    lead_time_days: Mapped[int | None] = mapped_column(Integer)
    last_purchase_price: Mapped[Decimal | None] = mapped_column(UnitCost)
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False)

    supplier: Mapped[Supplier] = relationship(lazy="joined")


class Warehouse(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StorageLocation(UUIDPk, Timestamps, Authored, Base):
    """Hierarchical location: Warehouse → Section → Rack → Bin. `path` is the human readable chain."""

    __tablename__ = "storage_locations"
    __table_args__ = (UniqueConstraint("warehouse_id", "path", name="uq_location_path"),)

    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str | None] = mapped_column(String(100))
    location_type: Mapped[LocationType] = mapped_column(str_enum(LocationType, "location_type"))
    path: Mapped[str] = mapped_column(String(300))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
