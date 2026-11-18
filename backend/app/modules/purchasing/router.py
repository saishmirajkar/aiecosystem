"""Purchasing HTTP endpoints."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import or_, select

from app.core.common import Page, paginate
from app.core.database import utcnow
from app.core.deps import DB, Actor, require_permission
from app.core.idempotency import run_idempotent
from app.models import GoodsReceipt, POStatus, PurchaseOrder, PurchaseRequisition, Supplier
from app.modules.inventory.schemas import CancelIn, RejectIn
from app.modules.purchasing import schemas as s
from app.modules.purchasing import service as svc

router = APIRouter(tags=["purchasing"])
VIEW = require_permission("purchasing.view")
REQ_CREATE = require_permission("purchasing.requisition.create")
PO_CREATE = require_permission("purchasing.po.create")


# ---------------------------------------------------------------- requisitions
@router.get("/purchase-requisitions", response_model=Page[s.RequisitionOut])
def list_requisitions(db: DB, status: str | None = None, q: str | None = None,
                      mine: bool = False, actor: Actor = Depends(VIEW),
                      page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(PurchaseRequisition)
    if status:
        stmt = stmt.where(PurchaseRequisition.status.in_(status.split(",")))
    if q:
        stmt = stmt.where(PurchaseRequisition.requisition_number.ilike(f"%{q}%"))
    if mine:
        stmt = stmt.where(PurchaseRequisition.created_by == actor.user_id)
    result = paginate(db, stmt.order_by(PurchaseRequisition.created_at.desc()), page, page_size)
    result["items"] = [svc.requisition_out(db, r) for r in result["items"]]
    return result


@router.post("/purchase-requisitions", response_model=s.RequisitionOut, status_code=201)
def create_requisition(data: s.RequisitionCreate, db: DB, actor: Actor = Depends(REQ_CREATE)):
    r = svc.create_requisition(db, actor, data)
    db.commit()
    return svc.requisition_out(db, r)


@router.get("/purchase-requisitions/{rid}", response_model=s.RequisitionOut)
def get_requisition(rid: uuid.UUID, db: DB, _: Actor = Depends(VIEW)):
    r = db.get(PurchaseRequisition, rid)
    if r is None:
        from app.core.errors import NotFound
        raise NotFound("Purchase requisition not found")
    return svc.requisition_out(db, r)


@router.patch("/purchase-requisitions/{rid}", response_model=s.RequisitionOut)
def update_requisition(rid: uuid.UUID, data: s.RequisitionUpdate, db: DB, actor: Actor = Depends(REQ_CREATE)):
    r = svc.update_requisition(db, actor, rid, data)
    db.commit()
    return svc.requisition_out(db, r)


@router.post("/purchase-requisitions/{rid}/submit", response_model=s.RequisitionOut)
def submit_requisition(rid: uuid.UUID, db: DB, actor: Actor = Depends(REQ_CREATE)):
    r = svc.submit_requisition(db, actor, rid)
    db.commit()
    return svc.requisition_out(db, r)


@router.post("/purchase-requisitions/{rid}/approve", response_model=s.RequisitionOut)
def approve_requisition(rid: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("purchasing.requisition.approve"))):
    r = svc.decide_requisition(db, actor, rid, True)
    db.commit()
    return svc.requisition_out(db, r)


@router.post("/purchase-requisitions/{rid}/reject", response_model=s.RequisitionOut)
def reject_requisition(rid: uuid.UUID, data: RejectIn, db: DB, actor: Actor = Depends(require_permission("purchasing.requisition.approve"))):
    r = svc.decide_requisition(db, actor, rid, False, data.reason)
    db.commit()
    return svc.requisition_out(db, r)


@router.post("/purchase-requisitions/{rid}/cancel", response_model=s.RequisitionOut)
def cancel_requisition(rid: uuid.UUID, data: CancelIn, db: DB, actor: Actor = Depends(REQ_CREATE)):
    r = svc.cancel_requisition(db, actor, rid, data.reason)
    db.commit()
    return svc.requisition_out(db, r)


@router.post("/purchase-requisitions/{rid}/convert", response_model=s.POOut, status_code=201)
def convert_requisition(rid: uuid.UUID, data: s.ConvertIn, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.convert_requisition(db, actor, rid, data)
    db.commit()
    return svc.po_out(db, po)


# ---------------------------------------------------------------- purchase orders
@router.get("/purchase-orders", response_model=Page[s.POOut])
def list_pos(db: DB, actor: Actor = Depends(VIEW), status: str | None = None, supplier_id: uuid.UUID | None = None,
             q: str | None = None, overdue: bool = False, open_for_receipt: bool = False,
             date_from: date | None = None, date_to: date | None = None,
             page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(PurchaseOrder)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(PurchaseOrder.delivery_warehouse_id.in_(actor.warehouse_ids))
    if status:
        stmt = stmt.where(PurchaseOrder.status.in_(status.split(",")))
    if open_for_receipt or overdue:
        stmt = stmt.where(PurchaseOrder.status.in_(svc.OPEN_FOR_RECEIPT))
    if overdue:
        stmt = stmt.where(PurchaseOrder.expected_delivery_date < utcnow().date())
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if date_from:
        stmt = stmt.where(PurchaseOrder.order_date >= date_from)
    if date_to:
        stmt = stmt.where(PurchaseOrder.order_date <= date_to)
    if q:
        stmt = stmt.join(Supplier, Supplier.id == PurchaseOrder.supplier_id).where(
            or_(PurchaseOrder.po_number.ilike(f"%{q}%"), Supplier.name.ilike(f"%{q}%")))
    result = paginate(db, stmt.order_by(PurchaseOrder.created_at.desc()), page, page_size)
    result["items"] = [svc.po_out(db, po) for po in result["items"]]
    return result


@router.post("/purchase-orders", response_model=s.POOut, status_code=201)
def create_po(data: s.POCreate, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.create_po(db, actor, data)
    db.commit()
    return svc.po_out(db, po)


@router.get("/purchase-orders/{po_id}", response_model=s.POOut)
def get_po(po_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW)):
    return svc.po_out(db, svc._get_po(db, po_id, lock=False))


@router.patch("/purchase-orders/{po_id}", response_model=s.POOut)
def update_po(po_id: uuid.UUID, data: s.POUpdate, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.update_po(db, actor, po_id, data)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/submit", response_model=s.POOut)
def submit_po(po_id: uuid.UUID, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.submit_po(db, actor, po_id)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/approve", response_model=s.POOut)
def approve_po(po_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("purchasing.po.approve"))):
    po = svc.decide_po(db, actor, po_id, True)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/reject", response_model=s.POOut)
def reject_po(po_id: uuid.UUID, data: RejectIn, db: DB, actor: Actor = Depends(require_permission("purchasing.po.approve"))):
    po = svc.decide_po(db, actor, po_id, False, data.reason)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/send", response_model=s.POOut)
def send_po(po_id: uuid.UUID, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.send_po(db, actor, po_id)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/close", response_model=s.POOut)
def close_po(po_id: uuid.UUID, data: CancelIn, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.close_po(db, actor, po_id, data.reason)
    db.commit()
    return svc.po_out(db, po)


@router.post("/purchase-orders/{po_id}/cancel", response_model=s.POOut)
def cancel_po(po_id: uuid.UUID, data: CancelIn, db: DB, actor: Actor = Depends(PO_CREATE)):
    po = svc.cancel_po(db, actor, po_id, data.reason)
    db.commit()
    return svc.po_out(db, po)


# ---------------------------------------------------------------- goods receipts
@router.get("/goods-receipts", response_model=Page[s.GRNOut])
def list_grns(db: DB, actor: Actor = Depends(VIEW), status: str | None = None, purchase_order_id: uuid.UUID | None = None,
              supplier_id: uuid.UUID | None = None, date_from: date | None = None, date_to: date | None = None,
              q: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200)):
    stmt = select(GoodsReceipt)
    if actor.warehouse_ids is not None:
        stmt = stmt.where(GoodsReceipt.warehouse_id.in_(actor.warehouse_ids))
    if status:
        stmt = stmt.where(GoodsReceipt.status.in_(status.split(",")))
    if purchase_order_id:
        stmt = stmt.where(GoodsReceipt.purchase_order_id == purchase_order_id)
    if supplier_id:
        stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
    if date_from:
        stmt = stmt.where(GoodsReceipt.received_date >= date_from)
    if date_to:
        stmt = stmt.where(GoodsReceipt.received_date <= date_to)
    if q:
        stmt = stmt.where(GoodsReceipt.grn_number.ilike(f"%{q}%"))
    result = paginate(db, stmt.order_by(GoodsReceipt.created_at.desc()), page, page_size)
    result["items"] = [svc.grn_out(g) for g in result["items"]]
    return result


@router.post("/goods-receipts", response_model=s.GRNOut, status_code=201)
def create_grn(data: s.GRNCreate, db: DB, actor: Actor = Depends(require_permission("inventory.receive"))):
    g = svc.create_grn(db, actor, data)
    db.commit()
    return svc.grn_out(g)


@router.get("/goods-receipts/{gid}", response_model=s.GRNOut)
def get_grn(gid: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)):
    g = db.get(GoodsReceipt, gid)
    if g is None or not actor.can_access_warehouse(g.warehouse_id):
        from app.core.errors import NotFound
        raise NotFound("Goods receipt not found")
    return svc.grn_out(g)


@router.post("/goods-receipts/{gid}/confirm", response_model=s.GRNOut)
def confirm_grn(gid: uuid.UUID, request: Request, db: DB, data: s.GRNConfirmIn | None = None,
                actor: Actor = Depends(require_permission("inventory.receive"))):
    data = data or s.GRNConfirmIn()
    return run_idempotent(db, actor, request, {"gid": gid, **data.model_dump()}, lambda: s.GRNOut.model_validate(
        svc.grn_out(svc.confirm_grn(db, actor, gid, data.over_receipt_reason))), status_code=200)


@router.post("/goods-receipts/{gid}/cancel", response_model=s.GRNOut)
def cancel_grn(gid: uuid.UUID, data: CancelIn, db: DB, actor: Actor = Depends(require_permission("inventory.receive"))):
    g = svc.cancel_grn(db, actor, gid, data.reason)
    db.commit()
    return svc.grn_out(g)
