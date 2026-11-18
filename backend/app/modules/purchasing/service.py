"""Procurement foundation: requisitions → purchase orders → goods receipts (the only path that changes stock)."""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import audit, get_setting, next_number
from app.core.database import ZERO, utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, InvalidState, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    Department, GoodsReceipt, GoodsReceiptItem, GRNStatus, LocationType, MovementType, POStatus, Product, ProductSupplier,
    PurchaseOrder, PurchaseOrderItem, PurchaseRequisition, PurchaseRequisitionItem, RequisitionStatus, StockStatus,
    StorageLocation, Supplier, User, Warehouse,
)
from app.modules.inventory.ledger import Line, Posting, get_or_create_batch, post, q4
from app.modules.inventory.service import default_location
from app.modules.purchasing import schemas as s

OPEN_FOR_RECEIPT = (POStatus.APPROVED, POStatus.SENT, POStatus.PARTIALLY_RECEIVED)


def _expect(status, allowed: tuple, action: str) -> None:
    if status not in allowed:
        raise InvalidState(f"Cannot {action} a document in status '{status.value}'",
                           details={"status": status.value, "allowed": [a.value for a in allowed]})


def _product(db: Session, pid: uuid.UUID) -> Product:
    p = db.get(Product, pid)
    if p is None:
        raise NotFound("Product not found", details={"product_id": str(pid)})
    if not p.is_active:
        raise BusinessRuleViolation(f"Product {p.sku} is inactive")
    return p


def _supplier(db: Session, sid: uuid.UUID) -> Supplier:
    sup = db.get(Supplier, sid)
    if sup is None:
        raise NotFound("Supplier not found")
    if not sup.is_active:
        raise BusinessRuleViolation(f"Supplier {sup.code} is inactive")
    return sup


# ---------------------------------------------------------------- requisitions
def requisition_out(db: Session, r: PurchaseRequisition) -> dict[str, Any]:
    dept = db.get(Department, r.department_id) if r.department_id else None
    creator = db.get(User, r.created_by) if r.created_by else None
    total = sum(((i.estimated_unit_cost or i.product.purchase_price) * i.quantity for i in r.items), ZERO)
    return {
        **{c: getattr(r, c) for c in ("id", "requisition_number", "status", "department_id", "required_date", "justification",
                                      "source", "created_by", "created_at", "submitted_at", "approved_by", "approved_at",
                                      "rejection_reason", "purchase_order_id")},
        "department_name": dept.name if dept else None, "created_by_name": creator.full_name if creator else None,
        "estimated_total": q4(total),
        "items": [{"id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_sku": i.product.sku,
                   "product_name": i.product.name, "uom_code": i.product.uom.code, "quantity": i.quantity,
                   "preferred_supplier_id": i.preferred_supplier_id,
                   "preferred_supplier_name": i.preferred_supplier.name if i.preferred_supplier else None,
                   "estimated_unit_cost": i.estimated_unit_cost, "notes": i.notes} for i in r.items],
    }


def _requisition_items(db: Session, items: list[s.RequisitionItemIn]) -> list[PurchaseRequisitionItem]:
    out = []
    for n, it in enumerate(items, start=1):
        _product(db, it.product_id)
        if it.preferred_supplier_id:
            _supplier(db, it.preferred_supplier_id)
        out.append(PurchaseRequisitionItem(line_no=n, **it.model_dump()))
    return out


def create_requisition(db: Session, actor: Actor, data: s.RequisitionCreate, *, source: str = "manual") -> PurchaseRequisition:
    if data.department_id and db.get(Department, data.department_id) is None:
        raise NotFound("Department not found")
    r = PurchaseRequisition(requisition_number=next_number(db, "purchase_requisition"), status=RequisitionStatus.DRAFT,
                            department_id=data.department_id, required_date=data.required_date,
                            justification=data.justification, source=source, created_by=actor.user_id, updated_by=actor.user_id)
    r.items = _requisition_items(db, data.items)
    db.add(r)
    db.flush()
    audit(db, actor, "requisition.create", "purchase_requisition", r.id, {"number": r.requisition_number, "source": source})
    if data.submit:
        submit_requisition(db, actor, r.id)
    return r


