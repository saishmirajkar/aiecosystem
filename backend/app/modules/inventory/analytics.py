"""Dashboard overview and valuation — every number is computed from database records."""
from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.database import ZERO, utcnow
from app.models import (
    GoodsReceipt, GRNStatus, InventoryBalance, POStatus, Product, ProductCategory, ProductValuation, PurchaseOrder,
    StockMovement, Warehouse,
)
from app.modules.inventory.alerts import compute_alerts
from app.modules.inventory.queries import ON_SHELF

Q4 = Decimal("0.0001")


def valuation(db: Session, warehouse_ids: frozenset[uuid.UUID] | None = None, group_by: str = "product",
              category_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Weighted-average valuation. Quantity = physical + in-transit (owned stock) in the visible warehouses."""
    B, V, P = InventoryBalance, ProductValuation, Product
    qty = func.sum(B.qty_on_hand)
    value = func.sum(B.qty_on_hand * V.average_cost)
    base = (select().select_from(B).join(V, V.product_id == B.product_id).join(P, P.id == B.product_id))
    if warehouse_ids is not None:
        base = base.where(B.warehouse_id.in_(warehouse_ids))
    if warehouse_id:
        base = base.where(B.warehouse_id == warehouse_id)
    if category_id:
        base = base.where(P.category_id == category_id)
    rows: list[dict[str, Any]] = []
    if group_by == "warehouse":
        stmt = base.add_columns(Warehouse.id, Warehouse.code, Warehouse.name, qty, value).join(Warehouse, Warehouse.id == B.warehouse_id)\
            .group_by(Warehouse.id).order_by(value.desc())
        for wid, code, name, q, v in db.execute(stmt):
            rows.append({"warehouse_id": str(wid), "warehouse_code": code, "warehouse_name": name, "value": (v or ZERO).quantize(Q4)})
    elif group_by == "category":
        stmt = base.add_columns(ProductCategory.id, func.coalesce(ProductCategory.name, "Uncategorised"), value)\
            .outerjoin(ProductCategory, ProductCategory.id == P.category_id).group_by(ProductCategory.id, ProductCategory.name)\
            .order_by(value.desc())
        for cid, name, v in db.execute(stmt):
            rows.append({"category_id": str(cid) if cid else None, "category_name": name, "value": (v or ZERO).quantize(Q4)})
    else:
        stmt = base.add_columns(P.id, P.sku, P.name, V.average_cost, qty, value).group_by(P.id, V.average_cost)\
            .having(qty != 0).order_by(value.desc())
        for pid, sku, name, avg, q, v in db.execute(stmt):
            p = db.get(Product, pid)
            rows.append({"product_id": str(pid), "sku": sku, "name": name, "uom": p.uom.code if p else None,
                         "quantity": q.quantize(Q4), "average_cost": avg, "value": (v or ZERO).quantize(Q4)})
    total = sum((r["value"] for r in rows), ZERO)
    return {"method": "weighted_average", "group_by": group_by, "total_value": total.quantize(Q4), "rows": rows,
            "as_of": utcnow().isoformat()}


def overview(db: Session, warehouse_ids: frozenset[uuid.UUID] | None = None, days: int = 30) -> dict[str, Any]:
    B = InventoryBalance
    now = utcnow()
    wh_filter = (lambda stmt, col: stmt.where(col.in_(warehouse_ids)) if warehouse_ids is not None else stmt)

    active_products = db.execute(select(func.count()).select_from(Product).where(Product.is_active.is_(True))).scalar_one()
    wh_stmt = select(func.count()).select_from(Warehouse).where(Warehouse.is_active.is_(True))
    if warehouse_ids is not None:
        wh_stmt = wh_stmt.where(Warehouse.id.in_(warehouse_ids))
    warehouse_count = db.execute(wh_stmt).scalar_one()
    val = valuation(db, warehouse_ids, "warehouse")

    alerts = compute_alerts(db, warehouse_ids)
    low = [a for a in alerts if a["type"] in ("low_stock", "critical_stock")]

    pending_grn = db.execute(wh_filter(select(func.count()).select_from(GoodsReceipt).where(GoodsReceipt.status == GRNStatus.DRAFT),
                                       GoodsReceipt.warehouse_id)).scalar_one()
    open_po_statuses = [POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED]
    awaiting_delivery = db.execute(wh_filter(select(func.count()).select_from(PurchaseOrder)
                                             .where(PurchaseOrder.status.in_(open_po_statuses)), PurchaseOrder.delivery_warehouse_id)).scalar_one()
    pending_po = db.execute(wh_filter(select(func.count()).select_from(PurchaseOrder)
                                      .where(PurchaseOrder.status.in_([POStatus.DRAFT, POStatus.PENDING_APPROVAL])),
                                      PurchaseOrder.delivery_warehouse_id)).scalar_one()

    # Distribution by product type (value)
    dist_stmt = (select(Product.product_type, func.sum(B.qty_on_hand * ProductValuation.average_cost))
                 .select_from(B).join(Product, Product.id == B.product_id).join(ProductValuation, ProductValuation.product_id == B.product_id)
                 .where(B.stock_status.in_(ON_SHELF)).group_by(Product.product_type))
    distribution = [{"product_type": t.value, "value": (v or ZERO).quantize(Q4)} for t, v in db.execute(wh_filter(dist_stmt, B.warehouse_id))]

    # Daily movement trend (inbound vs outbound quantity value), excluding internal transfer/status lines
    since = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    day = func.date_trunc("day", StockMovement.created_at)
    trend_stmt = (select(day.label("day"),
                         func.sum(case((StockMovement.value > 0, StockMovement.value), else_=ZERO)),
                         func.sum(case((StockMovement.value < 0, -StockMovement.value), else_=ZERO)),
                         func.count())
                  .where(StockMovement.created_at >= since).group_by(day).order_by(day))
    by_day = {r[0].date(): r for r in db.execute(wh_filter(trend_stmt, StockMovement.warehouse_id))}
    trend = []
    for i in range(days):
        d = (since + timedelta(days=i)).date()
        r = by_day.get(d)
        trend.append({"date": d.isoformat(), "inbound_value": (r[1] if r else ZERO).quantize(Q4),
                      "outbound_value": (r[2] if r else ZERO).quantize(Q4), "movements": r[3] if r else 0})

    recent = db.execute(wh_filter(select(StockMovement).order_by(StockMovement.created_at.desc(), StockMovement.line_no), StockMovement.warehouse_id)
                        .limit(10)).unique().scalars().all()
    return {
        "as_of": now.isoformat(),
        "metrics": {
            "active_products": active_products, "total_stock_value": val["total_value"], "warehouse_count": warehouse_count,
            "low_stock_items": len(low), "pending_goods_receipts": pending_grn, "pos_awaiting_delivery": awaiting_delivery,
            "pending_purchase_orders": pending_po,
            "critical_alerts": sum(1 for a in alerts if a["severity"] == "critical"),
        },
        "value_by_warehouse": val["rows"],
        "distribution": distribution,
        "trend": trend,
        "recent_movements": recent,
        "critical_alerts": [a for a in alerts if a["severity"] in ("critical", "warning")][:8],
    }
