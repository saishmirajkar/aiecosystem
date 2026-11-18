"""Inventory Agent tools. Every tool reads through the same services/queries as the REST API.

Read tools return verified records or calculated figures; draft tools create a pending approval and
never touch business tables.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, or_, select

from app.ai_tools.base import ToolContext, ToolError, ToolInput, ToolOutput, tool
from app.core.database import ZERO, utcnow
from app.core.errors import NotFound
from app.models import (
    GoodsReceipt, GRNStatus, InventoryBalance, LocationType, MovementType, POStatus, Product, ProductSupplier,
    ProductType, PurchaseOrder, PurchaseOrderItem, StockMovement, StockStatus, StorageLocation, Supplier, Warehouse,
)
from app.modules.inventory.alerts import compute_alerts
from app.modules.inventory.analytics import valuation as compute_valuation
from app.modules.inventory.queries import stock_summaries, summary_for
from app.modules.inventory.reports import REPORTS, Filters, run_report
from app.orchestrator import approval_manager

Q4 = Decimal("0.0001")


def fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, Decimal):
        q = v.quantize(Q4).normalize()
        return format(q, "f") if q != q.to_integral() else format(q.quantize(Decimal(1)), "f")
    return str(v)


def money(v: Decimal) -> str:
    return f"{v.quantize(Decimal('0.01')):,.2f}"


def src(kind: str, rid: Any, label: str, as_of: datetime | None = None) -> dict[str, Any]:
    return {"type": kind, "id": str(rid), "label": label, "as_of": (as_of or utcnow()).isoformat()}


def table(title: str, columns: list[tuple[str, str]], rows: list[dict[str, Any]], provenance: str,
          sources: list[dict[str, Any]] | None = None, note: str | None = None) -> dict[str, Any]:
    return {"type": "table", "title": title, "provenance": provenance, "columns": [{"key": k, "label": l} for k, l in columns],
            "rows": rows, "sources": (sources or [])[:50], "note": note, "as_of": utcnow().isoformat()}


def metrics(title: str, items: list[dict[str, Any]], provenance: str, sources: list[dict[str, Any]] | None = None,
            note: str | None = None) -> dict[str, Any]:
    return {"type": "metrics", "title": title, "provenance": provenance, "metrics": items, "sources": (sources or [])[:50],
            "note": note, "as_of": utcnow().isoformat()}


# ---------------------------------------------------------------- resolution helpers
def find_products(ctx: ToolContext, ref: str, limit: int = 20) -> list[Product]:
    db, ref = ctx.db, ref.strip()
    try:
        p = db.get(Product, uuid.UUID(ref))
        if p:
            return [p]
    except ValueError:
        pass
    exact = db.execute(select(Product).where(or_(func.lower(Product.sku) == ref.lower(),
                                                 func.lower(func.coalesce(Product.internal_code, "")) == ref.lower(),
                                                 Product.barcode == ref))).scalars().all()
    if exact:
        return list(exact)
    words = [w for w in ref.lower().replace(",", " ").split() if len(w) > 1]
    stmt = select(Product).where(Product.is_active.is_(True))
    for w in words or [ref.lower()]:
        like = f"%{w.rstrip('s') if len(w) > 3 else w}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(like), func.lower(Product.sku).like(like),
                              func.lower(func.coalesce(Product.description, "")).like(like)))
    return list(db.execute(stmt.order_by(Product.name).limit(limit)).scalars())


def resolve_product(ctx: ToolContext, ref: str) -> Product:
    matches = find_products(ctx, ref, limit=10)
    if not matches:
        raise NotFound(f"No product matches '{ref}'")
    if len(matches) > 1:
        raise ToolError(f"'{ref}' matches {len(matches)} products; ask the user which one",
                        details={"candidates": [{"sku": p.sku, "name": p.name} for p in matches]})
    return matches[0]


def resolve_warehouse(ctx: ToolContext, ref: str | None) -> Warehouse | None:
    if not ref:
        return None
    ref = ref.strip()
    wh = ctx.db.execute(select(Warehouse).where(or_(func.lower(Warehouse.code) == ref.lower(),
                                                    func.lower(Warehouse.name) == ref.lower()))).scalar_one_or_none()
    if wh is None:
        wh = ctx.db.execute(select(Warehouse).where(func.lower(Warehouse.name).like(f"%{ref.lower()}%"))).scalars().first()
    if wh is None or not ctx.actor.can_access_warehouse(wh.id):
        raise NotFound(f"No accessible warehouse matches '{ref}'")
    return wh


def visible_warehouses(ctx: ToolContext, wh: Warehouse | None) -> frozenset[uuid.UUID] | None:
    return frozenset([wh.id]) if wh else ctx.actor.warehouse_ids


# ---------------------------------------------------------------- read tools
class SearchProductsIn(ToolInput):
    query: str = Field(min_length=1, max_length=100, description="Product name, SKU, internal code, barcode or keyword")
    product_type: ProductType | None = Field(None, description="Optional product type filter")
    limit: int = Field(10, ge=1, le=25)


@tool("search_products", "Search the product master by name, SKU, code, barcode or keyword. Returns matching products "
      "with their current available stock. Use this to identify products before other calls.", SearchProductsIn, "inventory.view")
def search_products(ctx: ToolContext, a: SearchProductsIn) -> ToolOutput:
    products = [p for p in find_products(ctx, a.query, a.limit) if not a.product_type or p.product_type == a.product_type]
    sums = stock_summaries(ctx.db, [p.id for p in products], ctx.actor.warehouse_ids)
    rows = []
    for p in products:
        s = summary_for(p, sums.get(p.id))
        rows.append({"sku": p.sku, "name": p.name, "type": p.product_type.value, "uom": p.uom.code,
                     "available": fmt(s["available"]), "status": s["status"], "active": p.is_active})
    summary = f"Found {len(rows)} product(s) matching '{a.query}'." if rows else f"No products match '{a.query}'."
    return ToolOutput({"products": rows}, summary, table(f"Products matching “{a.query}”", [
        ("sku", "SKU"), ("name", "Product"), ("type", "Type"), ("available", "Available"), ("uom", "UoM"), ("status", "Status")],
        rows, "verified", [src("product", p.id, p.sku) for p in products]) if rows else None)


class ProductRefIn(ToolInput):
    product: str = Field(min_length=1, max_length=100, description="SKU, product code, barcode or product name")


@tool("get_product_details", "Get master data for one product (type, unit, costs, stock levels, reorder settings, suppliers) "
      "plus its current stock summary.", ProductRefIn, "inventory.view")
def get_product_details(ctx: ToolContext, a: ProductRefIn) -> ToolOutput:
    p = resolve_product(ctx, a.product)
    s = summary_for(p, stock_summaries(ctx.db, [p.id], ctx.actor.warehouse_ids).get(p.id))
    sups = ctx.db.execute(select(ProductSupplier).where(ProductSupplier.product_id == p.id)).scalars().all()
    data = {"sku": p.sku, "name": p.name, "type": p.product_type.value, "uom": p.uom.code,
            "category": p.category.name if p.category else None, "manufacturer": p.manufacturer,
            "purchase_price": fmt(p.purchase_price), "standard_cost": fmt(p.standard_cost),
            "selling_price": fmt(p.selling_price) if p.selling_price is not None else None,
            "min_stock_level": fmt(p.min_stock_level), "reorder_level": fmt(p.reorder_level),
            "reorder_quantity": fmt(p.reorder_quantity), "max_stock_level": fmt(p.max_stock_level),
            "track_batches": p.track_batches, "track_serials": p.track_serials, "active": p.is_active,
            "stock": {k: fmt(v) if isinstance(v, Decimal) else v for k, v in s.items()},
            "suppliers": [{"code": x.supplier.code, "name": x.supplier.name, "preferred": x.is_preferred,
                           "last_purchase_price": fmt(x.last_purchase_price), "lead_time_days": x.lead_time_days} for x in sups]}
    card = metrics(f"{p.name} · {p.sku}", [
        {"label": "Available", "value": fmt(s["available"]), "unit": p.uom.code},
        {"label": "Physical", "value": fmt(s["physical"]), "unit": p.uom.code},
        {"label": "Reserved", "value": fmt(s["reserved"]), "unit": p.uom.code},
        {"label": "Reorder level", "value": fmt(p.reorder_level), "unit": p.uom.code},
        {"label": "Avg cost", "value": money(s["average_cost"])}, {"label": "Status", "value": s["status"]}],
        "verified", [src("product", p.id, p.sku)])
    return ToolOutput(data, f"{p.name} ({p.sku}): {fmt(s['available'])} {p.uom.code} available, status {s['status']}.", card)


class StockLevelIn(ToolInput):
    product: str = Field(min_length=1, max_length=100, description="SKU, code or name; a generic name like 'steel' returns all matching products")
    warehouse: str | None = Field(None, max_length=100, description="Optional warehouse code or name")


@tool("get_stock_level", "Get current stock (physical, available, reserved, in transit, damaged) for a product or for every "
      "product matching a name, broken down by warehouse and storage location.", StockLevelIn, "inventory.view")
def get_stock_level(ctx: ToolContext, a: StockLevelIn) -> ToolOutput:
    wh = resolve_warehouse(ctx, a.warehouse)
    products = find_products(ctx, a.product, limit=20)
    if not products:
        raise NotFound(f"No product matches '{a.product}'")
    scope = visible_warehouses(ctx, wh)
    B = InventoryBalance
    stmt = select(B).where(B.product_id.in_([p.id for p in products]), B.qty_on_hand != 0)
    if scope is not None:
        stmt = stmt.where(B.warehouse_id.in_(scope))
    balances = ctx.db.execute(stmt).unique().scalars().all()
    sums = stock_summaries(ctx.db, [p.id for p in products], scope)
    rows, per_product, totals_by_uom = [], [], {}
    for b in sorted(balances, key=lambda x: (x.product.name, x.warehouse.code, x.location.path)):
        rows.append({"sku": b.product.sku, "product": b.product.name, "warehouse": b.warehouse.code,
                     "location": b.location.path, "batch": b.batch.batch_number if b.batch else "",
                     "status": b.stock_status.value, "on_hand": fmt(b.qty_on_hand), "reserved": fmt(b.qty_reserved),
                     "uom": b.product.uom.code})
    for p in products:
        s = summary_for(p, sums.get(p.id))
        per_product.append({"sku": p.sku, "name": p.name, "uom": p.uom.code, **{k: fmt(v) if isinstance(v, Decimal) else v
                                                                               for k, v in s.items() if k != "average_cost"}})
        t = totals_by_uom.setdefault(p.uom.code, {"physical": ZERO, "available": ZERO})
        t["physical"] += s["physical"]
        t["available"] += s["available"]
    totals = [{"uom": u, "physical": fmt(t["physical"]), "available": fmt(t["available"])} for u, t in totals_by_uom.items()]
    scope_label = wh.code if wh else ("your authorised warehouses" if ctx.actor.warehouse_ids is not None else "all warehouses")
    parts = "; ".join(f"{x['name']} ({x['sku']}): {x['available']} {x['uom']} available of {x['physical']} physical"
                      for x in per_product[:8])
    summary = f"Stock for '{a.product}' in {scope_label}: {parts}." + (" (more products matched)" if len(per_product) > 8 else "")
    cards_rows = [{"sku": x["sku"], "product": x["name"], "physical": x["physical"], "available": x["available"],
                   "reserved": x["reserved"], "in_transit": x["in_transit"], "uom": x["uom"], "status": x["status"]} for x in per_product]
    card = table(f"Stock — {a.product} ({scope_label})", [("sku", "SKU"), ("product", "Product"), ("physical", "Physical"),
                 ("available", "Available"), ("reserved", "Reserved"), ("in_transit", "In transit"), ("uom", "UoM"),
                 ("status", "Status")], cards_rows, "verified", [src("product", p.id, p.sku) for p in products])
    return ToolOutput({"scope": scope_label, "products": per_product, "totals_by_uom": totals, "locations": rows[:100]}, summary, card)


class WarehouseStockIn(ToolInput):
    warehouse: str = Field(min_length=1, max_length=100, description="Warehouse code or name")
    only_low: bool = Field(False, description="Only products at or below reorder level")
    limit: int = Field(50, ge=1, le=100)


@tool("get_warehouse_stock", "List the stock held in one warehouse by product, with value at weighted-average cost.",
      WarehouseStockIn, "inventory.view")
def get_warehouse_stock(ctx: ToolContext, a: WarehouseStockIn) -> ToolOutput:
    wh = resolve_warehouse(ctx, a.warehouse)
    assert wh is not None
    pids = ctx.db.execute(select(InventoryBalance.product_id).where(InventoryBalance.warehouse_id == wh.id,
                                                                     InventoryBalance.qty_on_hand != 0).distinct()).scalars().all()
    products = {p.id: p for p in ctx.db.execute(select(Product).where(Product.id.in_(pids))).scalars()} if pids else {}
    sums = stock_summaries(ctx.db, list(products), frozenset([wh.id]))
    rows, total_value = [], ZERO
    for pid, p in sorted(products.items(), key=lambda kv: kv[1].name):
        s = summary_for(p, sums.get(pid))
        if a.only_low and s["status"] not in ("low", "critical", "out"):
            continue
        total_value += s["value"]
        rows.append({"sku": p.sku, "product": p.name, "physical": fmt(s["physical"]), "available": fmt(s["available"]),
                     "uom": p.uom.code, "value": money(s["value"]), "status": s["status"]})
    rows = rows[:a.limit]
    summary = f"{wh.name} ({wh.code}) holds {len(products)} products with stock; total value {money(total_value)}."
    return ToolOutput({"warehouse": wh.code, "total_value": money(total_value), "products": rows}, summary,
                      table(f"{wh.name} ({wh.code})", [("sku", "SKU"), ("product", "Product"), ("physical", "Physical"),
                            ("available", "Available"), ("uom", "UoM"), ("value", "Value"), ("status", "Status")], rows,
                            "verified", [src("warehouse", wh.id, wh.code)]))


class LowStockIn(ToolInput):
    warehouse: str | None = Field(None, max_length=100, description="Optional warehouse code or name")
    limit: int = Field(50, ge=1, le=100)


@tool("get_low_stock_items", "List products whose AVAILABLE stock is at or below their reorder level or minimum level "
      "(including out of stock), with shortfall and configured reorder quantity.", LowStockIn, "inventory.view")
def get_low_stock_items(ctx: ToolContext, a: LowStockIn) -> ToolOutput:
    wh = resolve_warehouse(ctx, a.warehouse)
    alerts = [x for x in compute_alerts(ctx.db, visible_warehouses(ctx, wh), {"low_stock", "critical_stock"})][:a.limit]
    rows = [{"sku": x["data"]["sku"], "product": x["title"].split(" is ")[0], "available": x["data"]["available"],
             "uom": x["data"]["uom"], "reorder_level": x["data"]["reorder_level"], "min_level": x["data"]["min_stock_level"],
             "reorder_qty": x["data"]["reorder_quantity"], "severity": x["severity"]} for x in alerts]
    summary = (f"{len(rows)} product(s) are at or below reorder level: " + ", ".join(f"{r['product']} ({r['available']} {r['uom']})"
               for r in rows[:10]) + ".") if rows else "No products are below their reorder or minimum level."
    return ToolOutput({"items": rows}, summary, table("Items needing reorder", [("sku", "SKU"), ("product", "Product"),
                      ("available", "Available"), ("uom", "UoM"), ("reorder_level", "Reorder at"), ("min_level", "Min"),
                      ("reorder_qty", "Reorder qty"), ("severity", "Severity")], rows, "calculated",
                      [src("product", x["entity_id"], x["data"]["sku"]) for x in alerts],
                      note="Calculated from available stock vs. configured reorder/minimum levels."))


class MovementHistoryIn(ToolInput):
    product: str | None = Field(None, max_length=100)
    warehouse: str | None = Field(None, max_length=100)
    movement_type: MovementType | None = None
    days: int = Field(7, ge=1, le=365, description="Look-back window in days")
    limit: int = Field(25, ge=1, le=100)


@tool("get_stock_movement_history", "Read the immutable stock movement ledger with optional product, warehouse, movement type "
      "and look-back window.", MovementHistoryIn, "inventory.view")
def get_stock_movement_history(ctx: ToolContext, a: MovementHistoryIn) -> ToolOutput:
    from app.modules.inventory.router import movement_rows
    M = StockMovement
    stmt = select(M).where(M.created_at >= utcnow() - timedelta(days=a.days))
    product = resolve_product(ctx, a.product) if a.product else None
    wh = resolve_warehouse(ctx, a.warehouse)
    if product:
        stmt = stmt.where(M.product_id == product.id)
    scope = visible_warehouses(ctx, wh)
    if scope is not None:
        stmt = stmt.where(M.warehouse_id.in_(scope))
    if a.movement_type:
        stmt = stmt.where(M.movement_type == a.movement_type)
    moves = ctx.db.execute(stmt.order_by(M.created_at.desc(), M.line_no).limit(a.limit)).unique().scalars().all()
    rows = [{"date": r["created_at"].astimezone(ctx.tz).strftime("%Y-%m-%d %H:%M"), "txn": r["transaction_number"],
             "type": r["movement_type"].value, "sku": r["product_sku"], "warehouse": r["warehouse_code"],
             "location": r["location_path"], "qty": fmt(r["quantity"]), "uom": r["uom_code"], "balance": fmt(r["balance_after"]),
             "reference": r["reference_number"] or "", "user": r["performed_by_name"] or ""} for r in movement_rows(ctx.db, list(moves))]
    summary = f"{len(rows)} ledger line(s) in the last {a.days} day(s)" + (f" for {product.sku}" if product else "") + "."
    return ToolOutput({"movements": rows}, summary, table("Stock movements", [("date", "Date"), ("txn", "Txn"), ("type", "Type"),
                      ("sku", "SKU"), ("warehouse", "WH"), ("qty", "Qty"), ("uom", "UoM"), ("balance", "Balance"),
                      ("reference", "Ref"), ("user", "By")], rows, "verified",
                      [src("stock_movement", m.id, m.transaction.transaction_number, m.created_at) for m in moves]))


class ReceiptsIn(ToolInput):
    date_from: date | None = Field(None, description="Start date (company timezone). Defaults to today.")
    date_to: date | None = Field(None, description="End date inclusive. Defaults to date_from.")


@tool("get_recent_stock_receipts", "List CONFIRMED goods receipts (GRNs) and other stock-in in a date range (default: today "
      "in the company timezone) with products, quantities, suppliers and PO references.", ReceiptsIn, "inventory.view")
def get_recent_stock_receipts(ctx: ToolContext, a: ReceiptsIn) -> ToolOutput:
    d_from = a.date_from or ctx.now().date()
    d_to = a.date_to or d_from
    if d_to < d_from:
        raise ToolError("date_to must be on or after date_from")
    start = datetime.combine(d_from, time.min, ctx.tz)
    end = datetime.combine(d_to + timedelta(days=1), time.min, ctx.tz)
    M = StockMovement
    stmt = select(M).where(M.created_at >= start, M.created_at < end, M.quantity > 0,
                           M.movement_type.in_([MovementType.RECEIPT, MovementType.STOCK_IN]))
    if ctx.actor.warehouse_ids is not None:
        stmt = stmt.where(M.warehouse_id.in_(ctx.actor.warehouse_ids))
    moves = ctx.db.execute(stmt.order_by(M.created_at)).unique().scalars().all()
    grn_ids = {m.transaction.reference_id for m in moves if m.transaction.reference_type == "goods_receipt"}
    grns = {g.id: g for g in ctx.db.execute(select(GoodsReceipt).where(GoodsReceipt.id.in_(grn_ids))).unique().scalars()} if grn_ids else {}
    rows = []
    for m in moves:
        g = grns.get(m.transaction.reference_id)
        rows.append({"time": m.created_at.astimezone(ctx.tz).strftime("%Y-%m-%d %H:%M"), "sku": m.product.sku,
                     "product": m.product.name, "qty": fmt(m.quantity), "uom": m.product.uom.code, "status": m.stock_status.value,
                     "supplier": g.supplier.name if g else "", "grn": g.grn_number if g else m.transaction.transaction_number,
                     "po": g.purchase_order.po_number if g else "", "value": money(m.value)})
    period = d_from.isoformat() if d_from == d_to else f"{d_from.isoformat()} to {d_to.isoformat()}"
    summary = (f"{len(rows)} receipt line(s) on {period}: " + "; ".join(f"{r['product']} {r['qty']} {r['uom']}"
               + (f" from {r['supplier']} ({r['po']})" if r["supplier"] else "") for r in rows[:10])) if rows else \
        f"No stock was received on {period}."
    return ToolOutput({"period": period, "receipts": rows}, summary, table(f"Stock received · {period}", [("time", "Time"),
                      ("product", "Product"), ("qty", "Qty"), ("uom", "UoM"), ("status", "Status"), ("supplier", "Supplier"),
                      ("grn", "GRN / Txn"), ("po", "PO")], rows, "verified",
                      [src("goods_receipt", g.id, g.grn_number, g.confirmed_at) for g in grns.values()]))


class PendingReceiptsIn(ToolInput):
    supplier: str | None = Field(None, max_length=100, description="Optional supplier code or name")
    overdue_only: bool = Field(False, description="Only POs past their expected delivery date")
    limit: int = Field(50, ge=1, le=100)


@tool("get_pending_goods_receipts", "List purchase order lines still awaiting delivery (approved/sent/partially received), "
      "with outstanding quantity, expected date and overdue flag.", PendingReceiptsIn, "purchasing.view")
def get_pending_goods_receipts(ctx: ToolContext, a: PendingReceiptsIn) -> ToolOutput:
    today = ctx.now().date()
    stmt = (select(PurchaseOrder).where(PurchaseOrder.status.in_([POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED]))
            .order_by(PurchaseOrder.expected_delivery_date.asc().nulls_last()))
    if ctx.actor.warehouse_ids is not None:
        stmt = stmt.where(PurchaseOrder.delivery_warehouse_id.in_(ctx.actor.warehouse_ids))
    if a.supplier:
        stmt = stmt.join(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
            or_(func.lower(Supplier.code) == a.supplier.lower(), Supplier.name.ilike(f"%{a.supplier}%")))
    if a.overdue_only:
        stmt = stmt.where(PurchaseOrder.expected_delivery_date < today)
    pos = ctx.db.execute(stmt).unique().scalars().all()
    rows = []
    for po in pos:
        overdue = bool(po.expected_delivery_date and po.expected_delivery_date < today)
        for i in po.items:
            if i.quantity_outstanding > 0:
                rows.append({"po": po.po_number, "supplier": po.supplier.name, "sku": i.product.sku, "product": i.product.name,
                             "ordered": fmt(i.quantity_ordered), "received": fmt(i.quantity_received),
                             "outstanding": fmt(i.quantity_outstanding), "uom": i.product.uom.code,
                             "expected": po.expected_delivery_date.isoformat() if po.expected_delivery_date else "",
                             "overdue": "yes" if overdue else "no", "status": po.status.value})
    rows = rows[:a.limit]
    n_over = len({r["po"] for r in rows if r["overdue"] == "yes"})
    summary = (f"{len({r['po'] for r in rows})} purchase order(s) awaiting delivery ({n_over} overdue)." if rows
               else "No purchase orders are awaiting delivery" + (" past their expected date." if a.overdue_only else "."))
    return ToolOutput({"lines": rows, "overdue_po_count": n_over}, summary, table(
        "Overdue deliveries" if a.overdue_only else "Pending goods receipts", [("po", "PO"), ("supplier", "Supplier"),
        ("product", "Product"), ("outstanding", "Outstanding"), ("uom", "UoM"), ("expected", "Expected"), ("overdue", "Overdue"),
        ("status", "Status")], rows, "verified", [src("purchase_order", po.id, po.po_number) for po in pos]))


class POStatusIn(ToolInput):
    po_number: str = Field(min_length=1, max_length=40, description="Purchase order number, e.g. PO-000123")


@tool("get_purchase_order_status", "Get the status of one purchase order: lines, ordered/received/outstanding quantities, "
      "dates and its goods receipts.", POStatusIn, "purchasing.view")
def get_purchase_order_status(ctx: ToolContext, a: POStatusIn) -> ToolOutput:
    ref = a.po_number.strip().upper()
    if ref.isdigit():
        ref = f"PO-{ref.zfill(6)}"
    po = ctx.db.execute(select(PurchaseOrder).where(func.upper(PurchaseOrder.po_number) == ref)).unique().scalar_one_or_none()
    if po is None or not ctx.actor.can_access_warehouse(po.delivery_warehouse_id):
        raise NotFound(f"Purchase order {a.po_number} not found")
    grns = ctx.db.execute(select(GoodsReceipt).where(GoodsReceipt.purchase_order_id == po.id)).unique().scalars().all()
    lines = [{"sku": i.product.sku, "product": i.product.name, "ordered": fmt(i.quantity_ordered), "received": fmt(i.quantity_received),
              "outstanding": fmt(i.quantity_outstanding), "uom": i.product.uom.code, "unit_price": fmt(i.unit_price)} for i in po.items]
    data = {"po_number": po.po_number, "status": po.status.value, "supplier": po.supplier.name, "order_date": po.order_date.isoformat(),
            "expected_delivery_date": po.expected_delivery_date.isoformat() if po.expected_delivery_date else None,
            "total": money(po.total_amount), "currency": po.currency_code, "lines": lines,
            "receipts": [{"grn": g.grn_number, "status": g.status.value, "received_date": g.received_date.isoformat()} for g in grns]}
    summary = f"{po.po_number} ({po.supplier.name}) is {po.status.value.replace('_', ' ')}; total {po.currency_code} {money(po.total_amount)}."
    return ToolOutput(data, summary, table(f"{po.po_number} · {po.supplier.name} · {po.status.value.replace('_', ' ')}",
                      [("sku", "SKU"), ("product", "Product"), ("ordered", "Ordered"), ("received", "Received"),
                       ("outstanding", "Outstanding"), ("uom", "UoM")], lines, "verified", [src("purchase_order", po.id, po.po_number)]))


class ValuationIn(ToolInput):
    group_by: Literal["product", "warehouse", "category"] = "warehouse"
    limit: int = Field(25, ge=1, le=100)


@tool("get_inventory_valuation", "Calculate inventory value at weighted-average cost, grouped by warehouse, category or product. "
      "Uses purchase cost, never selling price.", ValuationIn, "reports.view")
def get_inventory_valuation(ctx: ToolContext, a: ValuationIn) -> ToolOutput:
    from app.modules.erp_core.service import get_company
    v = compute_valuation(ctx.db, ctx.actor.warehouse_ids, a.group_by)
    company = get_company(ctx.db)
    cur = company.currency_code if company else ""
    label = {"warehouse": "warehouse_code", "category": "category_name", "product": "name"}[a.group_by]
    rows = [{"group": r.get(label) or "—", "value": money(r["value"]), **({"qty": fmt(r["quantity"]), "uom": r.get("uom") or ""}
             if a.group_by == "product" else {})} for r in v["rows"][:a.limit]]
    cols = [("group", a.group_by.title()), ("value", f"Value ({cur})")] + ([("qty", "Qty"), ("uom", "UoM")] if a.group_by == "product" else [])
    summary = f"Total inventory value is {cur} {money(v['total_value'])} (weighted average cost, {len(v['rows'])} {a.group_by} group(s))."
    return ToolOutput({"currency": cur, "total_value": money(v["total_value"]), "method": v["method"], "groups": rows}, summary,
                      table(f"Inventory valuation · {cur} {money(v['total_value'])}", cols, rows, "calculated",
                            note="Weighted-average cost × owned quantity (incl. in transit)."))


AlertType = Literal["low_stock", "critical_stock", "overstock", "expiring_stock", "pending_receipt", "unconfirmed_transfer",
                    "stock_discrepancy", "inactive_stock"]


class AlertsIn(ToolInput):
    types: list[AlertType] | None = Field(None, description="Optional subset of alert types")
    limit: int = Field(30, ge=1, le=100)


@tool("get_inventory_alerts", "Get the current inventory alerts: low/critical stock, overstock, expiring batches, overdue "
      "deliveries, unconfirmed transfers, count discrepancies and inactive stock. Use for 'what needs attention'.",
      AlertsIn, "inventory.view")
def get_inventory_alerts(ctx: ToolContext, a: AlertsIn) -> ToolOutput:
    alerts = compute_alerts(ctx.db, ctx.actor.warehouse_ids, set(a.types) if a.types else None)
    counts: dict[str, int] = {}
    for x in alerts:
        counts[x["type"]] = counts.get(x["type"], 0) + 1
    rows = [{"severity": x["severity"], "type": x["type"].replace("_", " "), "title": x["title"], "detail": x["detail"]}
            for x in alerts[:a.limit]]
    from app.models import ActionApproval, ApprovalStatus
    pending = ctx.db.execute(select(func.count()).select_from(ActionApproval).where(
        ActionApproval.status == ApprovalStatus.PENDING)).scalar_one() if ctx.actor.has("approvals.view") else None
    summary = (f"{len(alerts)} active alert(s): " + ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in counts.items()) + "."
               if alerts else "There are no active inventory alerts.")
    if pending:
        summary += f" {pending} action approval(s) are pending."
    return ToolOutput({"counts": counts, "alerts": rows, "pending_approvals": pending}, summary, table(
        "Inventory alerts", [("severity", "Severity"), ("type", "Type"), ("title", "Alert"), ("detail", "Detail")], rows,
        "calculated", [src(x["entity_type"], x["entity_id"], x["title"]) for x in alerts[:a.limit]]))


class ReportIn(ToolInput):
    report_id: Literal[tuple(REPORTS)]  # type: ignore[valid-type]
    date_from: date | None = None
    date_to: date | None = None
    product: str | None = Field(None, max_length=100)
    warehouse: str | None = Field(None, max_length=100)
    limit: int = Field(25, ge=1, le=100)


@tool("generate_inventory_report", "Run one of the standard inventory reports (" + ", ".join(REPORTS) + ") with optional "
      "filters. Returns the first rows and the total row count.", ReportIn, "reports.view")
def generate_inventory_report(ctx: ToolContext, a: ReportIn) -> ToolOutput:
    product = resolve_product(ctx, a.product) if a.product else None
    wh = resolve_warehouse(ctx, a.warehouse)
    report, rows = run_report(ctx.db, ctx.actor, a.report_id, Filters(a.date_from, a.date_to, product.id if product else None,
                                                                      wh.id if wh else None))
    shown = [{k: fmt(r.get(k)) if isinstance(r.get(k), Decimal) else (r.get(k).isoformat() if hasattr(r.get(k), "isoformat")
              else r.get(k)) for k, _ in report.columns} for r in rows[:a.limit]]
    summary = f"Report '{report.title}' returned {len(rows)} row(s)" + (f"; showing {len(shown)}." if len(rows) > len(shown) else ".")
    return ToolOutput({"report": report.id, "total_rows": len(rows), "rows": shown}, summary,
                      table(report.title, report.columns, shown, "verified",
                            note=f"{len(rows)} rows total. Full export: Reports → {report.title} → CSV."))


# ---------------------------------------------------------------- draft (approval-gated) tools
class DraftItem(ToolInput):
    product: str = Field(min_length=1, max_length=100, description="SKU or exact product name")
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


class TransferDraftIn(ToolInput):
    source_warehouse: str = Field(min_length=1, max_length=100)
    destination_warehouse: str = Field(min_length=1, max_length=100)
    items: list[DraftItem] = Field(min_length=1, max_length=20)
    notes: str | None = Field(None, max_length=500)


def _approval_card(appr, title: str, rows: list[dict], columns: list[tuple[str, str]], note: str) -> dict[str, Any]:
    return {"type": "approval", "title": title, "provenance": "draft", "approval_id": str(appr.id), "status": appr.status.value,
            "action_type": appr.action_type, "summary": appr.summary, "expires_at": appr.expires_at.isoformat(),
            "columns": [{"key": k, "label": l} for k, l in columns], "rows": rows, "note": note,
            "sources": [], "as_of": utcnow().isoformat()}


@tool("create_stock_transfer_draft", "PREPARE (not execute) a stock transfer between two warehouses. Checks available stock "
      "at the source and creates a pending approval; nothing moves until a user approves.", TransferDraftIn, "inventory.transfer", "draft")
def create_stock_transfer_draft(ctx: ToolContext, a: TransferDraftIn) -> ToolOutput:
    src_wh, dst_wh = resolve_warehouse(ctx, a.source_warehouse), resolve_warehouse(ctx, a.destination_warehouse)
    assert src_wh and dst_wh
    if src_wh.id == dst_wh.id:
        raise ToolError("Source and destination warehouses must differ")
    items, rows = [], []
    for it in a.items:
        p = resolve_product(ctx, it.product)
        avail = stock_summaries(ctx.db, [p.id], frozenset([src_wh.id])).get(p.id, {}).get("available", ZERO)
        if avail < it.quantity:
            raise ToolError(f"Only {fmt(avail)} {p.uom.code} of {p.sku} is available at {src_wh.code}",
                            details={"sku": p.sku, "available": fmt(avail)})
        items.append({"product_id": str(p.id), "quantity": format(it.quantity, "f")})
        rows.append({"sku": p.sku, "product": p.name, "quantity": fmt(it.quantity), "uom": p.uom.code, "available_at_source": fmt(avail)})
    params = {"source_warehouse_id": str(src_wh.id), "destination_warehouse_id": str(dst_wh.id), "items": items, "notes": a.notes}
    summary = f"Create stock transfer {src_wh.code} → {dst_wh.code}: " + ", ".join(f"{r['quantity']} {r['uom']} {r['sku']}" for r in rows)
    appr = approval_manager.create(ctx.db, ctx.actor, "inventory.create_stock_transfer", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval", "proposal": rows},
                      f"Prepared a transfer draft ({summary}). It is awaiting approval; nothing has moved yet.",
                      _approval_card(appr, f"Proposed transfer · {src_wh.code} → {dst_wh.code}", rows,
                                     [("sku", "SKU"), ("product", "Product"), ("quantity", "Qty"), ("uom", "UoM"),
                                      ("available_at_source", "Available at source")],
                                     "Approving creates a draft transfer document; it still follows the transfer workflow."))


class AdjustmentDraftItem(ToolInput):
    product: str = Field(min_length=1, max_length=100)
    quantity_delta: Decimal = Field(max_digits=18, decimal_places=4, description="Positive to add, negative to remove")
    location: str | None = Field(None, max_length=100, description="Location path; defaults to the warehouse default location")


class AdjustmentDraftIn(ToolInput):
    warehouse: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)
    items: list[AdjustmentDraftItem] = Field(min_length=1, max_length=20)


@tool("create_stock_adjustment_draft", "PREPARE (not execute) a stock adjustment. Creates a pending approval; once approved it "
      "becomes an adjustment that still requires adjustment approval before stock changes.", AdjustmentDraftIn, "inventory.adjust", "draft")
def create_stock_adjustment_draft(ctx: ToolContext, a: AdjustmentDraftIn) -> ToolOutput:
    from app.modules.inventory.service import default_location
    wh = resolve_warehouse(ctx, a.warehouse)
    assert wh
    items, rows = [], []
    for it in a.items:
        if it.quantity_delta == 0:
            raise ToolError("quantity_delta must not be zero")
        p = resolve_product(ctx, it.product)
        if p.track_batches or p.track_serials:
            raise ToolError(f"{p.sku} is batch/serial tracked; adjust it from the Stock Adjustments screen")
        if it.location:
            loc = ctx.db.execute(select(StorageLocation).where(StorageLocation.warehouse_id == wh.id,
                                                               func.upper(StorageLocation.path) == it.location.upper())).scalar_one_or_none()
            if loc is None or loc.location_type == LocationType.TRANSIT:
                raise NotFound(f"Location '{it.location}' not found in {wh.code}")
        else:
            loc = default_location(ctx.db, wh.id)
        on_hand = ctx.db.execute(select(func.coalesce(func.sum(InventoryBalance.qty_on_hand), 0)).where(
            InventoryBalance.product_id == p.id, InventoryBalance.location_id == loc.id,
            InventoryBalance.stock_status == StockStatus.UNRESTRICTED)).scalar_one()
        if on_hand + it.quantity_delta < 0:
            raise ToolError(f"{p.sku} has only {fmt(on_hand)} {p.uom.code} at {wh.code}/{loc.path}")
        items.append({"product_id": str(p.id), "location_id": str(loc.id), "quantity_delta": format(it.quantity_delta, "f")})
        rows.append({"sku": p.sku, "product": p.name, "location": loc.path, "current": fmt(on_hand), "delta": fmt(it.quantity_delta),
                     "after": fmt(on_hand + it.quantity_delta), "uom": p.uom.code})
    params = {"warehouse_id": str(wh.id), "reason": a.reason, "items": items}
    summary = f"Create stock adjustment at {wh.code} ({a.reason}): " + ", ".join(f"{r['delta']} {r['uom']} {r['sku']}" for r in rows)
    appr = approval_manager.create(ctx.db, ctx.actor, "inventory.create_stock_adjustment", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval", "proposal": rows},
                      f"Prepared an adjustment draft ({summary}). Awaiting approval; stock is unchanged.",
                      _approval_card(appr, f"Proposed adjustment · {wh.code}", rows, [("sku", "SKU"), ("location", "Location"),
                                     ("current", "Current"), ("delta", "Change"), ("after", "After"), ("uom", "UoM")],
                                     "Approving creates an adjustment awaiting adjustment approval before stock changes."))


class RequisitionDraftItem(ToolInput):
    product: str = Field(min_length=1, max_length=100)
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    preferred_supplier: str | None = Field(None, max_length=100, description="Supplier code or name (optional)")


class RequisitionDraftIn(ToolInput):
    items: list[RequisitionDraftItem] = Field(min_length=1, max_length=20)
    justification: str = Field(min_length=3, max_length=1000)
    required_date: date | None = None


@tool("create_purchase_requisition_draft", "PREPARE (not execute) a purchase requisition. Gathers current stock, open PO "
      "quantities and supplier information for each item and creates a pending approval. Nothing is created until approved.",
      RequisitionDraftIn, "purchasing.requisition.create", "draft")
def create_purchase_requisition_draft(ctx: ToolContext, a: RequisitionDraftIn) -> ToolOutput:
    items, rows = [], []
    for it in a.items:
        p = resolve_product(ctx, it.product)
        s = summary_for(p, stock_summaries(ctx.db, [p.id], ctx.actor.warehouse_ids).get(p.id))
        on_order = ctx.db.execute(select(func.coalesce(func.sum(PurchaseOrderItem.quantity_ordered - PurchaseOrderItem.quantity_received), 0))
                                  .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.purchase_order_id)
                                  .where(PurchaseOrderItem.product_id == p.id, PurchaseOrder.status.in_(
                                      [POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED]))).scalar_one()
        supplier = None
        if it.preferred_supplier:
            supplier = ctx.db.execute(select(Supplier).where(or_(func.lower(Supplier.code) == it.preferred_supplier.lower(),
                                                                 Supplier.name.ilike(f"%{it.preferred_supplier}%")))).scalars().first()
            if supplier is None:
                raise NotFound(f"Supplier '{it.preferred_supplier}' not found")
        else:
            ps = ctx.db.execute(select(ProductSupplier).where(ProductSupplier.product_id == p.id)
                                .order_by(ProductSupplier.is_preferred.desc())).scalars().first()
            supplier = ps.supplier if ps else None
        ps = ctx.db.execute(select(ProductSupplier).where(ProductSupplier.product_id == p.id,
                                                          ProductSupplier.supplier_id == supplier.id)).scalar_one_or_none() if supplier else None
        est = ps.last_purchase_price if ps and ps.last_purchase_price is not None else p.purchase_price
        items.append({"product_id": str(p.id), "quantity": format(it.quantity, "f"),
                      "preferred_supplier_id": str(supplier.id) if supplier else None, "estimated_unit_cost": format(est, "f")})
        rows.append({"sku": p.sku, "product": p.name, "quantity": fmt(it.quantity), "uom": p.uom.code,
                     "available_now": fmt(s["available"]), "on_open_pos": fmt(on_order), "reorder_level": fmt(p.reorder_level),
                     "supplier": supplier.name if supplier else "not set", "est_unit_cost": money(est),
                     "est_total": money(est * it.quantity)})
    params = {"items": items, "justification": a.justification,
              "required_date": a.required_date.isoformat() if a.required_date else None}
    summary = "Create purchase requisition: " + ", ".join(f"{r['quantity']} {r['uom']} {r['sku']}" for r in rows)
    appr = approval_manager.create(ctx.db, ctx.actor, "purchasing.create_requisition", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval", "proposal": rows},
                      f"Prepared a requisition draft ({summary}). Context: " + "; ".join(
                          f"{r['sku']} available {r['available_now']} {r['uom']}, {r['on_open_pos']} on open POs" for r in rows)
                      + ". Awaiting approval; no requisition has been created yet.",
                      _approval_card(appr, "Proposed purchase requisition", rows, [("sku", "SKU"), ("quantity", "Qty"),
                                     ("uom", "UoM"), ("available_now", "Available"), ("on_open_pos", "On open POs"),
                                     ("supplier", "Supplier"), ("est_total", "Est. total")],
                                     "Approving creates the requisition and submits it for purchasing approval."))
