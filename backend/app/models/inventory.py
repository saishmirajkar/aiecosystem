"""Inventory models: balances, immutable ledger, transfers, adjustments, reservations, batches, valuation."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import ZERO, Authored, Base, Money, Qty, Timestamps, UnitCost, UUIDPk, utcnow
from app.models.master import Product, StorageLocation, Warehouse, str_enum


class StockStatus(str, enum.Enum):
    UNRESTRICTED = "unrestricted"
    DAMAGED = "damaged"
    RETURNED = "returned"
    IN_TRANSIT = "in_transit"


class MovementType(str, enum.Enum):
    OPENING = "opening"
    RECEIPT = "receipt"            # GRN against PO
    STOCK_IN = "stock_in"          # non-PO receipt (returns from production, found stock, …)
    ISSUE = "issue"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN_TRANSIT = "transfer_in_transit"
    TRANSFER_RECEIVE = "transfer_receive"
    ADJUSTMENT = "adjustment"
    RECONCILIATION = "reconciliation"
    STATUS_CHANGE = "status_change"
    # Company X production flow (client/company-x)
    JOB_WORK_OUT = "job_work_out"          # material sent to a job-work vendor (location move)
    JOB_WORK_RETURN = "job_work_return"    # material back from a vendor, unchanged item (location move)
    PRODUCTION_CONSUME = "production_consume"
    PRODUCTION_OUTPUT = "production_output"
    PRODUCTION_SCRAP = "production_scrap"
    DISPATCH = "dispatch"                  # shipped to a customer


class TransferStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AdjustmentType(str, enum.Enum):
    ADJUSTMENT = "adjustment"
    RECONCILIATION = "reconciliation"
    WRITE_OFF = "write_off"


class AdjustmentStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    POSTED = "posted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ReservationStatus(str, enum.Enum):
    ACTIVE = "active"
    RELEASED = "released"


class SerialStatus(str, enum.Enum):
    IN_STOCK = "in_stock"
    IN_TRANSIT = "in_transit"
    ISSUED = "issued"


class ProductBatch(UUIDPk, Base):
    __tablename__ = "product_batches"
    __table_args__ = (UniqueConstraint("product_id", "batch_number", name="uq_batch_product_number"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    batch_number: Mapped[str] = mapped_column(String(60))
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    supplier_batch_ref: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProductSerialNumber(UUIDPk, Base):
    __tablename__ = "product_serial_numbers"
    __table_args__ = (UniqueConstraint("product_id", "serial_number", name="uq_serial_product_number"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    serial_number: Mapped[str] = mapped_column(String(80))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_batches.id"))
    status: Mapped[SerialStatus] = mapped_column(str_enum(SerialStatus, "serial_status"))
    location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    stock_status: Mapped[StockStatus | None] = mapped_column(str_enum(StockStatus, "serial_stock_status"))
    last_transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class InventoryBalance(UUIDPk, Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("product_id", "location_id", "batch_id", "stock_status", name="uq_balance_key",
                         postgresql_nulls_not_distinct=True),
        CheckConstraint("qty_reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("stock_status = 'unrestricted' OR qty_reserved = 0", name="reserved_only_unrestricted"),
        Index("ix_balances_warehouse_product", "warehouse_id", "product_id"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_batches.id"))
    stock_status: Mapped[StockStatus] = mapped_column(str_enum(StockStatus, "stock_status"))
    qty_on_hand: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    qty_reserved: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    last_movement_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[StorageLocation] = relationship(lazy="joined")
    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    batch: Mapped[ProductBatch | None] = relationship(lazy="joined")


class InventoryTransaction(UUIDPk, Base):
    """Header of one posting (a single business event)."""

    __tablename__ = "inventory_transactions"
    __table_args__ = (Index("ix_inv_tx_reference", "reference_type", "reference_id"),)

    transaction_number: Mapped[str] = mapped_column(String(30), unique=True)
    transaction_type: Mapped[MovementType] = mapped_column(str_enum(MovementType, "transaction_type"))
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reference_number: Mapped[str | None] = mapped_column(String(60))
    reason: Mapped[str | None] = mapped_column(Text)
    approval_status: Mapped[str] = mapped_column(String(30), default="not_required")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    posted_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    movements: Mapped[list["StockMovement"]] = relationship(back_populates="transaction", lazy="selectin",
                                                            order_by="StockMovement.line_no")


class StockMovement(UUIDPk, Base):
    """Immutable ledger line. UPDATE/DELETE are blocked by a database trigger."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint("quantity <> 0", name="quantity_non_zero"),
        Index("ix_movements_product_created", "product_id", "created_at"),
        Index("ix_movements_warehouse_created", "warehouse_id", "created_at"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    movement_type: Mapped[MovementType] = mapped_column(str_enum(MovementType, "movement_type"))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_batches.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    stock_status: Mapped[StockStatus] = mapped_column(str_enum(StockStatus, "movement_stock_status"))
    counterpart_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)
    value: Mapped[Decimal] = mapped_column(Money)
    balance_after: Mapped[Decimal] = mapped_column(Qty)
    serial_numbers: Mapped[list | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    performed_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    transaction: Mapped[InventoryTransaction] = relationship(back_populates="movements")
    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[StorageLocation] = relationship(foreign_keys=[location_id], lazy="joined")
    counterpart_location: Mapped[StorageLocation | None] = relationship(foreign_keys=[counterpart_location_id], lazy="joined")
    batch: Mapped[ProductBatch | None] = relationship(lazy="joined")


class ProductValuation(Base):
    __tablename__ = "product_valuations"
    __table_args__ = (CheckConstraint("average_cost >= 0", name="avg_cost_non_negative"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), primary_key=True)
    valuation_method: Mapped[str] = mapped_column(String(30), default="weighted_average")
    quantity: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    average_cost: Mapped[Decimal] = mapped_column(UnitCost, default=ZERO)
    total_value: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StockTransfer(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "stock_transfers"
    __table_args__ = (CheckConstraint("source_warehouse_id <> destination_warehouse_id", name="distinct_warehouses"),)

    transfer_number: Mapped[str] = mapped_column(String(30), unique=True)
    source_warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    destination_warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    status: Mapped[TransferStatus] = mapped_column(str_enum(TransferStatus, "transfer_status"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="manual")

    source_warehouse: Mapped[Warehouse] = relationship(foreign_keys=[source_warehouse_id], lazy="joined")
    destination_warehouse: Mapped[Warehouse] = relationship(foreign_keys=[destination_warehouse_id], lazy="joined")
    items: Mapped[list["StockTransferItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                            order_by="StockTransferItem.line_no")


class StockTransferItem(UUIDPk, Base):
    __tablename__ = "stock_transfer_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    transfer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_transfers.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_batches.id"))
    source_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    destination_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    serial_numbers: Mapped[list | None] = mapped_column(JSONB)

    product: Mapped[Product] = relationship(lazy="joined")
    source_location: Mapped[StorageLocation] = relationship(foreign_keys=[source_location_id], lazy="joined")
    destination_location: Mapped[StorageLocation] = relationship(foreign_keys=[destination_location_id], lazy="joined")
    batch: Mapped[ProductBatch | None] = relationship(lazy="joined")


class StockAdjustment(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "stock_adjustments"

    adjustment_number: Mapped[str] = mapped_column(String(30), unique=True)
    adjustment_type: Mapped[AdjustmentType] = mapped_column(str_enum(AdjustmentType, "adjustment_type"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    status: Mapped[AdjustmentStatus] = mapped_column(str_enum(AdjustmentStatus, "adjustment_status"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"))
    source: Mapped[str] = mapped_column(String(20), default="manual")

    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    items: Mapped[list["StockAdjustmentItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                              order_by="StockAdjustmentItem.line_no")


class StockAdjustmentItem(UUIDPk, Base):
    __tablename__ = "stock_adjustment_items"
    __table_args__ = (CheckConstraint("quantity_delta <> 0 OR counted_quantity IS NOT NULL", name="delta_or_count"),)

    adjustment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("stock_adjustments.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_batches.id"))
    stock_status: Mapped[StockStatus] = mapped_column(str_enum(StockStatus, "adjustment_item_stock_status"))
    system_quantity: Mapped[Decimal | None] = mapped_column(Qty)
    counted_quantity: Mapped[Decimal | None] = mapped_column(Qty)
    quantity_delta: Mapped[Decimal] = mapped_column(Qty)
    unit_cost: Mapped[Decimal | None] = mapped_column(UnitCost)
    reason: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[StorageLocation] = relationship(lazy="joined")
    batch: Mapped[ProductBatch | None] = relationship(lazy="joined")


class InventoryReservation(UUIDPk, Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    balance_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_balances.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    status: Mapped[ReservationStatus] = mapped_column(str_enum(ReservationStatus, "reservation_status"), index=True)
    reference_type: Mapped[str | None] = mapped_column(String(40))
    reference_number: Mapped[str | None] = mapped_column(String(60))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    released_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(lazy="joined")
    balance: Mapped[InventoryBalance] = relationship(lazy="joined")
