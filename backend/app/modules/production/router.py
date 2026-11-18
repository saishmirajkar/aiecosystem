"""Production, job-work, customer and dispatch endpoints (client/company-x)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import or_, select

from app.core.common import jsonable, paginate
from app.core.deps import DB, Actor, require_permission
from app.core.errors import NotFound
from app.core.idempotency import run_idempotent
from app.models import (
    Customer, DispatchNote, JobWorkChallan, JobWorkReceipt, OrderPart, OrderStage, Product, ProductionOrder, SiteKind,
)
from app.modules.production import schemas as s
from app.modules.production import service as svc

router = APIRouter(tags=["production"])
VIEW = require_permission("production.view")
MANAGE = require_permission("production.manage")
EXECUTE = require_permission("production.execute")


# ---------------------------------------------------------------- sites
@router.get("/production/sites")
def list_sites(db: DB, _: Actor = Depends(VIEW), kind: SiteKind | None = None) -> list[dict]:
    return jsonable([svc.site_out(db, x) for x in svc.list_sites(db, kind)])


@router.post("/production/sites/plants", status_code=201)
def create_plant(data: s.PlantIn, db: DB, actor: Actor = Depends(require_permission("production.manage", "warehouses.manage"))) -> dict:
    site = svc.create_plant(db, actor, data)
    db.commit()
    return jsonable(svc.site_out(db, site))


@router.post("/production/sites/vendors", status_code=201)
def create_vendor_site(data: s.VendorSiteIn, db: DB, actor: Actor = Depends(require_permission("production.manage", "warehouses.manage"))) -> dict:
    site = svc.create_vendor_site(db, actor, data.supplier_id)
    db.commit()
    return jsonable(svc.site_out(db, site))


@router.post("/production/sites/designate", status_code=201)
def designate_plant(data: s.DesignateIn, db: DB, actor: Actor = Depends(require_permission("production.manage", "warehouses.manage"))) -> dict:
    site = svc.designate_plant(db, actor, data.warehouse_id)
    db.commit()
    return jsonable(svc.site_out(db, site))


# ---------------------------------------------------------------- customers
@router.get("/customers", tags=["customers"])
def list_customers(db: DB, _: Actor = Depends(VIEW), q: str | None = None, is_active: bool | None = True,
                   page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> dict:
    stmt = select(Customer)
    if q:
        stmt = stmt.where(or_(Customer.name.ilike(f"%{q}%"), Customer.code.ilike(f"%{q}%")))
    if is_active is not None:
        stmt = stmt.where(Customer.is_active.is_(is_active))
    return jsonable(paginate(db, stmt.order_by(Customer.name), page, page_size,
                             lambda c: {k: getattr(c, k) for k in ("id", "code", "name", "contact_person", "email", "phone", "address",
                                                                   "city", "gstin", "is_active", "created_at")}))


@router.post("/customers", status_code=201, tags=["customers"])
def create_customer(data: s.CustomerIn, db: DB, actor: Actor = Depends(require_permission("customers.manage"))) -> dict:
    c = svc.create_customer(db, actor, data)
    db.commit()
    return jsonable({k: getattr(c, k) for k in ("id", "code", "name", "contact_person", "email", "phone", "address", "city", "gstin", "is_active")})


@router.patch("/customers/{cid}", tags=["customers"])
def update_customer(cid: uuid.UUID, data: s.CustomerUpdate, db: DB, actor: Actor = Depends(require_permission("customers.manage"))) -> dict:
    c = svc.update_customer(db, actor, cid, data)
    db.commit()
    return jsonable({k: getattr(c, k) for k in ("id", "code", "name", "contact_person", "email", "phone", "address", "city", "gstin", "is_active")})


# ---------------------------------------------------------------- product structure
@router.get("/production/products/{product_id}/structure")
def get_structure(product_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW)) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise NotFound("Product not found")
    return jsonable(svc.structure_out(db, product))


@router.put("/production/products/{product_id}/structure")
def put_structure(product_id: uuid.UUID, data: s.StructureIn, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    product = svc.set_structure(db, actor, product_id, data)
    db.commit()
    return jsonable(svc.structure_out(db, product))


@router.get("/production/structures")
def list_structures(db: DB, _: Actor = Depends(VIEW)) -> list[dict]:
    """Products that have a route (manufactured items)."""
    from app.models import RouteStage
    ids = db.execute(select(RouteStage.product_id).distinct()).scalars().all()
    products = db.execute(select(Product).where(Product.id.in_(ids)).order_by(Product.name)).scalars().all() if ids else []
    return jsonable([{"product_id": p.id, "sku": p.sku, "name": p.name, "stages": len(svc.route_of(db, p.id)),
                      "components": len(svc.kit_of(db, p.id))} for p in products])


# ---------------------------------------------------------------- orders
@router.get("/production/orders")
def list_orders(db: DB, _: Actor = Depends(VIEW), status: str | None = None, q: str | None = None,
                page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
    stmt = select(ProductionOrder)
    if status:
        stmt = stmt.where(ProductionOrder.status.in_(status.split(",")))
    if q:
        stmt = stmt.join(Product, Product.id == ProductionOrder.product_id).where(
            or_(ProductionOrder.order_number.ilike(f"%{q}%"), Product.name.ilike(f"%{q}%"), Product.sku.ilike(f"%{q}%")))
    return jsonable(paginate(db, stmt.order_by(ProductionOrder.created_at.desc()), page, page_size, lambda o: svc.order_row(db, o)))


@router.post("/production/orders", status_code=201)
def create_order(data: s.OrderCreate, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    order = svc.create_order(db, actor, data)
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order.id)))


@router.get("/production/orders/{order_id}")
def get_order(order_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW)) -> dict:
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


@router.post("/production/orders/{order_id}/release")
def release_order(order_id: uuid.UUID, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    svc.release_order(db, actor, order_id)
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


@router.post("/production/orders/{order_id}/cancel")
def cancel_order(order_id: uuid.UUID, data: s.ReasonIn, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    svc.cancel_order(db, actor, order_id, data.reason)
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


def _order_of_stage(db, stage_id: uuid.UUID) -> uuid.UUID:
    stage = db.get(OrderStage, stage_id)
    if stage is None:
        raise NotFound("Stage not found")
    return db.get(OrderPart, stage.part_id).order_id


@router.patch("/production/stages/{stage_id}")
def update_stage(stage_id: uuid.UUID, data: s.StageUpdate, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    svc.update_stage(db, actor, stage_id, data)
    order_id = _order_of_stage(db, stage_id)
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


@router.delete("/production/stages/{stage_id}")
def remove_stage(stage_id: uuid.UUID, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    order_id = _order_of_stage(db, stage_id)
    svc.remove_stage(db, actor, stage_id)
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


@router.post("/production/parts/{part_id}/stages", status_code=201)
def add_stage(part_id: uuid.UUID, data: s.StageAdd, db: DB, actor: Actor = Depends(MANAGE)) -> dict:
    stage = svc.add_stage(db, actor, part_id, data)
    order_id = db.get(OrderPart, stage.part_id).order_id
    db.commit()
    return jsonable(svc.order_detail(db, svc.get_order(db, order_id)))


# ---------------------------------------------------------------- execution (idempotent postings)
@router.post("/production/stages/{stage_id}/challans", status_code=201)
def issue_challan(stage_id: uuid.UUID, data: s.ChallanIn, request: Request, db: DB, actor: Actor = Depends(EXECUTE)):
    return run_idempotent(db, actor, request, {"stage": stage_id, **data.model_dump()},
                          lambda: jsonable(svc.challan_out(db, svc.issue_challan(db, actor, stage_id, data))))


@router.post("/production/stages/{stage_id}/receipts", status_code=201)
def record_receipt(stage_id: uuid.UUID, data: s.ReceiptIn, request: Request, db: DB, actor: Actor = Depends(EXECUTE)):
    def run():
        r = svc.record_receipt(db, actor, stage_id, data)
        return jsonable({"id": r.id, "receipt_number": r.receipt_number, "units_accepted": r.units_accepted,
                         "units_rework": r.units_rework, "units_scrapped": r.units_scrapped, "direct_to_next": r.direct_to_next})
    return run_idempotent(db, actor, request, {"stage": stage_id, **data.model_dump()}, run)


@router.post("/production/stages/{stage_id}/complete", status_code=201)
def complete_stage(stage_id: uuid.UUID, data: s.CompletionIn, request: Request, db: DB, actor: Actor = Depends(EXECUTE)):
    def run():
        c = svc.complete_in_house(db, actor, stage_id, data)
        return jsonable({"id": c.id, "units_completed": c.units_completed, "units_scrapped": c.units_scrapped})
    return run_idempotent(db, actor, request, {"stage": stage_id, **data.model_dump()}, run)


# ---------------------------------------------------------------- challans, receipts, vendors, summaries
@router.get("/production/challans")
def list_challans(db: DB, _: Actor = Depends(VIEW), order_id: uuid.UUID | None = None, vendor_site_id: uuid.UUID | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)) -> dict:
    stmt = select(JobWorkChallan)
    if order_id:
        stmt = stmt.where(JobWorkChallan.order_id == order_id)
    if vendor_site_id:
        stmt = stmt.where(JobWorkChallan.vendor_site_id == vendor_site_id)
    return jsonable(paginate(db, stmt.order_by(JobWorkChallan.created_at.desc()), page, page_size, lambda c: svc.challan_out(db, c)))


@router.get("/production/challans/{challan_id}")
def get_challan(challan_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW)) -> dict:
    c = db.get(JobWorkChallan, challan_id)
    if c is None:
        raise NotFound("Challan not found")
    return jsonable(svc.challan_out(db, c))


@router.get("/production/receipts")
def list_receipts(db: DB, _: Actor = Depends(VIEW), order_id: uuid.UUID | None = None) -> list[dict]:
    stmt = select(JobWorkReceipt).order_by(JobWorkReceipt.created_at.desc()).limit(200)
    if order_id:
        stmt = stmt.where(JobWorkReceipt.order_id == order_id)
    return jsonable([{"id": r.id, "receipt_number": r.receipt_number, "stage_id": r.stage_id, "vendor_name": r.vendor_site.name,
                      "receive_to": r.receive_to_site.name if r.receive_to_site else None, "direct_to_next": r.direct_to_next,
                      "units_accepted": r.units_accepted, "units_rework": r.units_rework, "units_scrapped": r.units_scrapped,
                      "scrap_reason": r.scrap_reason, "vendor_document_number": r.vendor_document_number,
                      "received_date": r.received_date} for r in db.execute(stmt).unique().scalars()])


@router.get("/production/vendors/workload")
def vendor_workload(db: DB, _: Actor = Depends(VIEW), vendor_site_id: uuid.UUID | None = None) -> list[dict]:
    return jsonable(svc.vendor_workload(db, vendor_site_id))


@router.get("/production/delays")
def delays(db: DB, _: Actor = Depends(VIEW)) -> list[dict]:
    return jsonable(svc.delayed_stages(db))


@router.get("/production/summary")
def summary(db: DB, _: Actor = Depends(VIEW)) -> dict:
    return jsonable(svc.summary(db))


# ---------------------------------------------------------------- dispatch
@router.get("/dispatch-notes", tags=["dispatch"])
def list_dispatch(db: DB, _: Actor = Depends(VIEW), status: str | None = None,
                  page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
    stmt = select(DispatchNote)
    if status:
        stmt = stmt.where(DispatchNote.status.in_(status.split(",")))
    return jsonable(paginate(db, stmt.order_by(DispatchNote.created_at.desc()), page, page_size, svc.dispatch_out))


@router.post("/dispatch-notes", status_code=201, tags=["dispatch"])
def create_dispatch(data: s.DispatchCreate, db: DB, actor: Actor = Depends(require_permission("dispatch.manage"))) -> dict:
    d = svc.create_dispatch(db, actor, data)
    db.commit()
    return jsonable(svc.dispatch_out(d))


@router.get("/dispatch-notes/{dispatch_id}", tags=["dispatch"])
def get_dispatch(dispatch_id: uuid.UUID, db: DB, _: Actor = Depends(VIEW)) -> dict:
    d = db.get(DispatchNote, dispatch_id)
    if d is None:
        raise NotFound("Dispatch note not found")
    return jsonable(svc.dispatch_out(d))


@router.post("/dispatch-notes/{dispatch_id}/dispatch", tags=["dispatch"])
def do_dispatch(dispatch_id: uuid.UUID, request: Request, db: DB, actor: Actor = Depends(require_permission("dispatch.manage"))):
    return run_idempotent(db, actor, request, {"dispatch": dispatch_id},
                          lambda: jsonable(svc.dispatch_out(svc.dispatch(db, actor, dispatch_id))), status_code=200)


@router.post("/dispatch-notes/{dispatch_id}/cancel", tags=["dispatch"])
def cancel_dispatch(dispatch_id: uuid.UUID, data: s.ReasonIn, db: DB, actor: Actor = Depends(require_permission("dispatch.manage"))) -> dict:
    d = svc.cancel_dispatch(db, actor, dispatch_id, data.reason)
    db.commit()
    return jsonable(svc.dispatch_out(d))