def _get_requisition(db: Session, rid: uuid.UUID) -> PurchaseRequisition:
    r = db.execute(select(PurchaseRequisition).where(PurchaseRequisition.id == rid).with_for_update(of=PurchaseRequisition)).unique().scalar_one_or_none()
    if r is None:
        raise NotFound("Purchase requisition not found")
    return r


def update_requisition(db: Session, actor: Actor, rid: uuid.UUID, data: s.RequisitionUpdate) -> PurchaseRequisition:
    r = _get_requisition(db, rid)
    _expect(r.status, (RequisitionStatus.DRAFT,), "edit")
    if r.created_by != actor.user_id and not actor.has("purchasing.requisition.approve"):
        raise PermissionDenied("Only the requester can edit this requisition")
    for k, v in data.model_dump(exclude_unset=True, exclude={"items"}).items():
        setattr(r, k, v)
    if data.items is not None:
        r.items = _requisition_items(db, data.items)
    r.updated_by = actor.user_id
    audit(db, actor, "requisition.update", "purchase_requisition", r.id)
    return r


def submit_requisition(db: Session, actor: Actor, rid: uuid.UUID) -> PurchaseRequisition:
    r = _get_requisition(db, rid)
    _expect(r.status, (RequisitionStatus.DRAFT,), "submit")
    r.status, r.submitted_at = RequisitionStatus.PENDING_APPROVAL, utcnow()
    audit(db, actor, "requisition.submit", "purchase_requisition", r.id)
    return r


def decide_requisition(db: Session, actor: Actor, rid: uuid.UUID, approve: bool, reason: str | None = None) -> PurchaseRequisition:
    actor.require("purchasing.requisition.approve")
    r = _get_requisition(db, rid)
    _expect(r.status, (RequisitionStatus.PENDING_APPROVAL,), "approve" if approve else "reject")
    r.status = RequisitionStatus.APPROVED if approve else RequisitionStatus.REJECTED
    r.approved_by, r.approved_at, r.rejection_reason = actor.user_id, utcnow(), None if approve else reason
    audit(db, actor, "requisition.approve" if approve else "requisition.reject", "purchase_requisition", r.id, {"reason": reason})
    return r


def cancel_requisition(db: Session, actor: Actor, rid: uuid.UUID, reason: str) -> PurchaseRequisition:
    r = _get_requisition(db, rid)
    _expect(r.status, (RequisitionStatus.DRAFT, RequisitionStatus.PENDING_APPROVAL, RequisitionStatus.APPROVED), "cancel")
    if r.created_by != actor.user_id and not actor.has("purchasing.requisition.approve"):
        raise PermissionDenied("Only the requester or an approver can cancel this requisition")
    r.status, r.rejection_reason = RequisitionStatus.CANCELLED, reason
    audit(db, actor, "requisition.cancel", "purchase_requisition", r.id, {"reason": reason})
    return r


def convert_requisition(db: Session, actor: Actor, rid: uuid.UUID, data: s.ConvertIn) -> PurchaseOrder:
    r = _get_requisition(db, rid)
    _expect(r.status, (RequisitionStatus.APPROVED,), "convert")
    items = [s.POItemIn(product_id=i.product_id, quantity_ordered=i.quantity,
                        unit_price=data.unit_prices.get(i.product_id, i.estimated_unit_cost if i.estimated_unit_cost is not None
                                                        else i.product.purchase_price)) for i in r.items]
    po = create_po(db, actor, s.POCreate(supplier_id=data.supplier_id, delivery_warehouse_id=data.delivery_warehouse_id,
                                         expected_delivery_date=data.expected_delivery_date, items=items,
                                         notes=f"Created from requisition {r.requisition_number}"))
    r.status, r.purchase_order_id = RequisitionStatus.CONVERTED, po.id
    audit(db, actor, "requisition.convert", "purchase_requisition", r.id, {"purchase_order": po.po_number})
    return po


