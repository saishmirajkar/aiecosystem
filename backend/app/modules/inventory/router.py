"""Inventory HTTP endpoints."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.common import Page, apply_sort, paginate
from app.core.database import ZERO
from app.core.deps import DB, Actor, require_permission
from app.core.errors import NotFound
from app.core.idempotency import run_idempotent
from app.models import (
    InventoryBalance, InventoryReservation, InventoryTransaction, MovementType, Product, ProductBatch,
    ProductSerialNumber, ProductValuation, ReservationStatus, StockAdjustment, StockMovement, StockStatus, StockTransfer,
    StorageLocation, User, Warehouse,
)
from app.modules.inventory import analytics, schemas as s, service as svc
from app.modules.inventory.alerts import compute_alerts

router = APIRouter(prefix="/inventory", tags=["inventory"])
VIEW = require_permission("inventory.view")


# ---------------------------------------------------------------- read helpers
def movement_rows(db: Session, movements: list[StockMovement]) -> list[dict[str, Any]]:
    tx_ids = {m.transaction_id for m in movements}
    txs = {t.id: t for t in db.execute(select(InventoryTransaction).where(InventoryTransaction.id.in_(tx_ids))).scalars()} if tx_ids else {}
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_({m.performed_by for m in movements}))).all()) if movements else {}
    whs = dict(db.execute(select(Warehouse.id, Warehouse.code)).all()) if movements else {}
    out = []
    for m in movements:
        tx = txs[m.transaction_id]
        out.append({
            "id": m.id, "transaction_id": m.transaction_id, "transaction_number": tx.transaction_number, "line_no": m.line_no,
            "movement_type": m.movement_type, "product_id": m.product_id, "product_sku": m.product.sku,
            "product_name": m.product.name, "uom_code": m.product.uom.code,
            "batch_number": m.batch.batch_number if m.batch else None, "warehouse_id": m.warehouse_id,
            "warehouse_code": whs.get(m.warehouse_id, ""), "location_id": m.location_id, "location_path": m.location.path,
            "counterpart_location_path": m.counterpart_location.path if m.counterpart_location else None,
            "stock_status": m.stock_status, "quantity": m.quantity, "unit_cost": m.unit_cost, "value": m.value,
            "balance_after": m.balance_after, "serial_numbers": m.serial_numbers, "reference_type": tx.reference_type,
            "reference_number": tx.reference_number, "reason": m.reason, "performed_by": m.performed_by,
            "performed_by_name": users.get(m.performed_by), "created_at": m.created_at,
        })
    return out


def transaction_out(db: Session, tx: InventoryTransaction) -> dict[str, Any]:
    return {**{c: getattr(tx, c) for c in ("id", "transaction_number", "transaction_type", "reference_type", "reference_id",
                                           "reference_number", "reason", "posted_at")},
            "movements": movement_rows(db, list(tx.movements))}


def movements_query(actor: Actor, *, product_id=None, warehouse_id=None, movement_type=None, date_from: date | None = None,
                    date_to: date | None = None, direction: str | None = None, reference: str | None = None,
                    q: str | None = None) -> Select:
    M = StockMovement
    stmt = select(M)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(M.warehouse_id.in_(actor.warehouse_ids))
    if product_id:
        stmt = stmt.where(M.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(M.warehouse_id == warehouse_id)
    if movement_type:
        stmt = stmt.where(M.movement_type == movement_type)
    if date_from:
        stmt = stmt.where(M.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        stmt = stmt.where(M.created_at < datetime.combine(date_to + timedelta(days=1), time.min))
    if direction == "in":
        stmt = stmt.where(M.quantity > 0, M.movement_type.in_([MovementType.OPENING, MovementType.RECEIPT, MovementType.STOCK_IN,
                                                                MovementType.ADJUSTMENT, MovementType.RECONCILIATION]))
    elif direction == "out":
        stmt = stmt.where(M.quantity < 0, M.movement_type.in_([MovementType.ISSUE, MovementType.ADJUSTMENT, MovementType.RECONCILIATION]))
    if reference:
        stmt = stmt.join(InventoryTransaction, InventoryTransaction.id == M.transaction_id).where(
            or_(InventoryTransaction.reference_number.ilike(f"%{reference}%"),
                InventoryTransaction.transaction_number.ilike(f"%{reference}%")))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.join(Product, Product.id == M.product_id).where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like)))
    return stmt


def balances_query(actor: Actor, *, product_id=None, warehouse_id=None, location_id=None, stock_status=None,
                   category_id=None, q: str | None = None, include_zero: bool = False) -> Select:
    B = InventoryBalance
    stmt = select(B).join(Product, Product.id == B.product_id)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(B.warehouse_id.in_(actor.warehouse_ids))
    if not include_zero:
        stmt = stmt.where(or_(B.qty_on_hand != 0, B.qty_reserved != 0))
    if product_id:
        stmt = stmt.where(B.product_id == product_id)
    if warehouse_id:
        stmt = stmt.where(B.warehouse_id == warehouse_id)
    if location_id:
        stmt = stmt.where(B.location_id == location_id)
    if stock_status:
        stmt = stmt.where(B.stock_status == stock_status)
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like)))
    return stmt


def balance_rows(db: Session, balances: list[InventoryBalance]) -> list[dict[str, Any]]:
    avg = dict(db.execute(select(ProductValuation.product_id, ProductValuation.average_cost)
                          .where(ProductValuation.product_id.in_({b.product_id for b in balances}))).all()) if balances else {}
    out = []
    for b in balances:
        cost = avg.get(b.product_id, ZERO)
        available = b.qty_on_hand - b.qty_reserved if b.stock_status == StockStatus.UNRESTRICTED else ZERO
        out.append({
            "id": b.id, "product_id": b.product_id, "product_sku": b.product.sku, "product_name": b.product.name,
            "uom_code": b.product.uom.code, "category_name": b.product.category.name if b.product.category else None,
            "warehouse_id": b.warehouse_id, "warehouse_code": b.warehouse.code, "warehouse_name": b.warehouse.name,
            "location_id": b.location_id, "location_path": b.location.path, "batch_id": b.batch_id,
            "batch_number": b.batch.batch_number if b.batch else None, "expiry_date": b.batch.expiry_date if b.batch else None,
            "stock_status": b.stock_status, "qty_on_hand": b.qty_on_hand, "qty_reserved": b.qty_reserved,
            "qty_available": available, "average_cost": cost, "value": (b.qty_on_hand * cost).quantize(Decimal("0.0001")),
            "last_movement_at": b.last_movement_at,
        })
    return out


# ---------------------------------------------------------------- overview, balances, movements
@router.get("/overview")
def get_overview(db: DB, actor: Actor = Depends(VIEW), days: int = Query(30, ge=7, le=180)) -> dict:
    data = analytics.overview(db, actor.warehouse_ids, days)
    data["recent_movements"] = movement_rows(db, data["recent_movements"])
    from app.core.common import jsonable
    return jsonable(data)


@router.get("/balances", response_model=Page[s.BalanceOut])
def list_balances(db: DB, actor: Actor = Depends(VIEW), product_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
                  location_id: uuid.UUID | None = None, stock_status: StockStatus | None = None,
                  category_id: uuid.UUID | None = None, q: str | None = None, include_zero: bool = False,
                  page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), sort: str | None = None):
    stmt = balances_query(actor, product_id=product_id, warehouse_id=warehouse_id, location_id=location_id,
                          stock_status=stock_status, category_id=category_id, q=q, include_zero=include_zero)
    stmt = apply_sort(stmt, sort, {"product": Product.name, "sku": Product.sku, "qty": InventoryBalance.qty_on_hand,
                                   "updated": InventoryBalance.last_movement_at}, Product.name)
    result = paginate(db, stmt, page, page_size)
    result["items"] = balance_rows(db, result["items"])
    return result


@router.get("/products/{product_id}/stock")
def product_stock(product_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise NotFound("Product not found")
    from app.modules.master_data.service import products_out
    rows = db.execute(balances_query(actor, product_id=product_id).order_by(InventoryBalance.warehouse_id)).unique().scalars().all()
    from app.core.common import jsonable
    return jsonable({"product": products_out(db, [product], actor)[0], "balances": balance_rows(db, list(rows))})


@router.get("/movements", response_model=Page[s.MovementOut])
def list_movements(db: DB, actor: Actor = Depends(VIEW), product_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None,
                   movement_type: MovementType | None = None, date_from: date | None = None, date_to: date | None = None,
                   direction: str | None = Query(None, pattern="^(in|out)$"), reference: str | None = None, q: str | None = None,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    stmt = movements_query(actor, product_id=product_id, warehouse_id=warehouse_id, movement_type=movement_type,
                           date_from=date_from, date_to=date_to, direction=direction, reference=reference, q=q)
    stmt = stmt.order_by(StockMovement.created_at.desc(), StockMovement.line_no)
    result = paginate(db, stmt, page, page_size)
    result["items"] = movement_rows(db, result["items"])
    return result


@router.get("/transactions/{tx_id}", response_model=s.TransactionOut)
def get_transaction(tx_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)):
    tx = db.get(InventoryTransaction, tx_id)
    if tx is None or (actor.warehouse_ids is not None and not any(actor.can_access_warehouse(m.warehouse_id) for m in tx.movements)):
        raise NotFound("Transaction not found")
    return transaction_out(db, tx)


# ---------------------------------------------------------------- postings (idempotent)
@router.post("/opening-balances", response_model=s.TransactionOut, status_code=201)
def opening_balances(data: s.OpeningBalanceIn, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.opening_balance"))):
    return run_idempotent(db, actor, request, data.model_dump(), lambda: s.TransactionOut.model_validate(
        transaction_out(db, svc.post_opening_balances(db, actor, data))))


@router.post("/receipts", response_model=s.TransactionOut, status_code=201)
def stock_in(data: s.StockInIn, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.receive"))):
    return run_idempotent(db, actor, request, data.model_dump(), lambda: s.TransactionOut.model_validate(
        transaction_out(db, svc.post_stock_in(db, actor, data))))


@router.post("/issues", response_model=s.TransactionOut, status_code=201)
def issue(data: s.IssueIn, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.issue"))):
    return run_idempotent(db, actor, request, data.model_dump(), lambda: s.TransactionOut.model_validate(
        transaction_out(db, svc.post_issue(db, actor, data))))


@router.post("/status-changes", response_model=s.TransactionOut, status_code=201)
def status_change(data: s.StatusChangeIn, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.adjust"))):
    return run_idempotent(db, actor, request, data.model_dump(), lambda: s.TransactionOut.model_validate(
        transaction_out(db, svc.post_status_change(db, actor, data))))


# ---------------------------------------------------------------- transfers
@router.get("/transfers", response_model=Page[s.TransferOut])
def list_transfers(db: DB, actor: Actor = Depends(VIEW), status: str | None = None, warehouse_id: uuid.UUID | None = None,
                   q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(StockTransfer)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(or_(StockTransfer.source_warehouse_id.in_(actor.warehouse_ids),
                              StockTransfer.destination_warehouse_id.in_(actor.warehouse_ids)))
    if status:
        stmt = stmt.where(StockTransfer.status.in_(status.split(",")))
    if warehouse_id:
        stmt = stmt.where(or_(StockTransfer.source_warehouse_id == warehouse_id, StockTransfer.destination_warehouse_id == warehouse_id))
    if q:
        stmt = stmt.where(StockTransfer.transfer_number.ilike(f"%{q}%"))
    result = paginate(db, stmt.order_by(StockTransfer.created_at.desc()), page, page_size)
    result["items"] = [svc.transfer_out(t) for t in result["items"]]
    return result


@router.post("/transfers", response_model=s.TransferOut, status_code=201)
def create_transfer(data: s.TransferCreate, db: DB, actor: Actor = Depends(require_permission("inventory.transfer"))):
    t = svc.create_transfer(db, actor, data)
    db.commit()
    return svc.transfer_out(t)


@router.get("/transfers/{tid}", response_model=s.TransferOut)
def get_transfer(tid: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)):
    return svc.transfer_out(svc._get_transfer(db, actor, tid, lock=False))


def _transfer_action(fn):
    def endpoint(tid: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("inventory.transfer"))):
        t = fn(db, actor, tid)
        db.commit()
        return svc.transfer_out(t)
    return endpoint


router.post("/transfers/{tid}/submit", response_model=s.TransferOut)(_transfer_action(svc.submit_transfer))
router.post("/transfers/{tid}/approve", response_model=s.TransferOut)(_transfer_action(svc.approve_transfer))


@router.post("/transfers/{tid}/dispatch", response_model=s.TransferOut)
def dispatch_transfer(tid: uuid.UUID, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.transfer"))):
    return run_idempotent(db, actor, request, {"tid": tid}, lambda: s.TransferOut.model_validate(
        svc.transfer_out(svc.dispatch_transfer(db, actor, tid))), status_code=200)


@router.post("/transfers/{tid}/receive", response_model=s.TransferOut)
def receive_transfer(tid: uuid.UUID, request: Request, db: DB, actor: Actor = Depends(require_permission("inventory.transfer"))):
    return run_idempotent(db, actor, request, {"tid": tid}, lambda: s.TransferOut.model_validate(
        svc.transfer_out(svc.receive_transfer(db, actor, tid))), status_code=200)


@router.post("/transfers/{tid}/cancel", response_model=s.TransferOut)
def cancel_transfer(tid: uuid.UUID, data: s.CancelIn, db: DB, actor: Actor = Depends(require_permission("inventory.transfer"))):
    t = svc.cancel_transfer(db, actor, tid, data.reason)
    db.commit()
    return svc.transfer_out(t)


# ---------------------------------------------------------------- adjustments
@router.get("/adjustments", response_model=Page[s.AdjustmentOut])
def list_adjustments(db: DB, actor: Actor = Depends(VIEW), status: str | None = None, adjustment_type: str | None = None,
                     warehouse_id: uuid.UUID | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(StockAdjustment)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(StockAdjustment.warehouse_id.in_(actor.warehouse_ids))
    if status:
        stmt = stmt.where(StockAdjustment.status.in_(status.split(",")))
    if adjustment_type:
        stmt = stmt.where(StockAdjustment.adjustment_type == adjustment_type)
    if warehouse_id:
        stmt = stmt.where(StockAdjustment.warehouse_id == warehouse_id)
    result = paginate(db, stmt.order_by(StockAdjustment.created_at.desc()), page, page_size)
    result["items"] = [svc.adjustment_out(db, a) for a in result["items"]]
    return result


@router.post("/adjustments", response_model=s.AdjustmentOut, status_code=201)
def create_adjustment(data: s.AdjustmentCreate, db: DB, actor: Actor = Depends(require_permission("inventory.adjust"))):
    a = svc.create_adjustment(db, actor, data)
    db.commit()
    return svc.adjustment_out(db, a)


@router.get("/adjustments/{aid}", response_model=s.AdjustmentOut)
def get_adjustment(aid: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)):
    a = db.get(StockAdjustment, aid)
    if a is None or not actor.can_access_warehouse(a.warehouse_id):
        raise NotFound("Stock adjustment not found")
    return svc.adjustment_out(db, a)


@router.post("/adjustments/{aid}/submit", response_model=s.AdjustmentOut)
def submit_adjustment(aid: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("inventory.adjust"))):
    a = svc.submit_adjustment(db, actor, aid)
    db.commit()
    return svc.adjustment_out(db, a)


@router.post("/adjustments/{aid}/approve", response_model=s.AdjustmentOut)
def approve_adjustment(aid: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("inventory.adjust.approve"))):
    a = svc.approve_adjustment(db, actor, aid)
    db.commit()
    return svc.adjustment_out(db, a)


@router.post("/adjustments/{aid}/reject", response_model=s.AdjustmentOut)
def reject_adjustment(aid: uuid.UUID, data: s.RejectIn, db: DB, actor: Actor = Depends(require_permission("inventory.adjust.approve"))):
    a = svc.reject_adjustment(db, actor, aid, data.reason)
    db.commit()
    return svc.adjustment_out(db, a)


@router.post("/adjustments/{aid}/cancel", response_model=s.AdjustmentOut)
def cancel_adjustment(aid: uuid.UUID, data: s.CancelIn, db: DB, actor: Actor = Depends(require_permission("inventory.adjust"))):
    a = svc.cancel_adjustment(db, actor, aid, data.reason)
    db.commit()
    return svc.adjustment_out(db, a)


# ---------------------------------------------------------------- reservations, batches, serials
@router.get("/reservations", response_model=Page[s.ReservationOut])
def list_reservations(db: DB, actor: Actor = Depends(VIEW), status: ReservationStatus | None = ReservationStatus.ACTIVE,
                      product_id: uuid.UUID | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(InventoryReservation)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(InventoryReservation.warehouse_id.in_(actor.warehouse_ids))
    if status:
        stmt = stmt.where(InventoryReservation.status == status)
    if product_id:
        stmt = stmt.where(InventoryReservation.product_id == product_id)
    result = paginate(db, stmt.order_by(InventoryReservation.created_at.desc()), page, page_size)
    result["items"] = [svc.reservation_out(r) for r in result["items"]]
    return result


@router.post("/reservations", response_model=s.ReservationOut, status_code=201)
def create_reservation(data: s.ReservationIn, db: DB, actor: Actor = Depends(require_permission("inventory.reserve"))):
    r = svc.create_reservation(db, actor, data)
    db.commit()
    return svc.reservation_out(r)


@router.post("/reservations/{rid}/release", response_model=s.ReservationOut)
def release_reservation(rid: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("inventory.reserve"))):
    r = svc.release_reservation(db, actor, rid)
    db.commit()
    return svc.reservation_out(r)


@router.get("/batches", response_model=Page[s.BatchOut])
def list_batches(db: DB, actor: Actor = Depends(VIEW), product_id: uuid.UUID | None = None, expiring_before: date | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    qty = func.coalesce(func.sum(InventoryBalance.qty_on_hand), 0)
    stmt = (select(ProductBatch, qty).outerjoin(InventoryBalance, InventoryBalance.batch_id == ProductBatch.id)
            .group_by(ProductBatch.id).order_by(ProductBatch.expiry_date.asc().nulls_last(), ProductBatch.batch_number))
    if actor.warehouse_ids is not None:
        stmt = stmt.where(or_(InventoryBalance.warehouse_id.in_(actor.warehouse_ids), InventoryBalance.id.is_(None)))
    if product_id:
        stmt = stmt.where(ProductBatch.product_id == product_id)
    if expiring_before:
        stmt = stmt.where(ProductBatch.expiry_date <= expiring_before)
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.limit(page_size).offset((page - 1) * page_size)).all()
    skus = dict(db.execute(select(Product.id, Product.sku).where(Product.id.in_({b.product_id for b, _ in rows}))).all()) if rows else {}
    items = [{**{c: getattr(b, c) for c in ("id", "product_id", "batch_number", "manufacturing_date", "expiry_date",
                                            "supplier_batch_ref", "created_at")}, "product_sku": skus.get(b.product_id), "quantity": q}
             for b, q in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/serials", response_model=Page[s.SerialOut])
def list_serials(db: DB, _: Actor = Depends(VIEW), product_id: uuid.UUID | None = None, q: str | None = None,
                 status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    stmt = select(ProductSerialNumber)
    if product_id:
        stmt = stmt.where(ProductSerialNumber.product_id == product_id)
    if q:
        stmt = stmt.where(ProductSerialNumber.serial_number.ilike(f"%{q}%"))
    if status:
        stmt = stmt.where(ProductSerialNumber.status == status)
    result = paginate(db, stmt.order_by(ProductSerialNumber.serial_number), page, page_size)
    serials = result["items"]
    prods = {p.id: p for p in db.execute(select(Product).where(Product.id.in_({x.product_id for x in serials}))).scalars()} if serials else {}
    locs = {l.id: l for l in db.execute(select(StorageLocation).where(StorageLocation.id.in_({x.location_id for x in serials if x.location_id}))).scalars()} if serials else {}
    batches = dict(db.execute(select(ProductBatch.id, ProductBatch.batch_number).where(
        ProductBatch.id.in_({x.batch_id for x in serials if x.batch_id}))).all()) if serials else {}
    result["items"] = [{"id": x.id, "product_id": x.product_id, "product_sku": prods[x.product_id].sku, "serial_number": x.serial_number,
                        "status": x.status.value, "batch_number": batches.get(x.batch_id),
                        "warehouse_code": locs[x.location_id].warehouse.code if x.location_id else None,
                        "location_path": locs[x.location_id].path if x.location_id else None,
                        "stock_status": x.stock_status.value if x.stock_status else None, "updated_at": x.updated_at} for x in serials]
    return result


# ---------------------------------------------------------------- valuation & alerts
@router.get("/valuation")
def get_valuation(db: DB, actor: Actor = Depends(require_permission("reports.view")),
                  group_by: str = Query("product", pattern="^(product|warehouse|category)$"),
                  category_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None) -> dict:
    from app.core.common import jsonable
    return jsonable(analytics.valuation(db, actor.warehouse_ids, group_by, category_id, warehouse_id))


@router.get("/alerts")
def get_alerts(db: DB, actor: Actor = Depends(VIEW), types: str | None = None) -> dict:
    alerts = compute_alerts(db, actor.warehouse_ids, set(types.split(",")) if types else None)
    counts: dict[str, int] = {}
    for a in alerts:
        counts[a["type"]] = counts.get(a["type"], 0) + 1
    return {"items": alerts, "counts": counts, "total": len(alerts)}
