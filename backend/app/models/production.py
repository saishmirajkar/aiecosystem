"""Company X production flow: sites, product structure, production orders, job work, dispatch.

Physical stock always lives in the foundation ledger. Plants and job-work vendors are warehouses
(locations) flagged here by `production_sites`, so stock at a vendor is a real, valued balance.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import ZERO, Authored, Base, Qty, Timestamps, UnitCost, UUIDPk, utcnow
from app.models.master import Product, Supplier, Warehouse, str_enum


class SiteKind(str, enum.Enum):
    PLANT = "plant"
    VENDOR = "vendor"


class Performer(str, enum.Enum):
    PLANT = "plant"
    VENDOR = "vendor"


class OrderStatus(str, enum.Enum):
    PLANNED = "planned"        # created; parts and stages editable
    RELEASED = "released"      # work may start
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PartSource(str, enum.Enum):
    MAKE = "make"
    BUY = "buy"


class StageStatus(str, enum.Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class DispatchStatus(str, enum.Enum):
    DRAFT = "draft"
    DISPATCHED = "dispatched"
    CANCELLED = "cancelled"


class ProductionSite(Base):
    __tablename__ = "production_sites"

    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"), primary_key=True)
    kind: Mapped[SiteKind] = mapped_column(str_enum(SiteKind, "site_kind"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    supplier: Mapped[Supplier | None] = relationship(lazy="joined")


class Customer(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "customers"

    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    contact_person: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(50))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BomLine(UUIDPk, Timestamps, Base):
    """1 unit of `product_id` needs `quantity` of `component_id`."""

    __tablename__ = "bom_lines"
    __table_args__ = (
        UniqueConstraint("product_id", "component_id", name="uq_bom_line"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("product_id <> component_id", name="not_self"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    component_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    notes: Mapped[str | None] = mapped_column(String(300))

    component: Mapped[Product] = relationship(foreign_keys=[component_id], lazy="joined")


class RouteStage(UUIDPk, Timestamps, Base):
    """Default route of a manufactured product: ordered stages, each done at the plant or a vendor."""

    __tablename__ = "route_stages"
    __table_args__ = (
        UniqueConstraint("product_id", "sequence", name="uq_route_stage_seq"),
        CheckConstraint("planned_days >= 0", name="days_non_negative"),
        CheckConstraint("rate_per_unit >= 0", name="rate_non_negative"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    performer: Mapped[Performer] = mapped_column(str_enum(Performer, "route_performer"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    rate_per_unit: Mapped[Decimal] = mapped_column(UnitCost, default=ZERO)
    planned_days: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(String(300))


class ProductionOrder(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "production_orders"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    order_number: Mapped[str] = mapped_column(String(30), unique=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Qty)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"))
    plant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    start_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[OrderStatus] = mapped_column(str_enum(OrderStatus, "production_order_status"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
    customer: Mapped[Customer | None] = relationship(lazy="joined")
    plant: Mapped[Warehouse] = relationship(lazy="joined")
    parts: Mapped[list["OrderPart"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="OrderPart.position")


class OrderPart(UUIDPk, Base):
    """A node of the order tree: the finished product, a made part, or a bought-in component."""

    __tablename__ = "order_parts"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_orders.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_parts.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    qty_per_parent: Mapped[Decimal] = mapped_column(Qty)
    source: Mapped[PartSource] = mapped_column(str_enum(PartSource, "part_source"))

    product: Mapped[Product] = relationship(lazy="joined")
    stages: Mapped[list["OrderStage"]] = relationship(lazy="selectin", cascade="all, delete-orphan", order_by="OrderStage.sequence")


class OrderStage(UUIDPk, Base):
    __tablename__ = "order_stages"
    __table_args__ = (
        CheckConstraint("units_completed >= 0 AND units_scrapped >= 0", name="units_non_negative"),
        CheckConstraint("rate_per_unit >= 0", name="rate_non_negative"),
    )

    part_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_parts.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(120))
    performer: Mapped[Performer] = mapped_column(str_enum(Performer, "stage_performer"))
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    rate_per_unit: Mapped[Decimal] = mapped_column(UnitCost, default=ZERO)
    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_end: Mapped[date | None] = mapped_column(Date)
    actual_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[StageStatus] = mapped_column(str_enum(StageStatus, "stage_status"))
    units_completed: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    units_scrapped: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    notes: Mapped[str | None] = mapped_column(String(300))

    site: Mapped[Warehouse] = relationship(lazy="joined")


class JobWorkChallan(UUIDPk, Timestamps, Authored, Base):
    """Material sent out to a job-work vendor for one stage (delivery challan)."""

    __tablename__ = "job_work_challans"
    __table_args__ = (CheckConstraint("units > 0", name="units_positive"), Index("ix_challan_stage_vendor", "stage_id", "vendor_site_id"))

    challan_number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_orders.id"), index=True)
    stage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_stages.id"))
    from_site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    vendor_site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    units: Mapped[Decimal] = mapped_column(Qty)
    challan_date: Mapped[date] = mapped_column(Date)
    expected_return_date: Mapped[date | None] = mapped_column(Date)
    vehicle_number: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"))

    from_site: Mapped[Warehouse] = relationship(foreign_keys=[from_site_id], lazy="joined")
    vendor_site: Mapped[Warehouse] = relationship(foreign_keys=[vendor_site_id], lazy="joined")
    lines: Mapped[list["JobWorkChallanLine"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class JobWorkChallanLine(UUIDPk, Base):
    __tablename__ = "job_work_challan_lines"

    challan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_work_challans.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_value: Mapped[Decimal] = mapped_column(UnitCost, default=ZERO)

    product: Mapped[Product] = relationship(lazy="joined")


class JobWorkReceipt(UUIDPk, Timestamps, Authored, Base):
    """Units coming back from a vendor for one stage: accepted, held for rework, or scrapped."""

    __tablename__ = "job_work_receipts"
    __table_args__ = (
        CheckConstraint("units_accepted >= 0 AND units_rework >= 0 AND units_scrapped >= 0", name="units_non_negative"),
        CheckConstraint("units_accepted + units_rework + units_scrapped > 0", name="units_any"),
        Index("ix_receipt_stage_vendor", "stage_id", "vendor_site_id"),
    )

    receipt_number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_orders.id"), index=True)
    stage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_stages.id"))
    vendor_site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    receive_to_site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    direct_to_next: Mapped[bool] = mapped_column(Boolean, default=False)
    units_accepted: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    units_rework: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    units_scrapped: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    scrap_reason: Mapped[str | None] = mapped_column(Text)
    vendor_document_number: Mapped[str | None] = mapped_column(String(60))
    received_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    transaction_ids: Mapped[list | None] = mapped_column(JSONB)

    vendor_site: Mapped[Warehouse] = relationship(foreign_keys=[vendor_site_id], lazy="joined")
    receive_to_site: Mapped[Warehouse | None] = relationship(foreign_keys=[receive_to_site_id], lazy="joined")


class StageCompletion(UUIDPk, Base):
    """In-house stage progress at the plant."""

    __tablename__ = "stage_completions"
    __table_args__ = (CheckConstraint("units_completed >= 0 AND units_scrapped >= 0 AND units_completed + units_scrapped > 0", name="units_valid"),)

    stage_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("order_stages.id"), index=True)
    units_completed: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    units_scrapped: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    scrap_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductionEvent(UUIDPk, Base):
    """Timeline entry: every status change and material movement of an order."""

    __tablename__ = "production_events"
    __table_args__ = (Index("ix_production_events_order_created", "order_id", "created_at"),)

    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("production_orders.id", ondelete="CASCADE"))
    part_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_parts.id", ondelete="CASCADE"))
    stage_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("order_stages.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    units: Mapped[Decimal | None] = mapped_column(Qty)
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DispatchNote(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "dispatch_notes"

    dispatch_number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id"), index=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("production_orders.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    status: Mapped[DispatchStatus] = mapped_column(str_enum(DispatchStatus, "dispatch_status"), index=True)
    dispatch_date: Mapped[date] = mapped_column(Date)
    vehicle_number: Mapped[str | None] = mapped_column(String(30))
    transporter: Mapped[str | None] = mapped_column(String(100))
    customer_reference: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"))

    customer: Mapped[Customer] = relationship(lazy="joined")
    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    lines: Mapped[list["DispatchLine"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class DispatchLine(UUIDPk, Base):
    __tablename__ = "dispatch_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    dispatch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dispatch_notes.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)

    product: Mapped[Product] = relationship(lazy="joined")
