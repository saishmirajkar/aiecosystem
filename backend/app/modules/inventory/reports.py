"""Report registry. Each report: id, title, description, supported filters, columns and a query function.

Reports read the same tables as the operational screens; no copies, no caches.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import ZERO
from app.core.deps import Actor
from app.core.errors import NotFound, ValidationFailed
from app.models import (
    AdjustmentStatus, GoodsReceipt, GoodsReceiptItem, GRNStatus, InventoryBalance, InventoryTransaction, MovementType,
    Product, ProductValuation, PurchaseOrder, StockAdjustment, StockAdjustmentItem, StockMovement, StockStatus, Supplier,
    User, Warehouse,
)
from app.modules.inventory.queries import stock_level_status, stock_summaries

MAX_ROWS = 50000
Q4 = Decimal("0.0001")


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    product_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None


@dataclass
class Report:
    id: str
    title: str
    description: str
    filters: list[str]
    columns: list[tuple[str, str]]  # (key, label)
    run: Callable[[Session, Actor, Filters], list[dict[str, Any]]]
    requires: list[str] = field(default_factory=list)


def _dt_range(stmt, col, f: Filters):
    if f.date_from:
        stmt = stmt.where(col >= datetime.combine(f.date_from, time.min))
    if f.date_to:
        stmt = stmt.where(col < datetime.combine(f.date_to + timedelta(days=1), time.min))
    return stmt


def _wh_scope(stmt, col, actor: Actor, f: Filters):
    if actor.warehouse_ids is not None:
        stmt = stmt.where(col.in_(actor.warehouse_ids))
    if f.warehouse_id:
        stmt = stmt.where(col == f.warehouse_id)
    return stmt


def _products(db: Session, f: Filters) -> list[Product]:
    stmt = select(Product).where(Product.is_active.is_(True)).order_by(Product.name)
    if f.product_id:
        stmt = stmt.where(Product.id == f.product_id)
    if f.category_id:
        stmt = stmt.where(Product.category_id == f.category_id)
    return list(db.execute(stmt).scalars())


# ---------------------------------------------------------------- stock position reports
def _current_stock(db: Session, actor: Actor, f: Filters, warehouse_breakdown: bool) -> list[dict[str, Any]]:
    B, V = InventoryBalance, ProductValuation
    group = [B.product_id] + ([B.warehouse_id] if warehouse_breakdown else [])
    from app.modules.inventory.queries import balance_aggregates
    agg = balance_aggregates()
    stmt = select(*group, *[v.label(k) for k, v in agg.items()]).group_by(*group)
    stmt = _wh_scope(stmt, B.warehouse_id, actor, f)
    if f.product_id:
        stmt = stmt.where(B.product_id == f.product_id)
    rows = db.execute(stmt).all()
    pids = {r.product_id for r in rows}
    products = {p.id: p for p in db.execute(select(Product).where(Product.id.in_(pids))).scalars()} if pids else {}
    avg = dict(db.execute(select(V.product_id, V.average_cost).where(V.product_id.in_(pids))).all()) if pids else {}
    whs = dict(db.execute(select(Warehouse.id, Warehouse.code)).all())
    out = []
    for r in rows:
        p = products[r.product_id]
        if f.category_id and p.category_id != f.category_id:
            continue
        if r.physical == 0 and r.in_transit == 0 and r.reserved == 0:
            continue
        cost = avg.get(p.id, ZERO)
        row = {"sku": p.sku, "product": p.name, "category": p.category.name if p.category else "", "uom": p.uom.code}
        if warehouse_breakdown:
            row["warehouse"] = whs.get(r.warehouse_id, "")
        row.update({"physical": r.physical, "available": r.available, "reserved": r.reserved, "damaged": r.damaged,
                    "in_transit": r.in_transit, "average_cost": cost, "value": ((r.physical + r.in_transit) * cost).quantize(Q4)})
        out.append(row)
    out.sort(key=lambda x: (x.get("warehouse", ""), x["product"]))
    return out


def _level_report(db: Session, actor: Actor, f: Filters, want: set[str]) -> list[dict[str, Any]]:
    products = _products(db, f)
    wh = frozenset([f.warehouse_id]) if f.warehouse_id else actor.warehouse_ids
    sums = stock_summaries(db, [p.id for p in products], wh)
    out = []
    for p in products:
        s = sums.get(p.id, {})
        avail, phys = s.get("available", ZERO), s.get("physical", ZERO)
        status = stock_level_status(p, avail, phys)
        if status in want:
            out.append({"sku": p.sku, "product": p.name, "uom": p.uom.code, "status": status, "physical": phys,
                        "available": avail, "min_level": p.min_stock_level, "reorder_level": p.reorder_level,
                        "max_level": p.max_stock_level, "reorder_quantity": p.reorder_quantity,
                        "shortfall": max(p.reorder_level - avail, ZERO), "excess": max(phys - (p.max_stock_level or phys), ZERO)})
    return out


def _valuation(db: Session, actor: Actor, f: Filters) -> list[dict[str, Any]]:
    rows = _current_stock(db, actor, f, warehouse_breakdown=False)
    return [{k: r[k] for k in ("sku", "product", "category", "uom", "physical", "in_transit", "average_cost", "value")} for r in rows]


def _damaged(db: Session, actor: Actor, f: Filters) -> list[dict[str, Any]]:
    B = InventoryBalance
    stmt = select(B).where(B.stock_status.in_([StockStatus.DAMAGED, StockStatus.RETURNED]), B.qty_on_hand != 0)
    stmt = _wh_scope(stmt, B.warehouse_id, actor, f)
    if f.product_id:
        stmt = stmt.where(B.product_id == f.product_id)
    avg = dict(db.execute(select(ProductValuation.product_id, ProductValuation.average_cost)).all())
    return [{"sku": b.product.sku, "product": b.product.name, "warehouse": b.warehouse.code, "location": b.location.path,
             "batch": b.batch.batch_number if b.batch else "", "status": b.stock_status.value, "quantity": b.qty_on_hand,
             "uom": b.product.uom.code, "value": (b.qty_on_hand * avg.get(b.product_id, ZERO)).quantize(Q4)}
            for b in db.execute(stmt).unique().scalars()]


# ---------------------------------------------------------------- movement reports
INBOUND = [MovementType.OPENING, MovementType.RECEIPT, MovementType.STOCK_IN]
OUTBOUND = [MovementType.ISSUE]


def _movements(db: Session, actor: Actor, f: Filters, types: list[MovementType] | None = None,
               sign: int = 0, require_product: bool = False) -> list[dict[str, Any]]:
    if require_product and not f.product_id:
        raise ValidationFailed("This report requires product_id")
    M, T = StockMovement, InventoryTransaction
    stmt = select(M, T.transaction_number, T.reference_number, T.reference_type).join(T, T.id == M.transaction_id)
    stmt = _wh_scope(_dt_range(stmt, M.created_at, f), M.warehouse_id, actor, f)
    if f.product_id:
        stmt = stmt.where(M.product_id == f.product_id)
    if types:
        stmt = stmt.where(M.movement_type.in_(types))
    if sign > 0:
        stmt = stmt.where(M.quantity > 0)
    elif sign < 0:
        stmt = stmt.where(M.quantity < 0)
    stmt = stmt.order_by(M.created_at.desc(), M.line_no).limit(MAX_ROWS)
    users = dict(db.execute(select(User.id, User.full_name)).all())
    whs = dict(db.execute(select(Warehouse.id, Warehouse.code)).all())
    return [{"date": m.created_at, "transaction": txn, "type": m.movement_type.value, "sku": m.product.sku,
             "product": m.product.name, "warehouse": whs.get(m.warehouse_id, ""), "location": m.location.path,
             "status": m.stock_status.value, "batch": m.batch.batch_number if m.batch else "", "quantity": m.quantity,
             "uom": m.product.uom.code, "unit_cost": m.unit_cost, "value": m.value, "balance_after": m.balance_after,
             "reference": ref or "", "reason": m.reason or "", "user": users.get(m.performed_by, "")}
            for m, txn, ref, _ in db.execute(stmt).unique()]


def _adjustments(db: Session, actor: Actor, f: Filters) -> list[dict[str, Any]]:
    A, I = StockAdjustment, StockAdjustmentItem
    stmt = select(A, I).join(I, I.adjustment_id == A.id)
    stmt = _wh_scope(_dt_range(stmt, A.created_at, f), A.warehouse_id, actor, f)
    if f.product_id:
        stmt = stmt.where(I.product_id == f.product_id)
    stmt = stmt.order_by(A.created_at.desc(), I.line_no).limit(MAX_ROWS)
    users = dict(db.execute(select(User.id, User.full_name)).all())
    return [{"date": a.created_at, "adjustment": a.adjustment_number, "type": a.adjustment_type.value, "status": a.status.value,
             "warehouse": a.warehouse.code, "sku": i.product.sku, "product": i.product.name, "location": i.location.path,
             "system_qty": i.system_quantity, "counted_qty": i.counted_quantity, "delta": i.quantity_delta,
             "reason": i.reason or a.reason, "created_by": users.get(a.created_by, ""), "approved_by": users.get(a.approved_by, "")}
            for a, i in db.execute(stmt).unique()]


def _goods_receipts(db: Session, actor: Actor, f: Filters) -> list[dict[str, Any]]:
    G, I = GoodsReceipt, GoodsReceiptItem
    stmt = select(G, I).join(I, I.goods_receipt_id == G.id).where(G.status == GRNStatus.CONFIRMED)
    stmt = _wh_scope(stmt, G.warehouse_id, actor, f)
    if f.date_from:
        stmt = stmt.where(G.received_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(G.received_date <= f.date_to)
    if f.product_id:
        stmt = stmt.where(I.product_id == f.product_id)
    if f.supplier_id:
        stmt = stmt.where(G.supplier_id == f.supplier_id)
    stmt = stmt.order_by(G.received_date.desc(), G.grn_number, I.line_no).limit(MAX_ROWS)
    return [{"received_date": g.received_date, "grn": g.grn_number, "po": g.purchase_order.po_number, "supplier": g.supplier.name,
             "warehouse": g.warehouse.code, "sku": i.product.sku, "product": i.product.name, "accepted": i.quantity_received,
             "damaged": i.quantity_damaged, "rejected": i.quantity_rejected, "uom": i.product.uom.code, "unit_cost": i.unit_cost,
             "value": ((i.quantity_received + i.quantity_damaged) * i.unit_cost).quantize(Q4),
             "delivery_note": g.delivery_note_number or "", "batch": i.batch_number or ""}
            for g, i in db.execute(stmt).unique()]


def _supplier_purchases(db: Session, actor: Actor, f: Filters) -> list[dict[str, Any]]:
    G, I = GoodsReceipt, GoodsReceiptItem
    qty = func.sum(I.quantity_received + I.quantity_damaged)
    value = func.sum((I.quantity_received + I.quantity_damaged) * I.unit_cost)
    stmt = (select(Supplier.code, Supplier.name, Product.sku, Product.name, qty, value, func.count(func.distinct(G.id)))
            .select_from(G).join(I, I.goods_receipt_id == G.id).join(Supplier, Supplier.id == G.supplier_id)
            .join(Product, Product.id == I.product_id).where(G.status == GRNStatus.CONFIRMED)
            .group_by(Supplier.code, Supplier.name, Product.sku, Product.name).order_by(Supplier.name, value.desc()))
    stmt = _wh_scope(stmt, G.warehouse_id, actor, f)
    if f.date_from:
        stmt = stmt.where(G.received_date >= f.date_from)
    if f.date_to:
        stmt = stmt.where(G.received_date <= f.date_to)
    if f.supplier_id:
        stmt = stmt.where(G.supplier_id == f.supplier_id)
    if f.product_id:
        stmt = stmt.where(I.product_id == f.product_id)
    return [{"supplier_code": sc, "supplier": sn, "sku": sku, "product": pn, "quantity_received": q,
             "value": (v or ZERO).quantize(Q4), "receipts": n} for sc, sn, sku, pn, q, v, n in db.execute(stmt)]


REPORTS: dict[str, Report] = {r.id: r for r in [
    Report("current_stock", "Current stock", "Stock position per product across visible warehouses.",
           ["product_id", "warehouse_id", "category_id"],
           [("sku", "SKU"), ("product", "Product"), ("category", "Category"), ("uom", "UoM"), ("physical", "Physical"),
            ("available", "Available"), ("reserved", "Reserved"), ("damaged", "Damaged"), ("in_transit", "In transit"),
            ("average_cost", "Avg cost"), ("value", "Value")],
           lambda db, a, f: _current_stock(db, a, f, False)),
    Report("warehouse_inventory", "Warehouse-wise inventory", "Stock position per product per warehouse.",
           ["product_id", "warehouse_id", "category_id"],
           [("warehouse", "Warehouse"), ("sku", "SKU"), ("product", "Product"), ("uom", "UoM"), ("physical", "Physical"),
            ("available", "Available"), ("reserved", "Reserved"), ("damaged", "Damaged"), ("in_transit", "In transit"),
            ("value", "Value")],
           lambda db, a, f: _current_stock(db, a, f, True)),
    Report("stock_movements", "Stock movement ledger", "Every ledger line with cost and running balance.",
           ["date_from", "date_to", "product_id", "warehouse_id"],
           [("date", "Date"), ("transaction", "Txn"), ("type", "Type"), ("sku", "SKU"), ("product", "Product"),
            ("warehouse", "WH"), ("location", "Location"), ("status", "Status"), ("quantity", "Qty"), ("uom", "UoM"),
            ("value", "Value"), ("balance_after", "Balance"), ("reference", "Reference"), ("user", "User")],
           lambda db, a, f: _movements(db, a, f)),
    Report("stock_in", "Stock-in history", "Opening balances, goods receipts and other stock-in.",
           ["date_from", "date_to", "product_id", "warehouse_id"],
           [("date", "Date"), ("transaction", "Txn"), ("type", "Type"), ("sku", "SKU"), ("product", "Product"),
            ("warehouse", "WH"), ("quantity", "Qty"), ("uom", "UoM"), ("unit_cost", "Unit cost"), ("value", "Value"),
            ("reference", "Reference"), ("user", "User")],
           lambda db, a, f: _movements(db, a, f, INBOUND, 1)),
    Report("stock_out", "Stock-out history", "Stock issues to production and departments.",
           ["date_from", "date_to", "product_id", "warehouse_id"],
           [("date", "Date"), ("transaction", "Txn"), ("sku", "SKU"), ("product", "Product"), ("warehouse", "WH"),
            ("location", "Location"), ("quantity", "Qty"), ("uom", "UoM"), ("value", "Value"), ("reference", "Reference"),
            ("reason", "Reason"), ("user", "User")],
           lambda db, a, f: _movements(db, a, f, OUTBOUND, -1)),
    Report("low_stock", "Low-stock products", "Products at or below reorder / minimum level (available stock).",
           ["warehouse_id", "category_id"],
           [("sku", "SKU"), ("product", "Product"), ("status", "Status"), ("available", "Available"), ("uom", "UoM"),
            ("min_level", "Min"), ("reorder_level", "Reorder at"), ("reorder_quantity", "Reorder qty"), ("shortfall", "Shortfall")],
           lambda db, a, f: _level_report(db, a, f, {"low", "critical", "out"})),
    Report("overstock", "Overstock", "Products above their maximum stock level.", ["warehouse_id", "category_id"],
           [("sku", "SKU"), ("product", "Product"), ("physical", "Physical"), ("uom", "UoM"), ("max_level", "Max"),
            ("excess", "Excess")],
           lambda db, a, f: _level_report(db, a, f, {"overstock"})),
    Report("inventory_valuation", "Inventory valuation", "Weighted-average valuation of owned stock (incl. in transit).",
           ["product_id", "warehouse_id", "category_id"],
           [("sku", "SKU"), ("product", "Product"), ("category", "Category"), ("physical", "Physical"),
            ("in_transit", "In transit"), ("uom", "UoM"), ("average_cost", "Avg cost"), ("value", "Value")],
           _valuation),
    Report("stock_adjustments", "Stock adjustments", "Adjustments, write-offs and reconciliations with approvals.",
           ["date_from", "date_to", "product_id", "warehouse_id"],
           [("date", "Date"), ("adjustment", "No."), ("type", "Type"), ("status", "Status"), ("warehouse", "WH"),
            ("sku", "SKU"), ("product", "Product"), ("system_qty", "System"), ("counted_qty", "Counted"), ("delta", "Delta"),
            ("reason", "Reason"), ("approved_by", "Approved by")],
           _adjustments),
    Report("damaged_stock", "Damaged & returned stock", "Stock held in damaged or returned status.",
           ["product_id", "warehouse_id"],
           [("sku", "SKU"), ("product", "Product"), ("warehouse", "WH"), ("location", "Location"), ("batch", "Batch"),
            ("status", "Status"), ("quantity", "Qty"), ("uom", "UoM"), ("value", "Value")],
           _damaged),
    Report("goods_receipts", "Goods receipts", "Confirmed GRN lines with accepted, damaged and rejected quantities.",
           ["date_from", "date_to", "product_id", "warehouse_id", "supplier_id"],
           [("received_date", "Date"), ("grn", "GRN"), ("po", "PO"), ("supplier", "Supplier"), ("sku", "SKU"),
            ("product", "Product"), ("accepted", "Accepted"), ("damaged", "Damaged"), ("rejected", "Rejected"),
            ("uom", "UoM"), ("unit_cost", "Unit cost"), ("value", "Value")],
           _goods_receipts, ["purchasing.view"]),
    Report("product_history", "Product history", "Complete ledger for one product.",
           ["product_id", "date_from", "date_to", "warehouse_id"],
           [("date", "Date"), ("transaction", "Txn"), ("type", "Type"), ("warehouse", "WH"), ("location", "Location"),
            ("status", "Status"), ("batch", "Batch"), ("quantity", "Qty"), ("unit_cost", "Unit cost"), ("value", "Value"),
            ("balance_after", "Balance"), ("reference", "Reference"), ("user", "User")],
           lambda db, a, f: _movements(db, a, f, require_product=True)),
    Report("supplier_purchases", "Supplier-wise purchased inventory", "Quantities and value received per supplier and product.",
           ["date_from", "date_to", "supplier_id", "product_id", "warehouse_id"],
           [("supplier", "Supplier"), ("sku", "SKU"), ("product", "Product"), ("quantity_received", "Qty received"),
            ("value", "Value"), ("receipts", "Receipts")],
           _supplier_purchases, ["purchasing.view"]),
]}


def get_report(report_id: str, actor: Actor) -> Report:
    report = REPORTS.get(report_id)
    if report is None:
        raise NotFound(f"Unknown report '{report_id}'", details={"available": sorted(REPORTS)})
    for perm in report.requires:
        actor.require(perm)
    return report


def run_report(db: Session, actor: Actor, report_id: str, filters: Filters) -> tuple[Report, list[dict[str, Any]]]:
    if filters.date_from and filters.date_to and filters.date_from > filters.date_to:
        raise ValidationFailed("date_from must be on or before date_to")
    report = get_report(report_id, actor)
    return report, report.run(db, actor, filters)[:MAX_ROWS]


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    text = str(v)
    return "'" + text if text[:1] in ("=", "+", "-", "@") and not _is_number(text) else text  # CSV injection guard


def _is_number(s: str) -> bool:
    try:
        Decimal(s)
        return True
    except Exception:
        return False


def to_csv(report: Report, rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in report.columns])
    for r in rows:
        writer.writerow([_cell(r.get(k)) for k, _ in report.columns])
    return buf.getvalue()