# ---------------------------------------------------------------- purchase orders
def po_out(db: Session, po: PurchaseOrder) -> dict[str, Any]:
    today = utcnow().date()
    reqs = db.execute(select(PurchaseRequisition.requisition_number).where(PurchaseRequisition.purchase_order_id == po.id)).scalars().all()
    return {
        **{c: getattr(po, c) for c in ("id", "po_number", "supplier_id", "status", "order_date", "expected_delivery_date",
                                       "delivery_warehouse_id", "currency_code", "supplier_reference", "payment_terms", "notes",
                                       "subtotal", "tax_amount", "total_amount", "created_by", "created_at", "submitted_at",
                                       "approved_by", "approved_at", "rejection_reason", "sent_at", "closed_at", "cancel_reason")},
        "supplier_code": po.supplier.code, "supplier_name": po.supplier.name, "delivery_warehouse_code": po.delivery_warehouse.code,
        "is_overdue": bool(po.expected_delivery_date and po.expected_delivery_date < today and po.status in OPEN_FOR_RECEIPT),
        "requisition_numbers": list(reqs),
        "items": [{"id": i.id, "line_no": i.line_no, "product_id": i.product_id, "product_sku": i.product.sku,
                   "product_name": i.product.name, "uom_code": i.product.uom.code, "description": i.description,
                   "quantity_ordered": i.quantity_ordered, "quantity_received": i.quantity_received,
                   "quantity_rejected": i.quantity_rejected, "quantity_outstanding": i.quantity_outstanding,
                   "unit_price": i.unit_price, "tax_rate": i.tax_rate, "line_total": i.line_total} for i in po.items],
    }


def _po_items(db: Session, items: list[s.POItemIn]) -> tuple[list[PurchaseOrderItem], Decimal, Decimal]:
    lines, subtotal, tax = [], ZERO, ZERO
    for n, it in enumerate(items, start=1):
        _product(db, it.product_id)
        net = q4(it.quantity_ordered * it.unit_price)
        t = q4(net * it.tax_rate / Decimal(100))
        subtotal, tax = subtotal + net, tax + t
        lines.append(PurchaseOrderItem(line_no=n, product_id=it.product_id, description=it.description,
                                       quantity_ordered=it.quantity_ordered, unit_price=it.unit_price, tax_rate=it.tax_rate,
                                       line_total=net + t))
    return lines, subtotal, tax


def create_po(db: Session, actor: Actor, data: s.POCreate) -> PurchaseOrder:
    actor.require("purchasing.po.create")
    sup = _supplier(db, data.supplier_id)
    wh = db.get(Warehouse, data.delivery_warehouse_id)
    if wh is None or not wh.is_active:
        raise NotFound("Delivery warehouse not found or inactive")
    from app.modules.erp_core.service import get_company
    company = get_company(db)
    items, subtotal, tax = _po_items(db, data.items)
    po = PurchaseOrder(po_number=next_number(db, "purchase_order"), supplier_id=sup.id, status=POStatus.DRAFT,
                       order_date=data.order_date or utcnow().date(), expected_delivery_date=data.expected_delivery_date,
                       delivery_warehouse_id=wh.id,
                       currency_code=data.currency_code or sup.currency_code or (company.currency_code if company else "INR"),
                       supplier_reference=data.supplier_reference, payment_terms=data.payment_terms or sup.payment_terms,
                       notes=data.notes, subtotal=subtotal, tax_amount=tax, total_amount=subtotal + tax,
                       created_by=actor.user_id, updated_by=actor.user_id)
    po.items = items
    db.add(po)
    db.flush()
    audit(db, actor, "purchase_order.create", "purchase_order", po.id, {"number": po.po_number, "total": po.total_amount})
    if data.submit:
        submit_po(db, actor, po.id)
    return po


def _get_po(db: Session, po_id: uuid.UUID, lock: bool = True) -> PurchaseOrder:
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    if lock:
        stmt = stmt.with_for_update(of=PurchaseOrder)
    po = db.execute(stmt).unique().scalar_one_or_none()
    if po is None:
        raise NotFound("Purchase order not found")
    return po


