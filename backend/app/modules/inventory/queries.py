"""Inventory read model: one definition of physical / available / reserved / in-transit used everywhere."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.database import ZERO
from app.models import InventoryBalance, Product, ProductValuation, StockStatus

ON_SHELF = (StockStatus.UNRESTRICTED, StockStatus.DAMAGED, StockStatus.RETURNED)


def _sum_when(cond, col):
    return func.coalesce(func.sum(case((cond, col), else_=ZERO)), ZERO)


def balance_aggregates():
    """Column expressions over InventoryBalance for the standard stock figures."""
    B = InventoryBalance
    return {
        "physical": _sum_when(B.stock_status.in_(ON_SHELF), B.qty_on_hand),
        "available": _sum_when(B.stock_status == StockStatus.UNRESTRICTED, B.qty_on_hand - B.qty_reserved),
        "reserved": func.coalesce(func.sum(B.qty_reserved), ZERO),
        "in_transit": _sum_when(B.stock_status == StockStatus.IN_TRANSIT, B.qty_on_hand),
        "damaged": _sum_when(B.stock_status == StockStatus.DAMAGED, B.qty_on_hand),
        "returned": _sum_when(B.stock_status == StockStatus.RETURNED, B.qty_on_hand),
    }


def stock_summaries(db: Session, product_ids: list[uuid.UUID] | None = None,
                    warehouse_ids: frozenset[uuid.UUID] | None = None) -> dict[uuid.UUID, dict[str, Decimal]]:
    agg = balance_aggregates()
    stmt = select(InventoryBalance.product_id, *[v.label(k) for k, v in agg.items()]).group_by(InventoryBalance.product_id)
    if product_ids is not None:
        if not product_ids:
            return {}
        stmt = stmt.where(InventoryBalance.product_id.in_(product_ids))
    if warehouse_ids is not None:
        stmt = stmt.where(InventoryBalance.warehouse_id.in_(warehouse_ids))
    out = {row.product_id: {k: getattr(row, k) for k in agg} for row in db.execute(stmt)}
    vals = db.execute(select(ProductValuation).where(ProductValuation.product_id.in_(product_ids))) if product_ids else \
        db.execute(select(ProductValuation))
    for v in vals.scalars():
        s = out.setdefault(v.product_id, {k: ZERO for k in agg})
        s["average_cost"] = v.average_cost
        s["valued_quantity"] = v.quantity
    return out


def stock_level_status(product: Product, available: Decimal, physical: Decimal | None = None) -> str:
    """ok | out | critical | low | overstock — based on *available* stock vs. configured levels."""
    if available <= 0 and (product.reorder_level > 0 or product.min_stock_level > 0):
        return "out"
    if product.min_stock_level > 0 and available <= product.min_stock_level:
        return "critical"
    if product.reorder_level > 0 and available <= product.reorder_level:
        return "low"
    if product.max_stock_level is not None and (physical if physical is not None else available) > product.max_stock_level:
        return "overstock"
    return "ok"


def summary_for(product: Product, s: dict[str, Decimal] | None) -> dict[str, object]:
    s = s or {}
    physical = s.get("physical", ZERO)
    available = s.get("available", ZERO)
    avg = s.get("average_cost", ZERO)
    # Value shown per product = valued quantity × average cost (company-wide); for warehouse-scoped
    # users we value their visible physical + in-transit quantity at the same average cost.
    qty_for_value = physical + s.get("in_transit", ZERO)
    return {
        "physical": physical, "available": available, "reserved": s.get("reserved", ZERO),
        "in_transit": s.get("in_transit", ZERO), "damaged": s.get("damaged", ZERO),
        "average_cost": avg, "value": (qty_for_value * avg).quantize(Decimal("0.0001")),
        "status": stock_level_status(product, available, physical),
    }
