"""Procurement foundation: requisitions, purchase orders, goods receipts."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import ZERO, Authored, Base, Money, Qty, Timestamps, UnitCost, UUIDPk
from app.models.master import Product, StorageLocation, Supplier, Warehouse, str_enum


class RequisitionStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CONVERTED = "converted"
    CANCELLED = "cancelled"


class POStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SENT = "sent"
    PARTIALLY_RECEIVED = "partially_received"
    FULLY_RECEIVED = "fully_received"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class GRNStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class PurchaseRequisition(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "purchase_requisitions"

    requisition_number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[RequisitionStatus] = mapped_column(str_enum(RequisitionStatus, "requisition_status"), index=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("departments.id"))
    required_date: Mapped[date | None] = mapped_column(Date)
    justification: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    purchase_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id", use_alter=True))
    source: Mapped[str] = mapped_column(String(20), default="manual")

    items: Mapped[list["PurchaseRequisitionItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                                  order_by="PurchaseRequisitionItem.line_no")


class PurchaseRequisitionItem(UUIDPk, Base):
    __tablename__ = "purchase_requisition_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    requisition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_requisitions.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    quantity: Mapped[Decimal] = mapped_column(Qty)
    preferred_supplier_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    estimated_unit_cost: Mapped[Decimal | None] = mapped_column(UnitCost)
    notes: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
    preferred_supplier: Mapped[Supplier | None] = relationship(lazy="joined")


class PurchaseOrder(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (Index("ix_po_status_expected", "status", "expected_delivery_date"),)

    po_number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"), index=True)
    status: Mapped[POStatus] = mapped_column(str_enum(POStatus, "po_status"))
    order_date: Mapped[date] = mapped_column(Date)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    currency_code: Mapped[str] = mapped_column(String(3))
    supplier_reference: Mapped[str | None] = mapped_column(String(60))
    payment_terms: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    tax_amount: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    total_amount: Mapped[Decimal] = mapped_column(Money, default=ZERO)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)

    supplier: Mapped[Supplier] = relationship(lazy="joined")
    delivery_warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    items: Mapped[list["PurchaseOrderItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                            order_by="PurchaseOrderItem.line_no")


class PurchaseOrderItem(UUIDPk, Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        CheckConstraint("quantity_ordered > 0", name="ordered_positive"),
        CheckConstraint("quantity_received >= 0 AND quantity_rejected >= 0", name="received_non_negative"),
        CheckConstraint("unit_price >= 0", name="price_non_negative"),
    )

    purchase_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"), index=True)
    description: Mapped[str | None] = mapped_column(String(300))
    quantity_ordered: Mapped[Decimal] = mapped_column(Qty)
    quantity_received: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    quantity_rejected: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    unit_price: Mapped[Decimal] = mapped_column(UnitCost)
    tax_rate: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    line_total: Mapped[Decimal] = mapped_column(Money)

    product: Mapped[Product] = relationship(lazy="joined")

    @property
    def quantity_outstanding(self) -> Decimal:
        return max(self.quantity_ordered - self.quantity_received, ZERO)


class GoodsReceipt(UUIDPk, Timestamps, Authored, Base):
    __tablename__ = "goods_receipts"
    __table_args__ = (Index("ix_grn_status_date", "status", "received_date"),)

    grn_number: Mapped[str] = mapped_column(String(30), unique=True)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_orders.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("suppliers.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("warehouses.id"))
    status: Mapped[GRNStatus] = mapped_column(str_enum(GRNStatus, "grn_status"))
    received_date: Mapped[date] = mapped_column(Date)
    delivery_note_number: Mapped[str | None] = mapped_column(String(60))
    invoice_number: Mapped[str | None] = mapped_column(String(60))
    transporter: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    over_receipt_reason: Mapped[str | None] = mapped_column(Text)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_transactions.id"))

    purchase_order: Mapped[PurchaseOrder] = relationship(lazy="joined")
    supplier: Mapped[Supplier] = relationship(lazy="joined")
    warehouse: Mapped[Warehouse] = relationship(lazy="joined")
    items: Mapped[list["GoodsReceiptItem"]] = relationship(lazy="selectin", cascade="all, delete-orphan",
                                                           order_by="GoodsReceiptItem.line_no")


class GoodsReceiptItem(UUIDPk, Base):
    __tablename__ = "goods_receipt_items"
    __table_args__ = (
        CheckConstraint("quantity_received >= 0 AND quantity_damaged >= 0 AND quantity_rejected >= 0", name="qty_non_negative"),
        CheckConstraint("quantity_received + quantity_damaged + quantity_rejected > 0", name="qty_any_positive"),
    )

    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    purchase_order_item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("purchase_order_items.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("products.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("storage_locations.id"))
    quantity_received: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    quantity_damaged: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    quantity_rejected: Mapped[Decimal] = mapped_column(Qty, default=ZERO)
    unit_cost: Mapped[Decimal] = mapped_column(UnitCost)
    batch_number: Mapped[str | None] = mapped_column(String(60))
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    supplier_batch_ref: Mapped[str | None] = mapped_column(String(60))
    serial_numbers: Mapped[list | None] = mapped_column(JSONB)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    product: Mapped[Product] = relationship(lazy="joined")
    location: Mapped[StorageLocation] = relationship(lazy="joined")