def update_po(db: Session, actor: Actor, po_id: uuid.UUID, data: s.POUpdate) -> PurchaseOrder:
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.DRAFT,), "edit")
    for k, v in data.model_dump(exclude_unset=True, exclude={"items"}).items():
        setattr(po, k, v)
    if data.items is not None:
        po.items, po.subtotal, po.tax_amount = _po_items(db, data.items)
        po.total_amount = po.subtotal + po.tax_amount
    po.updated_by = actor.user_id
    audit(db, actor, "purchase_order.update", "purchase_order", po.id)
    return po


def submit_po(db: Session, actor: Actor, po_id: uuid.UUID) -> PurchaseOrder:
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.DRAFT,), "submit")
    po.submitted_at = utcnow()
    if get_setting(db, "purchasing.po_requires_approval"):
        po.status = POStatus.PENDING_APPROVAL
    else:
        po.status, po.approved_by, po.approved_at = POStatus.APPROVED, actor.user_id, utcnow()
    audit(db, actor, "purchase_order.submit", "purchase_order", po.id, {"status": po.status})
    return po


def decide_po(db: Session, actor: Actor, po_id: uuid.UUID, approve: bool, reason: str | None = None) -> PurchaseOrder:
    actor.require("purchasing.po.approve")
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.PENDING_APPROVAL,), "approve" if approve else "reject")
    if approve:
        po.status, po.approved_by, po.approved_at, po.rejection_reason = POStatus.APPROVED, actor.user_id, utcnow(), None
    else:
        po.status, po.rejection_reason = POStatus.DRAFT, reason  # back to draft for correction
    audit(db, actor, "purchase_order.approve" if approve else "purchase_order.reject", "purchase_order", po.id, {"reason": reason})
    return po


def send_po(db: Session, actor: Actor, po_id: uuid.UUID) -> PurchaseOrder:
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.APPROVED,), "send")
    po.status, po.sent_at = POStatus.SENT, utcnow()
    audit(db, actor, "purchase_order.send", "purchase_order", po.id)
    return po


def close_po(db: Session, actor: Actor, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.SENT, POStatus.PARTIALLY_RECEIVED, POStatus.FULLY_RECEIVED), "close")
    po.status, po.closed_at = POStatus.CLOSED, utcnow()
    po.notes = ((po.notes + "\n") if po.notes else "") + f"Closed: {reason}"
    audit(db, actor, "purchase_order.close", "purchase_order", po.id, {"reason": reason})
    return po


def cancel_po(db: Session, actor: Actor, po_id: uuid.UUID, reason: str) -> PurchaseOrder:
    po = _get_po(db, po_id)
    _expect(po.status, (POStatus.DRAFT, POStatus.PENDING_APPROVAL, POStatus.APPROVED, POStatus.SENT), "cancel")
    if any(i.quantity_received > 0 for i in po.items):
        raise BusinessRuleViolation("Purchase order has receipts; close it instead of cancelling")
    drafts = db.execute(select(GoodsReceipt).where(GoodsReceipt.purchase_order_id == po.id, GoodsReceipt.status == GRNStatus.DRAFT)).first()
    if drafts:
        raise BusinessRuleViolation("Cancel the draft goods receipts for this order first")
    po.status, po.cancelled_at, po.cancel_reason = POStatus.CANCELLED, utcnow(), reason
    audit(db, actor, "purchase_order.cancel", "purchase_order", po.id, {"reason": reason})
    return po


# ---------------------------------------------------------------- goods receipts
def grn_out(g: GoodsReceipt) -> dict[str, Any]:
    total = sum(((i.quantity_received + i.quantity_damaged) * i.unit_cost for i in g.items), ZERO)
    return {
        **{c: getattr(g, c) for c in ("id", "grn_number", "purchase_order_id", "supplier_id", "warehouse_id", "status",
                                      "received_date", "delivery_note_number", "invoice_number", "transporter", "notes",
                                      "over_receipt_reason", "created_by", "created_at", "confirmed_by", "confirmed_at",
                                      "transaction_id")},
        "po_number": g.purchase_order.po_number, "supplier_name": g.supplier.name, "warehouse_code": g.warehouse.code,
        "total_value": q4(total),
        "items": [{"id": i.id, "line_no": i.line_no, "purchase_order_item_id": i.purchase_order_item_id, "product_id": i.product_id,
                   "product_sku": i.product.sku, "product_name": i.product.name, "uom_code": i.product.uom.code,
                   "location_id": i.location_id, "location_path": i.location.path, "quantity_received": i.quantity_received,
                   "quantity_damaged": i.quantity_damaged, "quantity_rejected": i.quantity_rejected, "unit_cost": i.unit_cost,
                   "batch_number": i.batch_number, "expiry_date": i.expiry_date, "serial_numbers": i.serial_numbers,
                   "rejection_reason": i.rejection_reason} for i in g.items],
    }


