"""The single inventory posting engine.

Every change to `inventory_balances` goes through `post()`. It:
  1. validates lines (products, locations, warehouse access, batch/serial rules),
  2. locks valuation rows (sorted by product) and then balance rows (sorted by key) — a fixed
     global lock order that prevents deadlocks between concurrent postings,
  3. applies quantity deltas and enforces the negative-stock / reserved-stock policy,
  4. updates the weighted-average valuation,
  5. writes an immutable `inventory_transactions` header and `stock_movements` lines.
The caller owns the surrounding DB transaction (commit/rollback).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, lazyload

from app.core.common import audit, get_setting, next_number
from app.core.database import ZERO, utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, InsufficientStock, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    InventoryBalance, InventoryTransaction, LocationType, MovementType, Product, ProductBatch, ProductSerialNumber,
    ProductValuation, SerialStatus, StockMovement, StockStatus, StorageLocation,
)

Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")

VALUED_TYPES = {MovementType.OPENING, MovementType.RECEIPT, MovementType.STOCK_IN, MovementType.ISSUE,
                MovementType.ADJUSTMENT, MovementType.RECONCILIATION}
HIGH_VALUE_TYPES = {MovementType.ISSUE, MovementType.TRANSFER_OUT}


@dataclass
class Line:
    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: Decimal  # signed delta
    movement_type: MovementType
    stock_status: StockStatus = StockStatus.UNRESTRICTED
    batch_id: uuid.UUID | None = None
    unit_cost: Decimal | None = None  # inbound valued lines only; None → current average
    counterpart_location_id: uuid.UUID | None = None
    serial_numbers: list[str] | None = None
    reason: str | None = None
    consume_reserved: Decimal = ZERO  # reserved qty released & consumed by this outbound line


@dataclass
class Posting:
    transaction_type: MovementType
    lines: list[Line]
    reference_type: str | None = None
    reference_id: uuid.UUID | None = None
    reference_number: str | None = None
    reason: str | None = None
    approval_status: str = "not_required"
    approved_by: uuid.UUID | None = None
    extra_audit: dict = field(default_factory=dict)
    effective_at: datetime | None = None  # internal use only (demo/back-dated migration loads); never exposed via API


def q4(v: Decimal) -> Decimal:
    return v.quantize(Q4, rounding=ROUND_HALF_UP)


def get_or_create_batch(db: Session, product: Product, batch_number: str, *, manufacturing_date: date | None = None,
                        expiry_date: date | None = None, supplier_id: uuid.UUID | None = None,
                        supplier_batch_ref: str | None = None) -> ProductBatch:
    batch_number = batch_number.strip()
    if not batch_number:
        raise ValidationFailed("Batch number is required")
    batch = db.execute(select(ProductBatch).where(ProductBatch.product_id == product.id,
                                                  ProductBatch.batch_number == batch_number)).scalar_one_or_none()
    if batch is None:
        if product.track_expiry and expiry_date is None:
            raise ValidationFailed(f"Expiry date is required for batch-tracked product {product.sku}")
        batch = ProductBatch(product_id=product.id, batch_number=batch_number, manufacturing_date=manufacturing_date,
                             expiry_date=expiry_date, supplier_id=supplier_id, supplier_batch_ref=supplier_batch_ref)
        db.add(batch)
        db.flush()
    return batch


def _key(line: Line) -> tuple[str, str, str, str]:
    return (str(line.product_id), str(line.location_id), str(line.batch_id or ""), line.stock_status.value)


def _lock_balances(db: Session, lines: list[Line], warehouse_of: dict[uuid.UUID, uuid.UUID]) -> dict[tuple, InventoryBalance]:
    keys = sorted({_key(l): l for l in lines}.items())
    for _, l in keys:
        db.execute(pg_insert(InventoryBalance).values(
            id=uuid.uuid4(), product_id=l.product_id, location_id=l.location_id, warehouse_id=warehouse_of[l.location_id],
            batch_id=l.batch_id, stock_status=l.stock_status.value, qty_on_hand=ZERO, qty_reserved=ZERO,
        ).on_conflict_do_nothing(constraint="uq_balance_key"))
    out: dict[tuple, InventoryBalance] = {}
    for k, l in keys:
        stmt = select(InventoryBalance).where(
            InventoryBalance.product_id == l.product_id, InventoryBalance.location_id == l.location_id,
            InventoryBalance.stock_status == l.stock_status,
            InventoryBalance.batch_id.is_(None) if l.batch_id is None else InventoryBalance.batch_id == l.batch_id,
        ).options(lazyload("*")).with_for_update(of=InventoryBalance).execution_options(populate_existing=True)
        bal = db.execute(stmt).scalar_one()  # populate_existing: values reflect the locked row
        out[k] = bal
    return out


def _apply_serials(db: Session, product: Product, line: Line, location: StorageLocation, tx_id: uuid.UUID,
                   moving: dict[str, ProductSerialNumber]) -> None:
    if not product.track_serials:
        if line.serial_numbers:
            raise ValidationFailed(f"Product {product.sku} is not serial-tracked")
        return
    serials = [s.strip() for s in (line.serial_numbers or []) if s and s.strip()]
    qty = abs(line.quantity)
    if qty != qty.to_integral_value() or len(serials) != int(qty) or len(set(serials)) != len(serials):
        raise ValidationFailed(f"Provide exactly {qty.normalize()} unique serial numbers for {product.sku}",
                               details={"product_id": str(product.id)})
    existing = {s.serial_number: s for s in db.execute(select(ProductSerialNumber).where(
        ProductSerialNumber.product_id == product.id, ProductSerialNumber.serial_number.in_(serials))
        .with_for_update(of=ProductSerialNumber)).scalars()}
    for sn in serials:
        rec = moving.get(sn) or existing.get(sn)
        if line.quantity < 0:
            if rec is None or rec.location_id != line.location_id or rec.stock_status != line.stock_status:
                raise ValidationFailed(f"Serial {sn} is not in stock at {location.path} ({line.stock_status.value})")
            rec.location_id, rec.stock_status, rec.status = None, None, SerialStatus.ISSUED
            moving[sn] = rec
        else:
            if rec is None:
                rec = ProductSerialNumber(product_id=product.id, serial_number=sn, batch_id=line.batch_id, status=SerialStatus.IN_STOCK)
                db.add(rec)
            elif rec.location_id is not None:
                raise ValidationFailed(f"Serial {sn} is already in stock")
            rec.location_id, rec.stock_status = line.location_id, line.stock_status
            rec.status = SerialStatus.IN_TRANSIT if location.location_type == LocationType.TRANSIT else SerialStatus.IN_STOCK
            moving.pop(sn, None)
        rec.last_transaction_id = tx_id


def post(db: Session, actor: Actor, posting: Posting) -> InventoryTransaction:
    if not posting.lines:
        raise ValidationFailed("A posting needs at least one line")
    for l in posting.lines:
        l.quantity = Decimal(l.quantity)
        if l.quantity == 0:
            raise ValidationFailed("Line quantity must not be zero")

    products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_({l.product_id for l in posting.lines}))).scalars()}
    locations = {loc.id: loc for loc in db.execute(select(StorageLocation).where(
        StorageLocation.id.in_({l.location_id for l in posting.lines}))).scalars()}
    for l in posting.lines:
        product = products.get(l.product_id)
        if product is None:
            raise NotFound("Product not found", details={"product_id": str(l.product_id)})
        loc = locations.get(l.location_id)
        if loc is None:
            raise NotFound("Storage location not found", details={"location_id": str(l.location_id)})
        actor.require_warehouse(loc.warehouse_id)
        if l.quantity > 0 and not loc.is_active:
            raise BusinessRuleViolation(f"Location {loc.path} is inactive")
        if l.quantity > 0 and not product.is_active:
            raise BusinessRuleViolation(f"Product {product.sku} is inactive and cannot receive stock")
        is_transit = loc.location_type == LocationType.TRANSIT
        if is_transit != (l.stock_status == StockStatus.IN_TRANSIT):
            raise ValidationFailed("In-transit stock must be held in a transit location, and only there")
        if product.track_batches and l.batch_id is None:
            raise ValidationFailed(f"Batch is required for batch-tracked product {product.sku}")
        if not product.track_batches and l.batch_id is not None:
            raise ValidationFailed(f"Product {product.sku} is not batch-tracked")
        decimals = product.uom.decimals
        if l.quantity != l.quantity.quantize(Decimal(1).scaleb(-decimals)):
            raise ValidationFailed(f"{product.sku} quantity allows at most {decimals} decimal places ({product.uom.code})")
        if l.unit_cost is not None and l.unit_cost < 0:
            raise ValidationFailed("Unit cost cannot be negative")

    # Lock order: valuations (sorted product id) → balances (sorted key).
    valuations: dict[uuid.UUID, ProductValuation] = {}
    for pid in sorted(products, key=str):
        db.execute(pg_insert(ProductValuation).values(product_id=pid).on_conflict_do_nothing())
        val = db.execute(select(ProductValuation).where(ProductValuation.product_id == pid).with_for_update()
                         .execution_options(populate_existing=True)).scalar_one()
        valuations[pid] = val
    balances = _lock_balances(db, posting.lines, {loc_id: loc.warehouse_id for loc_id, loc in locations.items()})

    allow_negative = bool(get_setting(db, "inventory.allow_negative_stock")) and actor.has("inventory.negative_stock")
    threshold = Decimal(str(get_setting(db, "inventory.high_value_threshold")))
    now = posting.effective_at or utcnow()

    tx = InventoryTransaction(
        id=uuid.uuid4(), transaction_number=next_number(db, "inventory_transaction"),
        transaction_type=posting.transaction_type, reference_type=posting.reference_type,
        reference_id=posting.reference_id, reference_number=posting.reference_number, reason=posting.reason,
        approval_status=posting.approval_status, approved_by=posting.approved_by, posted_by=actor.user_id, posted_at=now,
    )
    db.add(tx)
    db.flush()

    high_value_total = ZERO
    moving_serials: dict[str, ProductSerialNumber] = {}
    for i, l in enumerate(posting.lines, start=1):
        product, loc, val = products[l.product_id], locations[l.location_id], valuations[l.product_id]
        bal = balances[_key(l)]

        if l.consume_reserved:
            if l.consume_reserved < 0 or l.consume_reserved > bal.qty_reserved:
                raise BusinessRuleViolation("Cannot consume more reserved stock than is reserved")
            bal.qty_reserved -= l.consume_reserved

        new_on_hand = bal.qty_on_hand + l.quantity
        if l.quantity < 0:
            free_after = new_on_hand - (bal.qty_reserved if l.stock_status == StockStatus.UNRESTRICTED else ZERO)
            if new_on_hand < 0 and not allow_negative:
                raise InsufficientStock(
                    f"Insufficient stock for {product.sku} at {loc.warehouse.code}/{loc.path}: "
                    f"on hand {q4(bal.qty_on_hand).normalize()}, requested {q4(-l.quantity).normalize()}",
                    details={"product_id": str(product.id), "location_id": str(loc.id),
                             "on_hand": format(bal.qty_on_hand, "f"), "requested": format(-l.quantity, "f")})
            if free_after < 0 and new_on_hand >= 0:
                raise InsufficientStock(
                    f"Stock for {product.sku} at {loc.path} is reserved: available "
                    f"{q4(bal.qty_on_hand - bal.qty_reserved).normalize()}, requested {q4(-l.quantity).normalize()}",
                    details={"product_id": str(product.id), "reserved": format(bal.qty_reserved, "f")})
        bal.qty_on_hand = new_on_hand
        bal.last_movement_at = now

        # ---- valuation (weighted average)
        unit_cost = val.average_cost
        value = ZERO
        if l.movement_type in VALUED_TYPES:
            if l.quantity > 0:
                if l.unit_cost is not None:
                    cost = Decimal(l.unit_cost)
                elif val.quantity > 0:
                    cost = val.average_cost
                else:
                    cost = product.standard_cost
                if val.quantity <= 0:
                    new_avg = cost
                else:
                    new_avg = (val.quantity * val.average_cost + l.quantity * cost) / (val.quantity + l.quantity)
                val.average_cost = new_avg.quantize(Q6, rounding=ROUND_HALF_UP)
                unit_cost = cost
            value = q4(l.quantity * unit_cost)
            val.quantity += l.quantity
            val.total_value = q4(val.quantity * val.average_cost)
        if l.movement_type in HIGH_VALUE_TYPES and l.quantity < 0:
            high_value_total += abs(l.quantity) * val.average_cost

        tx_line = StockMovement(
            transaction_id=tx.id, line_no=i, movement_type=l.movement_type, product_id=l.product_id, batch_id=l.batch_id,
            warehouse_id=loc.warehouse_id, location_id=loc.id, stock_status=l.stock_status,
            counterpart_location_id=l.counterpart_location_id, quantity=l.quantity, unit_cost=unit_cost, value=value,
            balance_after=bal.qty_on_hand, serial_numbers=l.serial_numbers or None, reason=l.reason or posting.reason,
            performed_by=actor.user_id, created_at=now,
        )
        db.add(tx_line)
        _apply_serials(db, product, l, loc, tx.id, moving_serials)

    if high_value_total > threshold and threshold > 0 and not actor.has("inventory.high_value"):
        raise PermissionDenied(
            f"Movement value {q4(high_value_total)} exceeds the high-value threshold {threshold}; "
            "a user with 'inventory.high_value' must perform it",
            details={"permission": "inventory.high_value", "value": format(q4(high_value_total), "f")})

    db.flush()
    audit(db, actor, f"inventory.post.{posting.transaction_type.value}", "inventory_transaction", tx.id, {
        "transaction_number": tx.transaction_number, "reference": posting.reference_number,
        "lines": [{"product_id": l.product_id, "location_id": l.location_id, "qty": l.quantity,
                   "status": l.stock_status, "batch_id": l.batch_id} for l in posting.lines],
        **posting.extra_audit,
    })
    return tx
