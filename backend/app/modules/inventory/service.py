"""Inventory operations built on the posting engine."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session, lazyload

from app.core.common import audit, get_setting, next_number
from app.core.database import ZERO, utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, InsufficientStock, InvalidState, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    AdjustmentStatus, AdjustmentType, InventoryBalance, InventoryReservation, InventoryTransaction, LocationType,
    MovementType, Product, ProductBatch, ProductValuation, ReservationStatus, StockAdjustment, StockAdjustmentItem,
    StockStatus, StockTransfer, StockTransferItem, StorageLocation, TransferStatus, Warehouse,
)
from app.modules.inventory import schemas as s
from app.modules.inventory.ledger import Line, Posting, get_or_create_batch, post, q4, qtext


def _product(db: Session, pid: uuid.UUID) -> Product:
    p = db.get(Product, pid)
    if p is None:
        raise NotFound("Product not found", details={"product_id": str(pid)})
    return p


def _warehouse(db: Session, actor: Actor, wid: uuid.UUID) -> Warehouse:
    wh = db.get(Warehouse, wid)
    if wh is None:
        raise NotFound("Warehouse not found")
    actor.require_warehouse(wh.id)
    if not wh.is_active:
        raise BusinessRuleViolation(f"Warehouse {wh.code} is inactive")
    return wh


def _location(db: Session, lid: uuid.UUID, warehouse_id: uuid.UUID | None = None) -> StorageLocation:
    loc = db.get(StorageLocation, lid)
    if loc is None:
        raise NotFound("Storage location not found", details={"location_id": str(lid)})
    if warehouse_id and loc.warehouse_id != warehouse_id:
        raise ValidationFailed(f"Location {loc.path} does not belong to the selected warehouse")
    return loc


def default_location(db: Session, warehouse_id: uuid.UUID) -> StorageLocation:
    loc = db.execute(select(StorageLocation).where(StorageLocation.warehouse_id == warehouse_id,
                                                   StorageLocation.is_default.is_(True))).scalars().first()
    if loc is None:
        raise BusinessRuleViolation("Warehouse has no default storage location")
    return loc


def transit_location(db: Session, warehouse_id: uuid.UUID) -> StorageLocation:
    loc = db.execute(select(StorageLocation).where(StorageLocation.warehouse_id == warehouse_id,
                                                   StorageLocation.location_type == LocationType.TRANSIT)).scalars().first()
    if loc is None:
        raise BusinessRuleViolation("Warehouse has no transit location")
    return loc


def _inbound_lines(db: Session, lines: list[s.InboundLine], movement_type: MovementType, *,
                   supplier_id: uuid.UUID | None = None) -> list[Line]:
    out = []
    for l in lines:
        product = _product(db, l.product_id)
        batch_id = None
        if product.track_batches:
            if l.batch is None:
                raise ValidationFailed(f"Batch details are required for {product.sku}")
            batch_id = get_or_create_batch(db, product, l.batch.batch_number, manufacturing_date=l.batch.manufacturing_date,
                                           expiry_date=l.batch.expiry_date, supplier_id=supplier_id,
                                           supplier_batch_ref=l.batch.supplier_batch_ref).id
        status = getattr(l, "stock_status", StockStatus.UNRESTRICTED)
        out.append(Line(product_id=l.product_id, location_id=l.location_id, quantity=l.quantity, movement_type=movement_type,
                        stock_status=status, batch_id=batch_id, unit_cost=l.unit_cost, serial_numbers=l.serial_numbers))
    return out


# ---------------------------------------------------------------- opening, stock-in, issue, status change
def post_opening_balances(db: Session, actor: Actor, data: s.OpeningBalanceIn) -> InventoryTransaction:
    lines = _inbound_lines(db, data.lines, MovementType.OPENING)
    return post(db, actor, Posting(MovementType.OPENING, lines, reference_type="opening_balance", reason=data.reason))


def post_stock_in(db: Session, actor: Actor, data: s.StockInIn) -> InventoryTransaction:
    lines = _inbound_lines(db, data.lines, MovementType.STOCK_IN)
    return post(db, actor, Posting(MovementType.STOCK_IN, lines, reference_type="stock_in",
                                   reference_number=data.reference_number, reason=data.reason))


def allocate(db: Session, product: Product, warehouse_id: uuid.UUID, qty: Decimal,
             batch_id: uuid.UUID | None = None) -> list[tuple[InventoryBalance, Decimal]]:
    """FEFO (earliest expiry first), then default location, then location path."""
    B = InventoryBalance
    stmt = (select(B).outerjoin(ProductBatch, ProductBatch.id == B.batch_id).join(StorageLocation, StorageLocation.id == B.location_id)
            .where(B.product_id == product.id, B.warehouse_id == warehouse_id, B.stock_status == StockStatus.UNRESTRICTED,
                   B.qty_on_hand - B.qty_reserved > 0)
            .order_by(ProductBatch.expiry_date.asc().nulls_last(), StorageLocation.is_default.desc(), StorageLocation.path))
    if batch_id:
        stmt = stmt.where(B.batch_id == batch_id)
    remaining = qty
    picks: list[tuple[InventoryBalance, Decimal]] = []
    for bal in db.execute(stmt).scalars():
        take = min(bal.qty_on_hand - bal.qty_reserved, remaining)
        picks.append((bal, take))
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0:
        available = qty - remaining
        raise InsufficientStock(
            f"Insufficient available stock for {product.sku}: available {qtext(available)} {product.uom.code}, "
            f"requested {qtext(qty)}",
            details={"product_id": str(product.id), "available": format(available, "f"), "requested": format(qty, "f")})
    return picks


def post_issue(db: Session, actor: Actor, data: s.IssueIn) -> InventoryTransaction:
    lines: list[Line] = []
    released: list[InventoryReservation] = []
    for l in data.lines:
        product = _product(db, l.product_id)
        if l.reservation_id:
            res = db.execute(select(InventoryReservation).where(InventoryReservation.id == l.reservation_id)
                             .with_for_update(of=InventoryReservation)).unique().scalar_one_or_none()
            if res is None or res.product_id != product.id or res.status != ReservationStatus.ACTIVE:
                raise ValidationFailed("Reservation is not active for this product")
            if l.quantity > res.quantity:
                raise ValidationFailed("Issue quantity exceeds the reserved quantity")
            bal = res.balance
            lines.append(Line(product_id=product.id, location_id=bal.location_id, quantity=-l.quantity, batch_id=bal.batch_id,
                              movement_type=MovementType.ISSUE, serial_numbers=l.serial_numbers, consume_reserved=l.quantity))
            if l.quantity == res.quantity:  # fully consumed: record keeps its quantity as history
                res.status, res.released_at, res.released_by = ReservationStatus.RELEASED, utcnow(), actor.user_id
            else:  # partially consumed: the remainder stays reserved
                res.quantity -= l.quantity
            released.append(res)
        elif l.location_id:
            loc = _location(db, l.location_id, l.warehouse_id)
            lines.append(Line(product_id=product.id, location_id=loc.id, quantity=-l.quantity, batch_id=l.batch_id,
                              movement_type=MovementType.ISSUE, serial_numbers=l.serial_numbers))
        else:
            assert l.warehouse_id is not None
            _warehouse(db, actor, l.warehouse_id)
            if product.track_serials:
                raise ValidationFailed(f"Specify the location and serial numbers to issue {product.sku}")
            for bal, take in allocate(db, product, l.warehouse_id, l.quantity, l.batch_id):
                lines.append(Line(product_id=product.id, location_id=bal.location_id, quantity=-take, batch_id=bal.batch_id,
                                  movement_type=MovementType.ISSUE))
    return post(db, actor, Posting(MovementType.ISSUE, lines, reference_type="stock_issue",
                                   reference_number=data.reference_number, reason=data.reason,
                                   extra_audit={"department_id": data.department_id,
                                                "reservations": [r.id for r in released]}))


def post_status_change(db: Session, actor: Actor, data: s.StatusChangeIn) -> InventoryTransaction:
    _product(db, data.product_id)
    lines = [
        Line(product_id=data.product_id, location_id=data.location_id, quantity=-data.quantity, batch_id=data.batch_id,
             stock_status=data.from_status, movement_type=MovementType.STATUS_CHANGE, serial_numbers=data.serial_numbers),
        Line(product_id=data.product_id, location_id=data.location_id, quantity=data.quantity, batch_id=data.batch_id,
             stock_status=data.to_status, movement_type=MovementType.STATUS_CHANGE, serial_numbers=data.serial_numbers),
    ]
    return post(db, actor, Posting(MovementType.STATUS_CHANGE, lines, reference_type="status_change", reason=data.reason))


# ---------------------------------------------------------------- transfers
def transfer_out(t: StockTransfer) -> dict[str, Any]:
    return {
        **{c: getattr(t, c) for c in ("id", "transfer_number", "source_warehouse_id", "destination_warehouse_id", "status",
                                      "notes", "source", "created_by", "created_at", "submitted_at", "approved_by",
                                      "approved_at", "dispatched_at", "received_at", "cancel_reason")},
        "source_warehouse_code": t.source_warehouse.code, "destination_warehouse_code": t.destination_warehouse.code,
        "items": [{"id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_sku": i.product.sku,
                   "product_name": i.product.name, "uom_code": i.product.uom.code, "batch_id": i.batch_id,
                   "batch_number": i.batch.batch_number if i.batch else None, "source_location_id": i.source_location_id,
                   "source_location_path": i.source_location.path, "destination_location_id": i.destination_location_id,
                   "destination_location_path": i.destination_location.path, "quantity": i.quantity,
                   "serial_numbers": i.serial_numbers} for i in t.items],
    }


def create_transfer(db: Session, actor: Actor, data: s.TransferCreate, *, source: str = "manual") -> StockTransfer:
    src = _warehouse(db, actor, data.source_warehouse_id)
    dst = _warehouse(db, actor, data.destination_warehouse_id)
    t = StockTransfer(transfer_number=next_number(db, "stock_transfer"), source_warehouse_id=src.id,
                      destination_warehouse_id=dst.id, status=TransferStatus.DRAFT, notes=data.notes, source=source,
                      created_by=actor.user_id, updated_by=actor.user_id)
    for n, it in enumerate(data.items, start=1):
        product = _product(db, it.product_id)
        if not product.is_active:
            raise BusinessRuleViolation(f"Product {product.sku} is inactive")
        if product.track_batches and it.batch_id is None:
            raise ValidationFailed(f"Batch is required to transfer {product.sku}")
        sloc = _location(db, it.source_location_id, src.id) if it.source_location_id else default_location(db, src.id)
        dloc = _location(db, it.destination_location_id, dst.id) if it.destination_location_id else default_location(db, dst.id)
        if LocationType.TRANSIT in (sloc.location_type, dloc.location_type):
            raise ValidationFailed("Transit locations cannot be chosen directly")
        t.items.append(StockTransferItem(line_no=n, product_id=product.id, batch_id=it.batch_id, source_location_id=sloc.id,
                                         destination_location_id=dloc.id, quantity=it.quantity, serial_numbers=it.serial_numbers))
    db.add(t)
    db.flush()
    audit(db, actor, "stock_transfer.create", "stock_transfer", t.id, {"number": t.transfer_number, "source": source})
    return t


def _get_transfer(db: Session, actor: Actor, tid: uuid.UUID, lock: bool = True) -> StockTransfer:
    stmt = select(StockTransfer).where(StockTransfer.id == tid)
    if lock:
        stmt = stmt.with_for_update(of=StockTransfer)
    t = db.execute(stmt).unique().scalar_one_or_none()
    if t is None or not (actor.can_access_warehouse(t.source_warehouse_id) or actor.can_access_warehouse(t.destination_warehouse_id)):
        raise NotFound("Stock transfer not found")
    return t


def _expect(status, allowed: tuple, action: str) -> None:
    if status not in allowed:
        raise InvalidState(f"Cannot {action} a document in status '{status.value}'",
                           details={"status": status.value, "allowed": [a.value for a in allowed]})


def submit_transfer(db: Session, actor: Actor, tid: uuid.UUID) -> StockTransfer:
    t = _get_transfer(db, actor, tid)
    actor.require_warehouse(t.source_warehouse_id)
    _expect(t.status, (TransferStatus.DRAFT,), "submit")
    t.submitted_at = utcnow()
    if get_setting(db, "inventory.transfer_requires_approval"):
        t.status = TransferStatus.PENDING_APPROVAL
    else:
        t.status, t.approved_by, t.approved_at = TransferStatus.APPROVED, actor.user_id, utcnow()
    audit(db, actor, "stock_transfer.submit", "stock_transfer", t.id, {"status": t.status})
    return t


def approve_transfer(db: Session, actor: Actor, tid: uuid.UUID) -> StockTransfer:
    actor.require("inventory.transfer.approve")
    t = _get_transfer(db, actor, tid)
    _expect(t.status, (TransferStatus.PENDING_APPROVAL,), "approve")
    t.status, t.approved_by, t.approved_at = TransferStatus.APPROVED, actor.user_id, utcnow()
    audit(db, actor, "stock_transfer.approve", "stock_transfer", t.id)
    return t


def dispatch_transfer(db: Session, actor: Actor, tid: uuid.UUID) -> StockTransfer:
    t = _get_transfer(db, actor, tid)
    actor.require_warehouse(t.source_warehouse_id)
    _expect(t.status, (TransferStatus.APPROVED,), "dispatch")
    transit = transit_location(db, t.destination_warehouse_id)
    lines: list[Line] = []
    for it in t.items:
        lines.append(Line(product_id=it.product_id, location_id=it.source_location_id, quantity=-it.quantity,
                          batch_id=it.batch_id, movement_type=MovementType.TRANSFER_OUT, counterpart_location_id=transit.id,
                          serial_numbers=it.serial_numbers))
        lines.append(Line(product_id=it.product_id, location_id=transit.id, quantity=it.quantity, batch_id=it.batch_id,
                          stock_status=StockStatus.IN_TRANSIT, movement_type=MovementType.TRANSFER_IN_TRANSIT,
                          counterpart_location_id=it.source_location_id, serial_numbers=it.serial_numbers))
    post(db, actor, Posting(MovementType.TRANSFER_OUT, lines, reference_type="stock_transfer", reference_id=t.id,
                            reference_number=t.transfer_number, reason="Transfer dispatched",
                            approval_status="approved" if t.approved_by else "not_required", approved_by=t.approved_by))
    t.status, t.dispatched_by, t.dispatched_at = TransferStatus.IN_TRANSIT, actor.user_id, utcnow()
    audit(db, actor, "stock_transfer.dispatch", "stock_transfer", t.id)
    return t


def receive_transfer(db: Session, actor: Actor, tid: uuid.UUID) -> StockTransfer:
    t = _get_transfer(db, actor, tid)
    actor.require_warehouse(t.destination_warehouse_id)
    _expect(t.status, (TransferStatus.IN_TRANSIT,), "receive")
    transit = transit_location(db, t.destination_warehouse_id)
    lines: list[Line] = []
    for it in t.items:
        lines.append(Line(product_id=it.product_id, location_id=transit.id, quantity=-it.quantity, batch_id=it.batch_id,
                          stock_status=StockStatus.IN_TRANSIT, movement_type=MovementType.TRANSFER_RECEIVE,
                          counterpart_location_id=it.destination_location_id, serial_numbers=it.serial_numbers))
        lines.append(Line(product_id=it.product_id, location_id=it.destination_location_id, quantity=it.quantity,
                          batch_id=it.batch_id, movement_type=MovementType.TRANSFER_RECEIVE,
                          counterpart_location_id=transit.id, serial_numbers=it.serial_numbers))
    post(db, actor, Posting(MovementType.TRANSFER_RECEIVE, lines, reference_type="stock_transfer", reference_id=t.id,
                            reference_number=t.transfer_number, reason="Transfer received at destination"))
    t.status, t.received_by, t.received_at = TransferStatus.COMPLETED, actor.user_id, utcnow()
    audit(db, actor, "stock_transfer.receive", "stock_transfer", t.id)
    return t


def cancel_transfer(db: Session, actor: Actor, tid: uuid.UUID, reason: str) -> StockTransfer:
    t = _get_transfer(db, actor, tid)
    _expect(t.status, (TransferStatus.DRAFT, TransferStatus.PENDING_APPROVAL, TransferStatus.APPROVED), "cancel")
    t.status, t.cancel_reason = TransferStatus.CANCELLED, reason
    audit(db, actor, "stock_transfer.cancel", "stock_transfer", t.id, {"reason": reason})
    return t


# ---------------------------------------------------------------- adjustments
def _balance_qty(db: Session, product_id: uuid.UUID, location_id: uuid.UUID, batch_id: uuid.UUID | None,
                 status: StockStatus) -> Decimal:
    B = InventoryBalance
    row = db.execute(select(B.qty_on_hand).where(
        B.product_id == product_id, B.location_id == location_id, B.stock_status == status,
        B.batch_id.is_(None) if batch_id is None else B.batch_id == batch_id)).scalar_one_or_none()
    return row if row is not None else ZERO


def adjustment_out(db: Session, a: StockAdjustment) -> dict[str, Any]:
    avg = dict(db.execute(select(ProductValuation.product_id, ProductValuation.average_cost)
                          .where(ProductValuation.product_id.in_([i.product_id for i in a.items]))).all()) if a.items else {}
    est = sum(((i.quantity_delta * (i.unit_cost if i.unit_cost is not None and i.quantity_delta > 0 else avg.get(i.product_id, ZERO)))
               for i in a.items), ZERO)
    return {
        **{c: getattr(a, c) for c in ("id", "adjustment_number", "adjustment_type", "warehouse_id", "status", "reason", "notes",
                                      "source", "created_by", "created_at", "submitted_at", "approved_by", "approved_at",
                                      "rejection_reason", "transaction_id")},
        "warehouse_code": a.warehouse.code, "estimated_value": q4(est),
        "items": [{"id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_sku": i.product.sku,
                   "product_name": i.product.name, "uom_code": i.product.uom.code, "location_id": i.location_id,
                   "location_path": i.location.path, "batch_id": i.batch_id,
                   "batch_number": i.batch.batch_number if i.batch else None, "stock_status": i.stock_status,
                   "system_quantity": i.system_quantity, "counted_quantity": i.counted_quantity,
                   "quantity_delta": i.quantity_delta, "unit_cost": i.unit_cost, "reason": i.reason} for i in a.items],
    }


def create_adjustment(db: Session, actor: Actor, data: s.AdjustmentCreate, *, source: str = "manual") -> StockAdjustment:
    wh = _warehouse(db, actor, data.warehouse_id)
    a = StockAdjustment(adjustment_number=next_number(db, "stock_adjustment"), adjustment_type=data.adjustment_type,
                        warehouse_id=wh.id, status=AdjustmentStatus.DRAFT, reason=data.reason, notes=data.notes, source=source,
                        created_by=actor.user_id, updated_by=actor.user_id)
    for n, it in enumerate(data.items, start=1):
        product = _product(db, it.product_id)
        loc = _location(db, it.location_id, wh.id)
        if loc.location_type == LocationType.TRANSIT:
            raise ValidationFailed("Transit locations cannot be adjusted")
        if product.track_batches and it.batch_id is None:
            raise ValidationFailed(f"Batch is required for {product.sku}")
        if product.track_serials:
            raise ValidationFailed(f"{product.sku} is serial-tracked; use stock-in/issue with serial numbers instead")
        system_qty = _balance_qty(db, product.id, loc.id, it.batch_id, it.stock_status)
        if data.adjustment_type == AdjustmentType.RECONCILIATION:
            assert it.counted_quantity is not None
            delta = it.counted_quantity - system_qty
            counted = it.counted_quantity
        else:
            assert it.quantity_delta is not None
            delta, counted = it.quantity_delta, None
        a.items.append(StockAdjustmentItem(line_no=n, product_id=product.id, location_id=loc.id, batch_id=it.batch_id,
                                           stock_status=it.stock_status, system_quantity=system_qty, counted_quantity=counted,
                                           quantity_delta=delta, unit_cost=it.unit_cost, reason=it.reason))
    db.add(a)
    db.flush()
    audit(db, actor, "stock_adjustment.create", "stock_adjustment", a.id, {"number": a.adjustment_number, "source": source})
    if data.submit:
        submit_adjustment(db, actor, a.id)
    return a


def _get_adjustment(db: Session, actor: Actor, aid: uuid.UUID) -> StockAdjustment:
    a = db.execute(select(StockAdjustment).where(StockAdjustment.id == aid).with_for_update(of=StockAdjustment)).unique().scalar_one_or_none()
    if a is None or not actor.can_access_warehouse(a.warehouse_id):
        raise NotFound("Stock adjustment not found")
    return a


def submit_adjustment(db: Session, actor: Actor, aid: uuid.UUID) -> StockAdjustment:
    a = _get_adjustment(db, actor, aid)
    _expect(a.status, (AdjustmentStatus.DRAFT,), "submit")
    a.status, a.submitted_by, a.submitted_at = AdjustmentStatus.PENDING_APPROVAL, actor.user_id, utcnow()
    audit(db, actor, "stock_adjustment.submit", "stock_adjustment", a.id)
    return a


def approve_adjustment(db: Session, actor: Actor, aid: uuid.UUID) -> StockAdjustment:
    actor.require("inventory.adjust.approve")
    a = _get_adjustment(db, actor, aid)
    _expect(a.status, (AdjustmentStatus.PENDING_APPROVAL,), "approve")
    mtype = MovementType.RECONCILIATION if a.adjustment_type == AdjustmentType.RECONCILIATION else MovementType.ADJUSTMENT
    lines = [Line(product_id=i.product_id, location_id=i.location_id, quantity=i.quantity_delta, batch_id=i.batch_id,
                  stock_status=i.stock_status, movement_type=mtype, unit_cost=i.unit_cost if i.quantity_delta > 0 else None,
                  reason=i.reason) for i in a.items if i.quantity_delta != 0]
    a.status, a.approved_by, a.approved_at = AdjustmentStatus.POSTED, actor.user_id, utcnow()
    if lines:
        tx = post(db, actor, Posting(mtype, lines, reference_type="stock_adjustment", reference_id=a.id,
                                     reference_number=a.adjustment_number, reason=a.reason, approval_status="approved",
                                     approved_by=actor.user_id))
        a.transaction_id = tx.id
    audit(db, actor, "stock_adjustment.approve", "stock_adjustment", a.id)
    return a


def reject_adjustment(db: Session, actor: Actor, aid: uuid.UUID, reason: str) -> StockAdjustment:
    actor.require("inventory.adjust.approve")
    a = _get_adjustment(db, actor, aid)
    _expect(a.status, (AdjustmentStatus.PENDING_APPROVAL,), "reject")
    a.status, a.rejection_reason, a.approved_by, a.approved_at = AdjustmentStatus.REJECTED, reason, actor.user_id, utcnow()
    audit(db, actor, "stock_adjustment.reject", "stock_adjustment", a.id, {"reason": reason})
    return a


def cancel_adjustment(db: Session, actor: Actor, aid: uuid.UUID, reason: str) -> StockAdjustment:
    a = _get_adjustment(db, actor, aid)
    _expect(a.status, (AdjustmentStatus.DRAFT, AdjustmentStatus.PENDING_APPROVAL), "cancel")
    if a.created_by != actor.user_id and not actor.has("inventory.adjust.approve"):
        raise PermissionDenied("Only the creator or an approver can cancel this adjustment")
    a.status, a.rejection_reason = AdjustmentStatus.CANCELLED, reason
    audit(db, actor, "stock_adjustment.cancel", "stock_adjustment", a.id, {"reason": reason})
    return a


# ---------------------------------------------------------------- reservations
def reservation_out(r: InventoryReservation) -> dict[str, Any]:
    return {**{c: getattr(r, c) for c in ("id", "product_id", "warehouse_id", "quantity", "reference_type", "reference_number",
                                          "notes", "created_by", "created_at", "released_at")},
            "status": r.status.value, "product_sku": r.product.sku, "product_name": r.product.name,
            "location_path": r.balance.location.path}


def create_reservation(db: Session, actor: Actor, data: s.ReservationIn) -> InventoryReservation:
    product = _product(db, data.product_id)
    _warehouse(db, actor, data.warehouse_id)
    B = InventoryBalance
    stmt = (select(B).join(StorageLocation, StorageLocation.id == B.location_id)
            .where(B.product_id == product.id, B.warehouse_id == data.warehouse_id, B.stock_status == StockStatus.UNRESTRICTED)
            .order_by(case((B.location_id == data.location_id, 0), else_=1), StorageLocation.is_default.desc(), StorageLocation.path)
            .with_for_update(of=B).execution_options(populate_existing=True))
    if data.location_id:
        stmt = stmt.where(B.location_id == data.location_id)
    if data.batch_id:
        stmt = stmt.where(B.batch_id == data.batch_id)
    bal = next((b for b in db.execute(stmt).scalars() if b.qty_on_hand - b.qty_reserved >= data.quantity), None)
    if bal is None:
        raise InsufficientStock(f"No single location has {qtext(data.quantity)} {product.uom.code} of {product.sku} "
                                "available to reserve")
    bal.qty_reserved += data.quantity
    r = InventoryReservation(product_id=product.id, warehouse_id=data.warehouse_id, balance_id=bal.id, quantity=data.quantity,
                             status=ReservationStatus.ACTIVE, reference_type=data.reference_type,
                             reference_number=data.reference_number, notes=data.notes, created_by=actor.user_id)
    db.add(r)
    db.flush()
    audit(db, actor, "inventory.reserve", "inventory_reservation", r.id, data.model_dump())
    return r


def release_reservation(db: Session, actor: Actor, rid: uuid.UUID) -> InventoryReservation:
    r = db.execute(select(InventoryReservation).where(InventoryReservation.id == rid).with_for_update(of=InventoryReservation)).unique().scalar_one_or_none()
    if r is None or not actor.can_access_warehouse(r.warehouse_id):
        raise NotFound("Reservation not found")
    _expect(r.status, (ReservationStatus.ACTIVE,), "release")
    bal = db.execute(select(InventoryBalance).where(InventoryBalance.id == r.balance_id).options(lazyload("*"))
                     .with_for_update(of=InventoryBalance).execution_options(populate_existing=True)).scalar_one()
    bal.qty_reserved = max(bal.qty_reserved - r.quantity, ZERO)
    r.status, r.released_at, r.released_by = ReservationStatus.RELEASED, utcnow(), actor.user_id
    audit(db, actor, "inventory.release_reservation", "inventory_reservation", r.id)
    return r