def create_grn(db: Session, actor: Actor, data: s.GRNCreate) -> GoodsReceipt:
    po = _get_po(db, data.purchase_order_id, lock=False)
    actor.require_warehouse(po.delivery_warehouse_id)
    _expect(po.status, OPEN_FOR_RECEIPT, "receive against")
    po_items = {i.id: i for i in po.items}
    g = GoodsReceipt(grn_number=next_number(db, "goods_receipt"), purchase_order_id=po.id, supplier_id=po.supplier_id,
                     warehouse_id=po.delivery_warehouse_id, status=GRNStatus.DRAFT, received_date=data.received_date or utcnow().date(),
                     delivery_note_number=data.delivery_note_number, invoice_number=data.invoice_number,
                     transporter=data.transporter, notes=data.notes, created_by=actor.user_id, updated_by=actor.user_id)
    seen = set()
    for n, it in enumerate(data.items, start=1):
        poi = po_items.get(it.purchase_order_item_id)
        if poi is None:
            raise ValidationFailed("Line does not belong to this purchase order")
        if poi.id in seen:
            raise ValidationFailed("Each purchase order line may appear once per receipt")
        seen.add(poi.id)
        product = poi.product
        if it.location_id:
            loc = db.get(StorageLocation, it.location_id)
            if loc is None or loc.warehouse_id != po.delivery_warehouse_id or loc.location_type == LocationType.TRANSIT:
                raise ValidationFailed("Location must be a storage location in the delivery warehouse")
        else:
            loc = default_location(db, po.delivery_warehouse_id)
        if product.track_batches and (it.quantity_received + it.quantity_damaged) > 0 and it.batch is None:
            raise ValidationFailed(f"Batch details are required for {product.sku}")
        if it.quantity_rejected > 0 and not it.rejection_reason:
            raise ValidationFailed("A rejection reason is required when rejecting quantity")
        g.items.append(GoodsReceiptItem(
            line_no=n, purchase_order_item_id=poi.id, product_id=product.id, location_id=loc.id,
            quantity_received=it.quantity_received, quantity_damaged=it.quantity_damaged, quantity_rejected=it.quantity_rejected,
            unit_cost=poi.unit_price, batch_number=it.batch.batch_number if it.batch else None,
            manufacturing_date=it.batch.manufacturing_date if it.batch else None,
            expiry_date=it.batch.expiry_date if it.batch else None,
            supplier_batch_ref=it.batch.supplier_batch_ref if it.batch else None,
            serial_numbers=it.serial_numbers, rejection_reason=it.rejection_reason))
    db.add(g)
    db.flush()
    audit(db, actor, "goods_receipt.create", "goods_receipt", g.id, {"number": g.grn_number, "po": po.po_number})
    return g


def _get_grn(db: Session, actor: Actor, gid: uuid.UUID) -> GoodsReceipt:
    g = db.execute(select(GoodsReceipt).where(GoodsReceipt.id == gid).with_for_update(of=GoodsReceipt)).unique().scalar_one_or_none()
    if g is None or not actor.can_access_warehouse(g.warehouse_id):
        raise NotFound("Goods receipt not found")
    return g


