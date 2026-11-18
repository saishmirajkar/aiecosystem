"""Inventory alerts computed live from operational data (no stale copies)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, lazyload

from app.core.common import get_setting
from app.core.database import ZERO, utcnow
from app.models import (
    AdjustmentStatus, AdjustmentType, InventoryBalance, POStatus, Product, ProductBatch, PurchaseOrder, PurchaseOrderItem,
    StockAdjustment, StockAdjustmentItem, StockMovement, StockTransfer, TransferStatus, Warehouse,
)
from app.modules.inventory.queries import stock_summaries, stock_level_status

SEVERITY = {"critical_stock": "critical", "out_of_stock": "critical", "low_stock": "warning", "overstock": "info",
            "expiring_stock": "warning", "expired_stock": "critical", "pending_receipt": "warning",
            "unconfirmed_transfer": "warning", "stock_discrepancy": "warning", "inactive_stock": "info"}


def _alert(kind: str, title: str, detail: str, entity_type: str, entity_id: Any, data: dict | None = None) -> dict[str, Any]:
    return {"id": f"{kind}:{entity_id}", "type": kind, "severity": SEVERITY.get(kind, "info"), "title": title,
            "detail": detail, "entity_type": entity_type, "entity_id": str(entity_id), "data": data or {}}


def fmt(v: Decimal) -> str:
    return format(v.quantize(Decimal("0.0001")).normalize(), "f")


def compute_alerts(db: Session, warehouse_ids: frozenset[uuid.UUID] | None = None, types: set[str] | None = None,
                   today: date | None = None) -> list[dict[str, Any]]:
    today = today or utcnow().date()
    want = (lambda t: types is None or t in types)
    alerts: list[dict[str, Any]] = []

    products = list(db.execute(select(Product).where(Product.is_active.is_(True))).scalars())
    sums = stock_summaries(db, [p.id for p in products], warehouse_ids)
    for p in products:
        s = sums.get(p.id, {})
        avail, phys = s.get("available", ZERO), s.get("physical", ZERO)
        status = stock_level_status(p, avail, phys)
        data = {"product_id": str(p.id), "sku": p.sku, "available": fmt(avail), "physical": fmt(phys), "uom": p.uom.code,
                "reorder_level": fmt(p.reorder_level), "min_stock_level": fmt(p.min_stock_level),
                "reorder_quantity": fmt(p.reorder_quantity)}
        if status == "out" and want("critical_stock"):
            alerts.append(_alert("critical_stock", f"{p.name} is out of stock", f"Available 0 {p.uom.code}; reorder level "
                                 f"{fmt(p.reorder_level)}", "product", p.id, data))
        elif status == "critical" and want("critical_stock"):
            alerts.append(_alert("critical_stock", f"{p.name} is at critical level",
                                 f"Available {fmt(avail)} {p.uom.code} ≤ minimum {fmt(p.min_stock_level)}", "product", p.id, data))
        elif status == "low" and want("low_stock"):
            alerts.append(_alert("low_stock", f"{p.name} is below reorder level",
                                 f"Available {fmt(avail)} {p.uom.code} ≤ reorder level {fmt(p.reorder_level)}", "product", p.id, data))
        elif status == "overstock" and want("overstock"):
            alerts.append(_alert("overstock", f"{p.name} is overstocked",
                                 f"Physical {fmt(phys)} {p.uom.code} > maximum {fmt(p.max_stock_level or ZERO)}", "product", p.id, data))

    if want("expiring_stock"):
        horizon = today + timedelta(days=int(get_setting(db, "inventory.expiry_alert_days")))
        stmt = (select(ProductBatch, Product, func.sum(InventoryBalance.qty_on_hand))
                .join(InventoryBalance, InventoryBalance.batch_id == ProductBatch.id).join(Product, Product.id == ProductBatch.product_id)
                .where(ProductBatch.expiry_date.is_not(None), ProductBatch.expiry_date <= horizon, InventoryBalance.qty_on_hand > 0)
                .group_by(ProductBatch.id, Product.id).options(lazyload("*")))
        if warehouse_ids is not None:
            stmt = stmt.where(InventoryBalance.warehouse_id.in_(warehouse_ids))
        for batch, p, qty in db.execute(stmt):
            expired = batch.expiry_date < today
            alerts.append(_alert("expiring_stock", f"{p.name} batch {batch.batch_number} {'expired' if expired else 'expiring'}",
                                 f"{fmt(qty)} {p.uom.code} {'expired on' if expired else 'expires on'} {batch.expiry_date.isoformat()}",
                                 "product_batch", batch.id, {"product_id": str(p.id), "sku": p.sku, "quantity": fmt(qty),
                                                             "expiry_date": batch.expiry_date.isoformat(), "expired": expired}))

    if want("pending_receipt"):
        stmt = (select(PurchaseOrder).where(PurchaseOrder.status.in_([POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED]),
                                            PurchaseOrder.expected_delivery_date.is_not(None), PurchaseOrder.expected_delivery_date < today))
        if warehouse_ids is not None:
            stmt = stmt.where(PurchaseOrder.delivery_warehouse_id.in_(warehouse_ids))
        for po in db.execute(stmt).unique().scalars():
            days = (today - po.expected_delivery_date).days
            alerts.append(_alert("pending_receipt", f"{po.po_number} from {po.supplier.name} is overdue",
                                 f"Expected {po.expected_delivery_date.isoformat()} ({days} day{'s' if days != 1 else ''} late), status "
                                 f"{po.status.value.replace('_', ' ')}", "purchase_order", po.id,
                                 {"po_number": po.po_number, "supplier": po.supplier.name, "days_overdue": days}))

    if want("unconfirmed_transfer"):
        cutoff = utcnow() - timedelta(days=int(get_setting(db, "inventory.transfer_overdue_days")))
        stmt = select(StockTransfer).where(StockTransfer.status == TransferStatus.IN_TRANSIT, StockTransfer.dispatched_at < cutoff)
        if warehouse_ids is not None:
            stmt = stmt.where(StockTransfer.destination_warehouse_id.in_(warehouse_ids))
        for t in db.execute(stmt).unique().scalars():
            alerts.append(_alert("unconfirmed_transfer", f"Transfer {t.transfer_number} not yet received",
                                 f"In transit {t.source_warehouse.code} → {t.destination_warehouse.code} since "
                                 f"{t.dispatched_at.date().isoformat()}", "stock_transfer", t.id, {"transfer_number": t.transfer_number}))

    if want("stock_discrepancy"):
        since = utcnow() - timedelta(days=30)
        stmt = (select(StockAdjustment, func.count(StockAdjustmentItem.id), func.sum(func.abs(StockAdjustmentItem.quantity_delta)))
                .join(StockAdjustmentItem, StockAdjustmentItem.adjustment_id == StockAdjustment.id)
                .where(StockAdjustment.adjustment_type == AdjustmentType.RECONCILIATION,
                       StockAdjustment.status.in_([AdjustmentStatus.PENDING_APPROVAL, AdjustmentStatus.POSTED]),
                       StockAdjustment.created_at >= since, StockAdjustmentItem.quantity_delta != 0)
                .group_by(StockAdjustment.id).options(lazyload("*")))
        if warehouse_ids is not None:
            stmt = stmt.where(StockAdjustment.warehouse_id.in_(warehouse_ids))
        for adj, n, _ in db.execute(stmt).unique():
            alerts.append(_alert("stock_discrepancy", f"Count {adj.adjustment_number} found {n} variance line{'s' if n != 1 else ''}",
                                 f"{db.get(Warehouse, adj.warehouse_id).code} · {adj.status.value.replace('_', ' ')}", "stock_adjustment", adj.id,
                                 {"adjustment_number": adj.adjustment_number, "lines": n}))

    if want("inactive_stock"):
        days = int(get_setting(db, "inventory.inactive_days"))
        cutoff = utcnow() - timedelta(days=days)
        last_move = (select(StockMovement.product_id, func.max(StockMovement.created_at).label("last"))
                     .group_by(StockMovement.product_id).subquery())
        stmt = (select(Product, last_move.c.last, func.sum(InventoryBalance.qty_on_hand))
                .join(last_move, last_move.c.product_id == Product.id)
                .join(InventoryBalance, InventoryBalance.product_id == Product.id)
                .where(last_move.c.last < cutoff, InventoryBalance.qty_on_hand > 0)
                .group_by(Product.id, last_move.c.last).options(lazyload("*")))
        if warehouse_ids is not None:
            stmt = stmt.where(InventoryBalance.warehouse_id.in_(warehouse_ids))
        for p, last, qty in db.execute(stmt).unique():
            alerts.append(_alert("inactive_stock", f"{p.name} has not moved in {days}+ days",
                                 f"{fmt(qty)} {p.uom.code} on hand; last movement {last.date().isoformat()}", "product", p.id,
                                 {"sku": p.sku, "quantity": fmt(qty), "last_movement": last.isoformat()}))

    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a["severity"]], a["type"], a["title"]))
    return alerts
