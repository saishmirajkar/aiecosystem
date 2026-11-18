"""Inventory contracts."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, model_validator

from app.core.common import DecimalStr, InputSchema, Schema
from app.models import AdjustmentStatus, AdjustmentType, MovementType, StockStatus, TransferStatus

PosQty = Field(gt=0, max_digits=18, decimal_places=4)
Cost = Field(None, ge=0, max_digits=18, decimal_places=6)
Reason = Field(None, max_length=1000)
Serials = Field(None, max_length=10000)


class BatchIn(InputSchema):
    batch_number: str = Field(min_length=1, max_length=60)
    manufacturing_date: date | None = None
    expiry_date: date | None = None
    supplier_batch_ref: str | None = Field(None, max_length=60)


class InboundLine(InputSchema):
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal = PosQty
    unit_cost: Decimal | None = Cost
    batch: BatchIn | None = None
    serial_numbers: list[str] | None = Serials


class OpeningBalanceIn(InputSchema):
    lines: list[InboundLine] = Field(min_length=1, max_length=500)
    reason: str | None = Field("Opening balance", max_length=1000)

    @model_validator(mode="after")
    def _cost_required(self) -> "OpeningBalanceIn":
        if any(l.unit_cost is None for l in self.lines):
            raise ValueError("unit_cost is required for every opening balance line")
        return self


class StockInLine(InboundLine):
    stock_status: StockStatus = StockStatus.UNRESTRICTED

    @model_validator(mode="after")
    def _status(self) -> "StockInLine":
        if self.stock_status == StockStatus.IN_TRANSIT:
            raise ValueError("Stock-in cannot be in transit")
        return self


class StockInIn(InputSchema):
    lines: list[StockInLine] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)
    reference_number: str | None = Field(None, max_length=60)


class IssueLine(InputSchema):
    product_id: uuid.UUID
    quantity: Decimal = PosQty
    warehouse_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serial_numbers: list[str] | None = Serials
    reservation_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _where(self) -> "IssueLine":
        if self.location_id is None and self.warehouse_id is None and self.reservation_id is None:
            raise ValueError("Provide location_id, warehouse_id (auto-allocation) or reservation_id")
        return self


class IssueIn(InputSchema):
    lines: list[IssueLine] = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=1000)
    reference_number: str | None = Field(None, max_length=60)
    department_id: uuid.UUID | None = None


class StatusChangeIn(InputSchema):
    product_id: uuid.UUID
    location_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    quantity: Decimal = PosQty
    from_status: StockStatus
    to_status: StockStatus
    reason: str = Field(min_length=3, max_length=1000)
    serial_numbers: list[str] | None = Serials

    @model_validator(mode="after")
    def _statuses(self) -> "StatusChangeIn":
        if StockStatus.IN_TRANSIT in (self.from_status, self.to_status) or self.from_status == self.to_status:
            raise ValueError("Status change must be between different on-shelf statuses")
        return self


class MovementOut(Schema):
    id: uuid.UUID
    transaction_id: uuid.UUID
    transaction_number: str
    line_no: int
    movement_type: MovementType
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    batch_number: str | None
    warehouse_id: uuid.UUID
    warehouse_code: str
    location_id: uuid.UUID
    location_path: str
    counterpart_location_path: str | None
    stock_status: StockStatus
    quantity: DecimalStr
    unit_cost: DecimalStr
    value: DecimalStr
    balance_after: DecimalStr
    serial_numbers: list[str] | None
    reference_type: str | None
    reference_number: str | None
    reason: str | None
    performed_by: uuid.UUID
    performed_by_name: str | None
    created_at: datetime


class TransactionOut(Schema):
    id: uuid.UUID
    transaction_number: str
    transaction_type: MovementType
    reference_type: str | None
    reference_id: uuid.UUID | None
    reference_number: str | None
    reason: str | None
    posted_at: datetime
    movements: list[MovementOut]


class BalanceOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    category_name: str | None
    warehouse_id: uuid.UUID
    warehouse_code: str
    warehouse_name: str
    location_id: uuid.UUID
    location_path: str
    batch_id: uuid.UUID | None
    batch_number: str | None
    expiry_date: date | None
    stock_status: StockStatus
    qty_on_hand: DecimalStr
    qty_reserved: DecimalStr
    qty_available: DecimalStr
    average_cost: DecimalStr
    value: DecimalStr
    last_movement_at: datetime | None


# ---------------------------------------------------------------- transfers
class TransferItemIn(InputSchema):
    product_id: uuid.UUID
    quantity: Decimal = PosQty
    source_location_id: uuid.UUID | None = None
    destination_location_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    serial_numbers: list[str] | None = Serials


class TransferCreate(InputSchema):
    source_warehouse_id: uuid.UUID
    destination_warehouse_id: uuid.UUID
    items: list[TransferItemIn] = Field(min_length=1, max_length=200)
    notes: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _distinct(self) -> "TransferCreate":
        if self.source_warehouse_id == self.destination_warehouse_id:
            raise ValueError("Source and destination warehouses must differ")
        return self


class TransferItemOut(Schema):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    batch_id: uuid.UUID | None
    batch_number: str | None
    source_location_id: uuid.UUID
    source_location_path: str
    destination_location_id: uuid.UUID
    destination_location_path: str
    quantity: DecimalStr
    serial_numbers: list[str] | None


class TransferOut(Schema):
    id: uuid.UUID
    transfer_number: str
    source_warehouse_id: uuid.UUID
    source_warehouse_code: str
    destination_warehouse_id: uuid.UUID
    destination_warehouse_code: str
    status: TransferStatus
    notes: str | None
    source: str
    created_by: uuid.UUID | None
    created_at: datetime
    submitted_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    dispatched_at: datetime | None
    received_at: datetime | None
    cancel_reason: str | None
    items: list[TransferItemOut]


class CancelIn(InputSchema):
    reason: str = Field(min_length=3, max_length=1000)


# ---------------------------------------------------------------- adjustments
class AdjustmentItemIn(InputSchema):
    product_id: uuid.UUID
    location_id: uuid.UUID
    batch_id: uuid.UUID | None = None
    stock_status: StockStatus = StockStatus.UNRESTRICTED
    quantity_delta: Decimal | None = Field(None, max_digits=18, decimal_places=4)
    counted_quantity: Decimal | None = Field(None, ge=0, max_digits=18, decimal_places=4)
    unit_cost: Decimal | None = Cost
    reason: str | None = Reason


class AdjustmentCreate(InputSchema):
    adjustment_type: AdjustmentType = AdjustmentType.ADJUSTMENT
    warehouse_id: uuid.UUID
    reason: str = Field(min_length=3, max_length=1000)
    notes: str | None = Field(None, max_length=2000)
    items: list[AdjustmentItemIn] = Field(min_length=1, max_length=500)
    submit: bool = False

    @model_validator(mode="after")
    def _items(self) -> "AdjustmentCreate":
        for it in self.items:
            if it.stock_status == StockStatus.IN_TRANSIT:
                raise ValueError("In-transit stock cannot be adjusted directly")
            if self.adjustment_type == AdjustmentType.RECONCILIATION:
                if it.counted_quantity is None:
                    raise ValueError("Reconciliation lines need counted_quantity")
            else:
                if it.quantity_delta is None or it.quantity_delta == 0:
                    raise ValueError("Adjustment lines need a non-zero quantity_delta")
                if self.adjustment_type == AdjustmentType.WRITE_OFF and it.quantity_delta > 0:
                    raise ValueError("Write-off lines must be negative")
        return self


class AdjustmentItemOut(Schema):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    uom_code: str
    location_id: uuid.UUID
    location_path: str
    batch_id: uuid.UUID | None
    batch_number: str | None
    stock_status: StockStatus
    system_quantity: DecimalStr | None
    counted_quantity: DecimalStr | None
    quantity_delta: DecimalStr
    unit_cost: DecimalStr | None
    reason: str | None


class AdjustmentOut(Schema):
    id: uuid.UUID
    adjustment_number: str
    adjustment_type: AdjustmentType
    warehouse_id: uuid.UUID
    warehouse_code: str
    status: AdjustmentStatus
    reason: str
    notes: str | None
    source: str
    created_by: uuid.UUID | None
    created_at: datetime
    submitted_at: datetime | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    rejection_reason: str | None
    transaction_id: uuid.UUID | None
    estimated_value: DecimalStr
    items: list[AdjustmentItemOut]


class RejectIn(InputSchema):
    reason: str = Field(min_length=3, max_length=1000)


# ---------------------------------------------------------------- reservations
class ReservationIn(InputSchema):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal = PosQty
    location_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None
    reference_type: str | None = Field(None, max_length=40)
    reference_number: str | None = Field(None, max_length=60)
    notes: str | None = Field(None, max_length=1000)


class ReservationOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_sku: str
    product_name: str
    warehouse_id: uuid.UUID
    location_path: str
    quantity: DecimalStr
    status: str
    reference_type: str | None
    reference_number: str | None
    notes: str | None
    created_by: uuid.UUID
    created_at: datetime
    released_at: datetime | None


class BatchOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_sku: str
    batch_number: str
    manufacturing_date: date | None
    expiry_date: date | None
    supplier_batch_ref: str | None
    quantity: DecimalStr
    created_at: datetime


class SerialOut(Schema):
    id: uuid.UUID
    product_id: uuid.UUID
    product_sku: str
    serial_number: str
    status: str
    batch_number: str | None
    warehouse_code: str | None
    location_path: str | None
    stock_status: str | None
    updated_at: datetime