def confirm_grn(db: Session, actor: Actor, gid: uuid.UUID, over_receipt_reason: str | None = None) -> GoodsReceipt:
    g = _get_grn(db, actor, gid)
    _expect(g.status, (GRNStatus.DRAFT,), "confirm")
    po = _get_po(db, g.purchase_order_id)  # lock PO so concurrent receipts cannot both pass the outstanding check
    _expect(po.status, OPEN_FOR_RECEIPT, "receive against")
    po_items = {i.id: i for i in po.items}
    over_lines = []
    for it in g.items:
        poi = po_items[it.purchase_order_item_id]
        incoming = it.quantity_received + it.quantity_damaged
        if incoming > poi.quantity_outstanding:
            over_lines.append({"product": poi.product.sku, "outstanding": format(poi.quantity_outstanding, "f"),
                               "incoming": format(incoming, "f")})
    if over_lines:
        if not actor.has("purchasing.over_receipt"):
            raise BusinessRuleViolation("Received quantity exceeds the outstanding purchase order quantity",
                                        code="over_receipt", details={"lines": over_lines, "permission": "purchasing.over_receipt"})
        if not over_receipt_reason:
            raise ValidationFailed("over_receipt_reason is required to receive more than ordered", details={"lines": over_lines})
        g.over_receipt_reason = over_receipt_reason

    lines: list[Line] = []
    for it in g.items:
        product = it.product
        batch_id = None
        if product.track_batches and (it.quantity_received + it.quantity_damaged) > 0:
            batch_id = get_or_create_batch(db, product, it.batch_number or "", manufacturing_date=it.manufacturing_date,
                                           expiry_date=it.expiry_date, supplier_id=g.supplier_id,
                                           supplier_batch_ref=it.supplier_batch_ref).id
        serials = list(it.serial_numbers or [])
        if it.quantity_received > 0:
            n = int(it.quantity_received) if product.track_serials else 0
            lines.append(Line(product_id=product.id, location_id=it.location_id, quantity=it.quantity_received,
                              movement_type=MovementType.RECEIPT, batch_id=batch_id, unit_cost=it.unit_cost,
                              serial_numbers=serials[:n] if product.track_serials else None))
            serials = serials[n:]
        if it.quantity_damaged > 0:
            lines.append(Line(product_id=product.id, location_id=it.location_id, quantity=it.quantity_damaged,
                              movement_type=MovementType.RECEIPT, stock_status=StockStatus.DAMAGED, batch_id=batch_id,
                              unit_cost=it.unit_cost, serial_numbers=serials if product.track_serials else None,
                              reason="Damaged on receipt"))
        poi = po_items[it.purchase_order_item_id]
        poi.quantity_received += it.quantity_received + it.quantity_damaged
        poi.quantity_rejected += it.quantity_rejected
        ps = db.execute(select(ProductSupplier).where(ProductSupplier.product_id == product.id,
                                                      ProductSupplier.supplier_id == g.supplier_id)).scalar_one_or_none()
        if ps is None:
            db.add(ProductSupplier(product_id=product.id, supplier_id=g.supplier_id, last_purchase_price=it.unit_cost))
        else:
            ps.last_purchase_price = it.unit_cost

    if lines:
        tx = post(db, actor, Posting(MovementType.RECEIPT, lines, reference_type="goods_receipt", reference_id=g.id,
                                     reference_number=g.grn_number, reason=f"Goods receipt against {po.po_number}",
                                     extra_audit={"purchase_order": po.po_number}))
        g.transaction_id = tx.id
    g.status, g.confirmed_by, g.confirmed_at = GRNStatus.CONFIRMED, actor.user_id, utcnow()
    po.status = POStatus.FULLY_RECEIVED if all(i.quantity_outstanding == 0 for i in po.items) else POStatus.PARTIALLY_RECEIVED
    audit(db, actor, "goods_receipt.confirm", "goods_receipt", g.id, {"po_status": po.status, "over_receipt": bool(over_lines)})
    return g


def cancel_grn(db: Session, actor: Actor, gid: uuid.UUID, reason: str) -> GoodsReceipt:
    g = _get_grn(db, actor, gid)
    _expect(g.status, (GRNStatus.DRAFT,), "cancel")
    g.status = GRNStatus.CANCELLED
    g.notes = ((g.notes + "\n") if g.notes else "") + f"Cancelled: {reason}"
    audit(db, actor, "goods_receipt.cancel", "goods_receipt", g.id, {"reason": reason})
    return g
