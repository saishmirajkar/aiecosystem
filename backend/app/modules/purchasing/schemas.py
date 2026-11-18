"""Purchasing contracts."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.common import DecimalStr, InputSchema, Schema
from app.models import GRNStatus, POStatus, RequisitionStatus
from app.modules.inventory.schemas import BatchIn

PosQty = Field(gt=0, max_digits=18, decimal_places=4)
NonNegQty = Field(Decimal("0"), ge=0, max_digits=18, decimal_places=4)


# ---------------------------------------------------------------- requisitions
class RequisitionItemIn(InputSchema):
    product_id: uuid.UUID
    quantity: Decimal = PosQty
    preferred_supplier_id: uuid.UUID | None = None
    estimated_unit_cost: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=6)
    notes: str | None = Field(None, max_length=1000)


class RequisitionCreate(InputSchema):
    department_id: uuid.UUID | None = None
    required_date: date | None = None
    justification: str | None = Field(None, max_length=2000)
    items: list[RequisitionItemIn] = Field(min_length=1, max_length=200)
    submit: bool = False


class RequisitionUpdate(InputSchema):
    department_id: uuid.UUID | None = None
    required_date: date | None = None
    justification: str | None = Field(None, max_length=2000)
    items: list[RequisitionItemIn] | None = Field(None, min_length=1, max_length=200)


class RequisitionItemOut(Schema):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    quantity: DecimalStr
    preferred_supplier_id: uuid.UUID | None
    preferred_supplier_name: str | None
    estimated_unit_cost: DecimalStr | None
    notes: str | None


class RequisitionOut(Schema):
    id: uuid.UUID
    requisition_number: str
    status: RequisitionStatus
    department_id: uuid.UUID | None
    department_name: str | None
    required_date: date | None
    justification: str | None
    source: str
    created_by: uuid.UUID | None
    created_by_name: str | None
    created_at: datetime
    submitted_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    purchase_order_id: uuid.UUID | None
    estimated_total: DecimalStr
    items: list[RequisitionItemOut]


class ConvertIn(InputSchema):
    supplier_id: uuid.UUID
    delivery_warehouse_id: uuid.UUID
    expected_delivery_date: date | None = None
    unit_prices: dict[uuid.UUID, Decimal] = Field(default_factory=dict)


# ---------------------------------------------------------------- purchase orders
class POItemIn(InputSchema):
    product_id: uuid.UUID
    quantity_ordered: Decimal = PosQty
    unit_price: Decimal = Field(ge=0, max_digits=18, decimal_places=6)
    tax_rate: Decimal = Field(Decimal("0"), ge=0, le=100, max_digits=7, decimal_places=4)
    description: str | None = Field(None, max_length=300)


class POCreate(InputSchema):
    supplier_id: uuid.UUID
    delivery_warehouse_id: uuid.UUID
    order_date: date | None = None
    expected_delivery_date: date | None = None
    currency_code: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    supplier_reference: str | None = Field(None, max_length=60)
    payment_terms: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=2000)
    items: list[POItemIn] = Field(min_length=1, max_length=200)
    submit: bool = False

    @model_validator(mode="after")
    def _dates(self) -> "POCreate":
        if self.order_date and self.expected_delivery_date and self.expected_delivery_date < self.order_date:
            raise ValueError("expected_delivery_date cannot be before order_date")
        return self


class POUpdate(InputSchema):
    expected_delivery_date: date | None = None
    supplier_reference: str | None = Field(None, max_length=60)
    payment_terms: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=2000)
    items: list[POItemIn] | None = Field(None, min_length=1, max_length=200)


class POItemOut(Schema):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    description: str | None
    quantity_ordered: DecimalStr
    quantity_received: DecimalStr
    quantity_rejected: DecimalStr
    quantity_outstanding: DecimalStr
    unit_price: DecimalStr
    tax_rate: DecimalStr
    line_total: DecimalStr


class POOut(Schema):
    id: uuid.UUID
    po_number: str
    supplier_id: uuid.UUID
    supplier_code: str
    supplier_name: str
    status: POStatus
    order_date: date
    expected_delivery_date: date | None
    is_overdue: bool
    delivery_warehouse_id: uuid.UUID
    delivery_warehouse_code: str
    currency_code: str
    supplier_reference: str | None
    payment_terms: str | None
    notes: str | None
    subtotal: DecimalStr
    tax_amount: DecimalStr
    total_amount: DecimalStr
    created_by: uuid.UUID | None
    created_at: datetime
    submitted_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    sent_at: datetime | None
    closed_at: datetime | None
    cancel_reason: str | None
    requisition_numbers: list[str] = []
    items: list[POItemOut]


# ---------------------------------------------------------------- goods receipts
class GRNItemIn(InputSchema):
    purchase_order_item_id: uuid.UUID
    location_id: uuid.UUID | None = None
    quantity_received: Decimal = NonNegQty
    quantity_damaged: Decimal = NonNegQty
    quantity_rejected: Decimal = NonNegQty
    batch: BatchIn | None = None
    serial_numbers: list[str] | None = Field(None, max_length=10000)
    rejection_reason: str | None = Field(None, max_length=1000)

    @model_validator(mode="after")
    def _any(self) -> "GRNItemIn":
        if self.quantity_received + self.quantity_damaged + self.quantity_rejected <= 0:
            raise ValueError("Each line needs a received, damaged or rejected quantity")
        return self


class GRNCreate(InputSchema):
    purchase_order_id: uuid.UUID
    received_date: date | None = None
    delivery_note_number: str | None = Field(None, max_length=60)
    invoice_number: str | None = Field(None, max_length=60)
    transporter: str | None = Field(None, max_length=100)
    notes: str | None = Field(None, max_length=2000)
    items: list[GRNItemIn] = Field(min_length=1, max_length=200)


class GRNConfirmIn(InputSchema):
    over_receipt_reason: str | None = Field(None, min_length=3, max_length=1000)


class GRNItemOut(Schema):
    id: uuid.UUID
    line_no: int
    purchase_order_item_id: uuid.UUID
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    location_id: uuid.UUID
    location_path: str
    quantity_received: DecimalStr
    quantity_damaged: DecimalStr
    quantity_rejected: DecimalStr
    unit_cost: DecimalStr
    batch_number: str | None
    expiry_date: date | None
    serial_numbers: list[str] | None
    rejection_reason: str | None


class GRNOut(Schema):
    id: uuid.UUID
    grn_number: str
    purchase_order_id: uuid.UUID
    po_number: str
    supplier_id: uuid.UUID
    supplier_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    status: GRNStatus
    received_date: date
    delivery_note_number: str | None
    invoice_number: str | None
    transporter: str | None
    notes: str | None
    over_receipt_reason: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    transaction_id: uuid.UUID | None
    total_value: DecimalStr
    items: list[GRNItemOut]
