"""Company X production flow.

Material model
--------------
* A *made* part has a bill of materials (its "kit") and a route of stages.
* Through every stage the physical material is the kit itself; it moves plant → vendor → plant/next vendor
  as JOB_WORK_OUT / JOB_WORK_RETURN location moves. The transformation into the part happens when the
  LAST stage completes: the kit is consumed and the part is produced at a value of
  (consumed kit value) + (sum of all stage rates) per unit — so vendor charges land in product cost.
* Scrapped units consume their kit as a PRODUCTION_SCRAP write-off at wherever the material is.
* Everything posts through the foundation ledger, so vendor stock is a real, valued balance.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import audit, diff, next_number
from app.core.database import ZERO, utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, Conflict, InvalidState, NotFound, ValidationFailed
from app.models import (
    BomLine, Customer, DispatchLine, DispatchNote, DispatchStatus, InventoryBalance, JobWorkChallan, JobWorkChallanLine,
    JobWorkReceipt, MovementType, OrderPart, OrderStage, OrderStatus, PartSource, Performer, Product, ProductionEvent,
    ProductionOrder, ProductionSite, ProductValuation, RouteStage, SiteKind, StageCompletion, StageStatus, StockStatus,
    Supplier, User, Warehouse,
)
from app.modules.inventory.ledger import Line, Posting, post
from app.modules.inventory.service import allocate, default_location
from app.modules.master_data import schemas as md
from app.modules.master_data import service as md_svc
from app.modules.production import schemas as s

Q4 = Decimal("0.0001")
Q6 = Decimal("0.000001")
ACTIVE_ORDER = (OrderStatus.RELEASED, OrderStatus.IN_PROGRESS)


# ---------------------------------------------------------------- helpers
def _get(db: Session, model, oid: uuid.UUID, label: str):
    obj = db.get(model, oid)
    if obj is None:
        raise NotFound(f"{label} not found")
    return obj


def site_of(db: Session, warehouse_id: uuid.UUID, kind: SiteKind | None = None) -> ProductionSite:
    site = db.get(ProductionSite, warehouse_id)
    if site is None:
        raise ValidationFailed("Location is not a plant or job-work vendor site")
    if kind and site.kind != kind:
        raise ValidationFailed(f"{site.warehouse.name} is not a {kind.value} site")
    return site


def kit_of(db: Session, product_id: uuid.UUID) -> list[BomLine]:
    return list(db.execute(select(BomLine).where(BomLine.product_id == product_id).order_by(BomLine.created_at)).scalars())


def route_of(db: Session, product_id: uuid.UUID) -> list[RouteStage]:
    return list(db.execute(select(RouteStage).where(RouteStage.product_id == product_id).order_by(RouteStage.sequence)).scalars())


def _event(db: Session, actor: Actor | None, order: ProductionOrder, event_type: str, message: str, *,
           part: OrderPart | None = None, stage: OrderStage | None = None, units: Decimal | None = None,
           site_id: uuid.UUID | None = None) -> None:
    db.add(ProductionEvent(order_id=order.id, part_id=part.id if part else (stage.part_id if stage else None),
                           stage_id=stage.id if stage else None, event_type=event_type, message=message, units=units,
                           site_id=site_id, user_id=actor.user_id if actor else None))


def fmt(v: Decimal) -> str:
    q = v.quantize(Q4).normalize()
    return format(q, "f") if q != q.to_integral() else format(q.quantize(Decimal(1)), "f")


# ---------------------------------------------------------------- sites
def site_out(db: Session, site: ProductionSite) -> dict[str, Any]:
    return {"warehouse_id": site.warehouse_id, "kind": site.kind.value, "code": site.warehouse.code, "name": site.warehouse.name,
            "city": site.warehouse.city, "supplier_id": site.supplier_id,
            "supplier_name": site.supplier.name if site.supplier else None, "is_active": site.warehouse.is_active}


def list_sites(db: Session, kind: SiteKind | None = None) -> list[ProductionSite]:
    stmt = select(ProductionSite).join(Warehouse, Warehouse.id == ProductionSite.warehouse_id).order_by(ProductionSite.kind, Warehouse.code)
    if kind:
        stmt = stmt.where(ProductionSite.kind == kind)
    return list(db.execute(stmt).scalars())


def create_plant(db: Session, actor: Actor, data: s.PlantIn) -> ProductionSite:
    wh = md_svc.create_warehouse(db, actor, md.WarehouseIn(code=data.code, name=data.name, city=data.city))
    site = ProductionSite(warehouse_id=wh.id, kind=SiteKind.PLANT)
    db.add(site)
    db.flush()
    audit(db, actor, "production.site_create", "production_site", wh.id, {"kind": "plant", "code": wh.code})
    return site


def designate_plant(db: Session, actor: Actor, warehouse_id: uuid.UUID) -> ProductionSite:
    wh = _get(db, Warehouse, warehouse_id, "Warehouse")
    if db.get(ProductionSite, wh.id):
        raise Conflict(f"{wh.code} is already a production site")
    site = ProductionSite(warehouse_id=wh.id, kind=SiteKind.PLANT)
    db.add(site)
    db.flush()
    audit(db, actor, "production.site_designate", "production_site", wh.id, {"kind": "plant"})
    return site


def create_vendor_site(db: Session, actor: Actor, supplier_id: uuid.UUID) -> ProductionSite:
    sup = _get(db, Supplier, supplier_id, "Supplier")
    if db.execute(select(ProductionSite).where(ProductionSite.supplier_id == sup.id)).scalar_one_or_none():
        raise Conflict(f"{sup.name} already has a job-work location")
    code = ("V-" + re.sub(r"[^A-Z0-9_-]", "", sup.code.upper()))[:20]
    wh = md_svc.create_warehouse(db, actor, md.WarehouseIn(code=code, name=f"{sup.name} (job work)", city=sup.city))
    site = ProductionSite(warehouse_id=wh.id, kind=SiteKind.VENDOR, supplier_id=sup.id)
    db.add(site)
    db.flush()
    audit(db, actor, "production.site_create", "production_site", wh.id, {"kind": "vendor", "supplier": sup.code})
    return site


# ---------------------------------------------------------------- customers
def create_customer(db: Session, actor: Actor, data: s.CustomerIn) -> Customer:
    if db.execute(select(Customer).where(func.lower(Customer.code) == data.code.lower())).first():
        raise Conflict(f"Customer code '{data.code}' already exists")
    c = Customer(**data.model_dump(), created_by=actor.user_id, updated_by=actor.user_id)
    db.add(c)
    db.flush()
    audit(db, actor, "customer.create", "customer", c.id, data.model_dump())
    return c


def update_customer(db: Session, actor: Actor, cid: uuid.UUID, data: s.CustomerUpdate) -> Customer:
    c = _get(db, Customer, cid, "Customer")
    changes = diff(c, data.model_dump(exclude_unset=True))
    c.updated_by = actor.user_id
    audit(db, actor, "customer.update", "customer", c.id, changes)
    return c


# ---------------------------------------------------------------- product structure
def structure_out(db: Session, product: Product) -> dict[str, Any]:
    return {
        "product_id": product.id, "sku": product.sku, "name": product.name, "uom": product.uom.code,
        "bom": [{"id": b.id, "component_id": b.component_id, "component_sku": b.component.sku, "component_name": b.component.name,
                 "uom": b.component.uom.code, "quantity": b.quantity, "notes": b.notes,
                 "component_is_made": bool(route_of(db, b.component_id))} for b in kit_of(db, product.id)],
        "route": [{"id": r.id, "sequence": r.sequence, "name": r.name, "performer": r.performer.value, "site_id": r.site_id,
                   "site_name": db.get(Warehouse, r.site_id).name if r.site_id else None, "rate_per_unit": r.rate_per_unit,
                   "planned_days": r.planned_days, "notes": r.notes} for r in route_of(db, product.id)],
    }


def _check_cycle(db: Session, root: uuid.UUID, component: uuid.UUID, depth: int = 0) -> None:
    if component == root:
        raise ValidationFailed("Bill of materials cannot contain the product itself (directly or indirectly)")
    if depth > 10:
        raise ValidationFailed("Bill of materials is nested too deeply (max 10 levels)")
    for line in kit_of(db, component):
        _check_cycle(db, root, line.component_id, depth + 1)


def _validate_stage_site(db: Session, performer: Performer, site_id: uuid.UUID | None) -> None:
    if site_id is None:
        if performer == Performer.VENDOR:
            raise ValidationFailed("A vendor stage needs a job-work vendor")
        return
    site_of(db, site_id, SiteKind.VENDOR if performer == Performer.VENDOR else SiteKind.PLANT)


def set_structure(db: Session, actor: Actor, product_id: uuid.UUID, data: s.StructureIn) -> Product:
    product = _get(db, Product, product_id, "Product")
    ids = [b.component_id for b in data.bom]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("Each component may appear only once")
    for b in data.bom:
        _get(db, Product, b.component_id, "Component")
        _check_cycle(db, product.id, b.component_id)
    if data.route and not data.bom:
        raise ValidationFailed("A product with a route needs a bill of materials (the material its stages work on)")
    for st in data.route:
        _validate_stage_site(db, st.performer, st.site_id)
    for old in kit_of(db, product.id) + route_of(db, product.id):
        db.delete(old)
    db.flush()
    for b in data.bom:
        db.add(BomLine(product_id=product.id, **b.model_dump()))
    for n, st in enumerate(data.route, start=1):
        db.add(RouteStage(product_id=product.id, sequence=n, **st.model_dump()))
    db.flush()
    audit(db, actor, "production.structure_update", "product", product.id,
          {"bom": [b.model_dump() for b in data.bom], "route": [r.model_dump() for r in data.route]})
    return product


# ---------------------------------------------------------------- orders: creation (expand + schedule)
def _expand(db: Session, order: ProductionOrder, product: Product, qty: Decimal, qty_per_parent: Decimal,
            parent: OrderPart | None, level: int, counter: list[int]) -> OrderPart:
    if level > 10:
        raise ValidationFailed("Product structure is nested too deeply")
    stages = route_of(db, product.id)
    part = OrderPart(order_id=order.id, parent_id=parent.id if parent else None, position=counter[0], level=level,
                     product_id=product.id, quantity=qty, qty_per_parent=qty_per_parent,
                     source=PartSource.MAKE if stages else PartSource.BUY)
    counter[0] += 1
    db.add(part)
    db.flush()
    if stages:
        kit = kit_of(db, product.id)
        if not kit:
            raise ValidationFailed(f"{product.sku} has a route but no bill of materials")
        for st in stages:
            db.add(OrderStage(part_id=part.id, sequence=st.sequence, name=st.name, performer=st.performer,
                              site_id=st.site_id or order.plant_id, rate_per_unit=st.rate_per_unit, status=StageStatus.PLANNED,
                              notes=st.notes))
        db.flush()
        for line in kit:
            _expand(db, order, line.component, (qty * line.quantity).quantize(Q4), line.quantity, part, level + 1, counter)
    return part


def _schedule(db: Session, order: ProductionOrder) -> None:
    """Children first; a parent's first stage starts the day after its last made child finishes."""
    children: dict[uuid.UUID | None, list[OrderPart]] = defaultdict(list)
    for p in order.parts:
        children[p.parent_id].append(p)

    def plan(part: OrderPart) -> date:
        start = order.start_date
        for child in children.get(part.id, []):
            if child.source == PartSource.MAKE:
                start = max(start, plan(child) + timedelta(days=1))
        cursor = start
        route = {r.sequence: r.planned_days for r in route_of(db, part.product_id)}
        end = cursor - timedelta(days=1)
        for st in part.stages:
            days = max(route.get(st.sequence, 1), 1)
            st.planned_start = cursor
            st.planned_end = cursor + timedelta(days=days - 1)
            end = st.planned_end
            cursor = st.planned_end + timedelta(days=1)
        return end

    for root in children.get(None, []):
        plan(root)


def create_order(db: Session, actor: Actor, data: s.OrderCreate) -> ProductionOrder:
    product = _get(db, Product, data.product_id, "Product")
    if not route_of(db, product.id):
        raise ValidationFailed(f"{product.sku} has no route; define its bill of materials and route first")
    site_of(db, data.plant_id, SiteKind.PLANT)
    if data.customer_id:
        _get(db, Customer, data.customer_id, "Customer")
    start = data.start_date or utcnow().date()
    if data.due_date and data.due_date < start:
        raise ValidationFailed("Due date cannot be before the start date")
    order = ProductionOrder(order_number=next_number(db, "production_order"), product_id=product.id, quantity=data.quantity,
                            customer_id=data.customer_id, plant_id=data.plant_id, start_date=start, due_date=data.due_date,
                            status=OrderStatus.PLANNED, notes=data.notes, created_by=actor.user_id, updated_by=actor.user_id)
    db.add(order)
    db.flush()
    _expand(db, order, product, data.quantity, Decimal(1), None, 0, [1])
    db.flush()
    db.refresh(order)
    _schedule(db, order)
    _event(db, actor, order, "created", f"Order created for {fmt(data.quantity)} × {product.name}")
    audit(db, actor, "production.order_create", "production_order", order.id, {"number": order.order_number})
    return order


def get_order(db: Session, order_id: uuid.UUID, lock: bool = False) -> ProductionOrder:
    stmt = select(ProductionOrder).where(ProductionOrder.id == order_id)
    if lock:
        stmt = stmt.with_for_update(of=ProductionOrder)
    order = db.execute(stmt).unique().scalar_one_or_none()
    if order is None:
        raise NotFound("Production order not found")
    return order


def release_order(db: Session, actor: Actor, order_id: uuid.UUID) -> ProductionOrder:
    order = get_order(db, order_id, lock=True)
    if order.status != OrderStatus.PLANNED:
        raise InvalidState(f"Order is already {order.status.value}")
    order.status, order.released_at = OrderStatus.RELEASED, utcnow()
    _event(db, actor, order, "released", "Order released to the plant")
    audit(db, actor, "production.order_release", "production_order", order.id)
    db.flush()
    return order


def _has_activity(db: Session, order: ProductionOrder) -> bool:
    stage_ids = [st.id for p in order.parts for st in p.stages]
    if not stage_ids:
        return False
    return bool(db.execute(select(JobWorkChallan.id).where(JobWorkChallan.order_id == order.id)).first()
                or db.execute(select(StageCompletion.id).where(StageCompletion.stage_id.in_(stage_ids))).first())


def cancel_order(db: Session, actor: Actor, order_id: uuid.UUID, reason: str) -> ProductionOrder:
    order = get_order(db, order_id, lock=True)
    if order.status not in (OrderStatus.PLANNED, OrderStatus.RELEASED):
        raise InvalidState(f"Cannot cancel an order that is {order.status.value}")
    if _has_activity(db, order):
        raise BusinessRuleViolation("Material has already moved for this order; it cannot be cancelled")
    order.status, order.cancel_reason = OrderStatus.CANCELLED, reason
    _event(db, actor, order, "cancelled", f"Order cancelled: {reason}")
    audit(db, actor, "production.order_cancel", "production_order", order.id, {"reason": reason})
    db.flush()
    return order


def _stage(db: Session, stage_id: uuid.UUID) -> tuple[OrderStage, OrderPart, ProductionOrder]:
    stage = _get(db, OrderStage, stage_id, "Stage")
    part = db.get(OrderPart, stage.part_id)
    order = get_order(db, part.order_id, lock=True)
    return stage, part, order


def _stage_has_activity(db: Session, stage: OrderStage) -> bool:
    return stage.status != StageStatus.PLANNED or bool(
        db.execute(select(JobWorkChallan.id).where(JobWorkChallan.stage_id == stage.id)).first())


def update_stage(db: Session, actor: Actor, stage_id: uuid.UUID, data: s.StageUpdate) -> OrderStage:
    stage, part, order = _stage(db, stage_id)
    if order.status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED):
        raise InvalidState("Order is closed")
    updates = data.model_dump(exclude_unset=True)
    routing_change = {"performer", "site_id", "rate_per_unit"} & set(updates)
    if routing_change and _stage_has_activity(db, stage):
        raise InvalidState("This stage has started; only its name, dates and notes can change")
    performer = updates.get("performer", stage.performer)
    if "performer" in updates or "site_id" in updates:
        site_id = updates.get("site_id") or (order.plant_id if performer == Performer.PLANT else None)
        _validate_stage_site(db, performer, site_id)
        updates["site_id"] = site_id
    start, end = updates.get("planned_start", stage.planned_start), updates.get("planned_end", stage.planned_end)
    if start and end and end < start:
        raise ValidationFailed("Planned end cannot be before planned start")
    changes = diff(stage, updates)
    _event(db, actor, order, "stage_updated", f"{part.product.name} · {stage.name} updated", stage=stage)
    audit(db, actor, "production.stage_update", "order_stage", stage.id, changes)
    return stage


def add_stage(db: Session, actor: Actor, part_id: uuid.UUID, data: s.StageAdd) -> OrderStage:
    part = _get(db, OrderPart, part_id, "Part")
    order = get_order(db, part.order_id, lock=True)
    if part.source != PartSource.MAKE or order.status in (OrderStatus.COMPLETED, OrderStatus.CANCELLED):
        raise InvalidState("Stages can only be added to made parts of an open order")
    later = [st for st in part.stages if st.sequence > data.after_sequence]
    if any(_stage_has_activity(db, st) for st in later):
        raise InvalidState("A new stage can only be inserted before stages that have not started")
    site_id = data.site_id or (order.plant_id if data.performer == Performer.PLANT else None)
    _validate_stage_site(db, data.performer, site_id)
    for st in sorted(later, key=lambda x: -x.sequence):
        st.sequence += 1
    db.flush()
    prev = next((st for st in part.stages if st.sequence == data.after_sequence), None)
    start = (prev.planned_end + timedelta(days=1)) if prev and prev.planned_end else order.start_date
    stage = OrderStage(part_id=part.id, sequence=data.after_sequence + 1, name=data.name, performer=data.performer, site_id=site_id,
                       rate_per_unit=data.rate_per_unit, status=StageStatus.PLANNED, notes=data.notes, planned_start=start,
                       planned_end=start + timedelta(days=max(data.planned_days, 1) - 1))
    db.add(stage)
    db.flush()
    _event(db, actor, order, "stage_added", f"{part.product.name}: stage '{data.name}' added", stage=stage)
    audit(db, actor, "production.stage_add", "order_stage", stage.id, data.model_dump())
    return stage


def remove_stage(db: Session, actor: Actor, stage_id: uuid.UUID) -> None:
    stage, part, order = _stage(db, stage_id)
    if _stage_has_activity(db, stage):
        raise InvalidState("A stage that has started cannot be removed")
    if len(part.stages) == 1:
        raise BusinessRuleViolation("A made part needs at least one stage")
    seq = stage.sequence
    _event(db, actor, order, "stage_removed", f"{part.product.name}: stage '{stage.name}' removed")
    audit(db, actor, "production.stage_remove", "order_stage", stage.id, {"name": stage.name})
    db.delete(stage)
    db.flush()
    for st in sorted(part.stages, key=lambda x: x.sequence):
        if st.sequence > seq:
            st.sequence -= 1


# ---------------------------------------------------------------- tracking (computed from documents)
def _inflow(prev: OrderStage | None, target: Decimal, to_plant: dict[uuid.UUID, Decimal]) -> Decimal:
    """Units that have reached the plant ready for this stage."""
    if prev is None:
        return target
    return to_plant[prev.id] if prev.performer == Performer.VENDOR else prev.units_completed


def stage_stats(db: Session, order: ProductionOrder) -> dict[uuid.UUID, dict[str, Any]]:
    """Per stage: inflow, waiting at plant, units at each vendor, accepted, scrapped, rework."""
    stage_ids = [st.id for p in order.parts for st in p.stages]
    sent: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    first_sent: dict[tuple, date] = {}
    expected: dict[tuple, date | None] = {}
    for c in db.execute(select(JobWorkChallan).where(JobWorkChallan.stage_id.in_(stage_ids))).unique().scalars() if stage_ids else []:
        k = (c.stage_id, c.vendor_site_id)
        sent[k] += c.units
        first_sent[k] = min(first_sent.get(k, c.challan_date), c.challan_date)
        expected[k] = max(filter(None, [expected.get(k), c.expected_return_date]), default=None)
    acc: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    scr: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    rew: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    direct_in: dict[tuple, Decimal] = defaultdict(lambda: ZERO)  # (next_stage_id, vendor) → units shipped direct
    to_plant: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
    next_stage = {}
    for p in order.parts:
        for a, b in zip(p.stages, p.stages[1:]):
            next_stage[a.id] = b
    for r in db.execute(select(JobWorkReceipt).where(JobWorkReceipt.stage_id.in_(stage_ids))).unique().scalars() if stage_ids else []:
        k = (r.stage_id, r.vendor_site_id)
        acc[k] += r.units_accepted
        scr[k] += r.units_scrapped
        rew[k] += r.units_rework
        if r.direct_to_next and r.stage_id in next_stage:
            direct_in[(next_stage[r.stage_id].id, r.receive_to_site_id)] += r.units_accepted
            if r.receive_to_site_id:
                first_sent.setdefault((next_stage[r.stage_id].id, r.receive_to_site_id), r.received_date)
        else:
            to_plant[r.stage_id] += r.units_accepted
    names = {w.id: w for w in db.execute(select(Warehouse)).scalars()}
    out: dict[uuid.UUID, dict[str, Any]] = {}
    for p in order.parts:
        prev: OrderStage | None = None
        scrapped_before = ZERO
        for st in p.stages:
            target = p.quantity - scrapped_before
            if st.performer == Performer.VENDOR:
                vendors = {v for (sid, v) in list(sent) + list(direct_in) + list(acc) if sid == st.id and v}
                at_vendor = []
                for v in sorted(vendors, key=str):
                    held = sent[(st.id, v)] + direct_in[(st.id, v)] - acc[(st.id, v)] - scr[(st.id, v)]
                    at_vendor.append({"site_id": v, "site_name": names[v].name if v in names else "?", "units": held,
                                      "sent": sent[(st.id, v)] + direct_in[(st.id, v)], "accepted": acc[(st.id, v)],
                                      "scrapped": scr[(st.id, v)], "rework": rew[(st.id, v)],
                                      "since": first_sent.get((st.id, v)), "expected_return": expected.get((st.id, v))})
                waiting = _inflow(prev, target, to_plant) - sum((sent[(st.id, v)] for v in vendors), ZERO)
            else:
                at_vendor = []
                waiting = _inflow(prev, target, to_plant) - st.units_completed - st.units_scrapped
            out[st.id] = {"target": target, "waiting_at_plant": max(waiting, ZERO), "at_vendors": at_vendor,
                          "accepted": st.units_completed, "scrapped": st.units_scrapped,
                          "rework": sum((v["rework"] for v in at_vendor), ZERO)}
            scrapped_before += st.units_scrapped
            prev = st
    return out


def _available_at(db: Session, product_id: uuid.UUID, warehouse_id: uuid.UUID) -> Decimal:
    B = InventoryBalance
    return db.execute(select(func.coalesce(func.sum(B.qty_on_hand - B.qty_reserved), 0)).where(
        B.product_id == product_id, B.warehouse_id == warehouse_id, B.stock_status == StockStatus.UNRESTRICTED)).scalar_one()


def order_detail(db: Session, order: ProductionOrder) -> dict[str, Any]:
    today = utcnow().date()
    stats = stage_stats(db, order)
    by_id = {p.id: p for p in order.parts}
    parts_out, total_w, done_w = [], ZERO, ZERO
    for p in order.parts:
        stages_out, positions = [], []
        n = len(p.stages)
        for st in p.stages:
            sst = stats[st.id]
            late = st.status != StageStatus.COMPLETED and st.planned_end is not None and st.planned_end < today
            stages_out.append({
                "id": st.id, "sequence": st.sequence, "name": st.name, "performer": st.performer.value, "site_id": st.site_id,
                "site_name": st.site.name, "rate_per_unit": st.rate_per_unit, "planned_start": st.planned_start,
                "planned_end": st.planned_end, "actual_start": st.actual_start, "actual_end": st.actual_end,
                "status": st.status.value, "late": late, "is_last": st.sequence == n, "target": sst["target"],
                "units_completed": st.units_completed, "units_scrapped": st.units_scrapped, "waiting_at_plant": sst["waiting_at_plant"],
                "at_vendors": sst["at_vendors"], "rework": sst["rework"], "notes": st.notes,
            })
            if sst["waiting_at_plant"] > 0:
                fresh = st.sequence == 1 and st.status == StageStatus.PLANNED
                positions.append({"kind": "not_started" if fresh else "waiting",
                                  "label": "Not started" if fresh else f"Waiting at plant for {st.name}",
                                  "site_name": order.plant.name, "units": sst["waiting_at_plant"], "stage": st.name})
            for v in sst["at_vendors"]:
                if v["units"] > 0:
                    positions.append({"kind": "at_vendor", "label": f"At vendor for {st.name}", "site_name": v["site_name"],
                                      "units": v["units"], "stage": st.name, "since": v["since"], "rework": v["rework"],
                                      "expected_return": v["expected_return"],
                                      "overdue": bool(v["expected_return"] and v["expected_return"] < today)})
            total_w += sst["target"] if sst["target"] > 0 else ZERO
            done_w += min(st.units_completed, sst["target"]) if sst["target"] > 0 else ZERO
        if p.source == PartSource.MAKE and p.stages:
            last = p.stages[-1]
            finished = last.units_completed
            consumed = ZERO
            parent = by_id.get(p.parent_id)
            if parent and parent.stages:
                pl = parent.stages[-1]
                consumed = min(finished, ((pl.units_completed + sum((x.units_scrapped for x in parent.stages), ZERO)) * p.qty_per_parent).quantize(Q4))
            if finished - consumed > 0:
                positions.append({"kind": "done", "label": "Finished goods" if p.parent_id is None else "Finished, ready for assembly",
                                  "site_name": order.plant.name, "units": finished - consumed, "stage": last.name})
            if consumed > 0:
                positions.append({"kind": "consumed", "label": f"Used in {parent.product.name}", "site_name": order.plant.name,
                                  "units": consumed, "stage": None})
        else:
            avail = _available_at(db, p.product_id, order.plant_id)
            positions.append({"kind": "stock", "label": "Bought-in, from stock", "site_name": order.plant.name,
                              "units": avail, "required": p.quantity, "short": max(p.quantity - avail, ZERO)})
        kit_status = []
        if p.source == PartSource.MAKE and p.stages and order.status in (OrderStatus.PLANNED, *ACTIVE_ORDER):
            first = stats[p.stages[0].id]["waiting_at_plant"]
            for line in kit_of(db, p.product_id):
                need = (first * line.quantity).quantize(Q4)
                have = _available_at(db, line.component_id, order.plant_id)
                kit_status.append({"sku": line.component.sku, "name": line.component.name, "uom": line.component.uom.code,
                                   "needed": need, "available_at_plant": have, "short": max(need - have, ZERO)})
        part_done = sum((min(st.units_completed, stats[st.id]["target"]) for st in p.stages), ZERO)
        part_total = sum((stats[st.id]["target"] for st in p.stages), ZERO)
        parts_out.append({
            "id": p.id, "parent_id": p.parent_id, "level": p.level, "product_id": p.product_id, "sku": p.product.sku,
            "name": p.product.name, "uom": p.product.uom.code, "quantity": p.quantity, "qty_per_parent": p.qty_per_parent,
            "source": p.source.value, "stages": stages_out, "positions": positions, "kit": kit_status,
            "progress": float((part_done / part_total * 100).quantize(Decimal("0.1"))) if part_total else (100.0 if p.source == PartSource.BUY else 0.0),
            "conversion_cost_per_unit": sum((st.rate_per_unit for st in p.stages), ZERO),
        })
    root_last = next((p.stages[-1] for p in order.parts if p.parent_id is None and p.stages), None)
    events = db.execute(select(ProductionEvent).where(ProductionEvent.order_id == order.id).order_by(ProductionEvent.created_at.desc()).limit(100)).scalars().all()
    users = dict(db.execute(select(User.id, User.full_name)).all())
    return {
        "id": order.id, "order_number": order.order_number, "product_id": order.product_id, "product_sku": order.product.sku,
        "product_name": order.product.name, "uom": order.product.uom.code, "quantity": order.quantity,
        "customer_id": order.customer_id, "customer_name": order.customer.name if order.customer else None,
        "plant_id": order.plant_id, "plant_name": order.plant.name, "start_date": order.start_date, "due_date": order.due_date,
        "status": order.status.value, "notes": order.notes, "released_at": order.released_at, "completed_at": order.completed_at,
        "cancel_reason": order.cancel_reason, "created_at": order.created_at,
        "progress": float((done_w / total_w * 100).quantize(Decimal("0.1"))) if total_w else 0.0,
        "planned_finish": root_last.planned_end if root_last else None,
        "at_risk": bool(order.due_date and root_last and root_last.planned_end and root_last.planned_end > order.due_date
                        and order.status not in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)),
        "late_stages": sum(1 for p in parts_out for st in p["stages"] if st["late"]),
        "parts": parts_out,
        "events": [{"id": e.id, "event_type": e.event_type, "message": e.message, "units": e.units, "stage_id": e.stage_id,
                    "user_name": users.get(e.user_id), "created_at": e.created_at} for e in events],
    }


def order_row(db: Session, order: ProductionOrder) -> dict[str, Any]:
    d = order_detail(db, order)
    return {k: d[k] for k in ("id", "order_number", "product_sku", "product_name", "uom", "quantity", "customer_name", "plant_name",
                              "start_date", "due_date", "status", "progress", "planned_finish", "at_risk", "late_stages", "created_at")}


# ---------------------------------------------------------------- execution: postings
def _require_active(order: ProductionOrder) -> None:
    if order.status not in ACTIVE_ORDER:
        raise InvalidState("Release the order before moving material" if order.status == OrderStatus.PLANNED
                           else f"Order is {order.status.value}")


def _kit_lines(db: Session, part: OrderPart, units: Decimal) -> list[tuple[Product, Decimal]]:
    return [(line.component, (units * line.quantity).quantize(Q4)) for line in kit_of(db, part.product_id)]


def _avg_cost(db: Session, product_id: uuid.UUID) -> Decimal:
    v = db.get(ProductValuation, product_id)
    return v.average_cost if v else ZERO


def _touch(stage: OrderStage, order: ProductionOrder) -> None:
    if stage.status == StageStatus.PLANNED:
        stage.status, stage.actual_start = StageStatus.IN_PROGRESS, utcnow()
    if order.status == OrderStatus.RELEASED:
        order.status = OrderStatus.IN_PROGRESS


def _finish_check(db: Session, actor: Actor, stage: OrderStage, part: OrderPart, order: ProductionOrder, target: Decimal) -> None:
    if stage.units_completed + stage.units_scrapped >= target and stage.status != StageStatus.COMPLETED:
        stage.status, stage.actual_end = StageStatus.COMPLETED, utcnow()
        _event(db, actor, order, "stage_completed", f"{part.product.name} · {stage.name} completed", stage=stage)
        if part.parent_id is None and stage.sequence == len(part.stages):
            order.status, order.completed_at = OrderStatus.COMPLETED, utcnow()
            _event(db, actor, order, "completed", f"Order completed: {fmt(stage.units_completed)} × {part.product.name} finished")


def _transform(db: Session, actor: Actor, order: ProductionOrder, part: OrderPart, stage: OrderStage, units: Decimal,
               at_location: uuid.UUID, out_location: uuid.UUID, ref: str) -> list[uuid.UUID]:
    """Consume the kit for `units` at `at_location`, produce the part at `out_location` (kit value + stage rates)."""
    lines = [Line(product_id=c.id, location_id=at_location, quantity=-q, movement_type=MovementType.PRODUCTION_CONSUME,
                  reason=f"{order.order_number} · {part.product.sku}") for c, q in _kit_lines(db, part, units) if q > 0]
    tx1 = post(db, actor, Posting(MovementType.PRODUCTION_CONSUME, lines, reference_type="production_order", reference_id=order.id,
                                  reference_number=order.order_number, reason=f"Consumed for {part.product.sku} ({ref})"))
    kit_value = -sum((m.value for m in tx1.movements), ZERO)
    conversion = sum((st.rate_per_unit for st in part.stages), ZERO)
    unit_cost = (kit_value / units + conversion).quantize(Q6, rounding=ROUND_HALF_UP)
    tx2 = post(db, actor, Posting(MovementType.PRODUCTION_OUTPUT, [Line(
        product_id=part.product_id, location_id=out_location, quantity=units, movement_type=MovementType.PRODUCTION_OUTPUT,
        unit_cost=unit_cost, reason=f"{order.order_number} output")], reference_type="production_order", reference_id=order.id,
        reference_number=order.order_number, reason=f"Produced {part.product.sku} ({ref})"))
    return [tx1.id, tx2.id]


def _scrap(db: Session, actor: Actor, order: ProductionOrder, part: OrderPart, units: Decimal, location: uuid.UUID, reason: str) -> uuid.UUID:
    lines = [Line(product_id=c.id, location_id=location, quantity=-q, movement_type=MovementType.PRODUCTION_SCRAP, reason=reason)
             for c, q in _kit_lines(db, part, units) if q > 0]
    tx = post(db, actor, Posting(MovementType.PRODUCTION_SCRAP, lines, reference_type="production_order", reference_id=order.id,
                                 reference_number=order.order_number, reason=f"Scrap: {reason}"))
    return tx.id


def issue_challan(db: Session, actor: Actor, stage_id: uuid.UUID, data: s.ChallanIn) -> JobWorkChallan:
    stage, part, order = _stage(db, stage_id)
    _require_active(order)
    if stage.performer != Performer.VENDOR:
        raise BusinessRuleViolation("This stage is done at the plant; record completion instead of sending it out")
    vendor_id = data.vendor_site_id or stage.site_id
    site_of(db, vendor_id, SiteKind.VENDOR)
    waiting = stage_stats(db, order)[stage.id]["waiting_at_plant"]
    if data.units > waiting:
        raise BusinessRuleViolation(f"Only {fmt(waiting)} units are waiting at the plant for {stage.name}",
                                    details={"waiting": format(waiting, "f")})
    plant_loc, vendor_loc = default_location(db, order.plant_id), default_location(db, vendor_id)
    kit = _kit_lines(db, part, data.units)
    lines: list[Line] = []
    for comp, qty in kit:
        lines.append(Line(product_id=comp.id, location_id=plant_loc.id, quantity=-qty, movement_type=MovementType.JOB_WORK_OUT,
                          counterpart_location_id=vendor_loc.id))
        lines.append(Line(product_id=comp.id, location_id=vendor_loc.id, quantity=qty, movement_type=MovementType.JOB_WORK_OUT,
                          counterpart_location_id=plant_loc.id))
    number = next_number(db, "job_work_challan")
    tx = post(db, actor, Posting(MovementType.JOB_WORK_OUT, lines, reference_type="job_work_challan", reference_number=number,
                                 reason=f"{order.order_number} · {part.product.sku} · {stage.name}"))
    ch = JobWorkChallan(challan_number=number, order_id=order.id, stage_id=stage.id, from_site_id=order.plant_id,
                        vendor_site_id=vendor_id, units=data.units, challan_date=utcnow().date(),
                        expected_return_date=data.expected_return_date or stage.planned_end, vehicle_number=data.vehicle_number,
                        notes=data.notes, transaction_id=tx.id, created_by=actor.user_id, updated_by=actor.user_id)
    ch.lines = [JobWorkChallanLine(product_id=c.id, quantity=q, unit_value=_avg_cost(db, c.id)) for c, q in kit]
    db.add(ch)
    _touch(stage, order)
    db.flush()
    vendor = db.get(Warehouse, vendor_id)
    _event(db, actor, order, "sent_to_vendor", f"{fmt(data.units)} × {part.product.name} sent to {vendor.name} for {stage.name} ({number})",
           stage=stage, units=data.units, site_id=vendor_id)
    audit(db, actor, "production.challan_issue", "job_work_challan", ch.id, {"number": number, "units": data.units})
    return ch


def record_receipt(db: Session, actor: Actor, stage_id: uuid.UUID, data: s.ReceiptIn) -> JobWorkReceipt:
    stage, part, order = _stage(db, stage_id)
    _require_active(order)
    if stage.performer != Performer.VENDOR:
        raise BusinessRuleViolation("This stage is done at the plant")
    site_of(db, data.vendor_site_id, SiteKind.VENDOR)
    st = stage_stats(db, order)[stage.id]
    held = next((v["units"] for v in st["at_vendors"] if v["site_id"] == data.vendor_site_id), ZERO)
    if data.units_accepted + data.units_scrapped + data.units_rework > held:
        raise BusinessRuleViolation(f"The vendor holds {fmt(held)} units for this stage", details={"held": format(held, "f")})
    is_last = stage.sequence == len(part.stages)
    vendor_loc = default_location(db, data.vendor_site_id)
    nxt = next((x for x in part.stages if x.sequence == stage.sequence + 1), None)
    if data.direct_to_next:
        if is_last or nxt is None or nxt.performer != Performer.VENDOR:
            raise BusinessRuleViolation("Direct shipment is only possible when the next stage is at a vendor")
        dest_site = data.receive_to_site_id or nxt.site_id
        site_of(db, dest_site, SiteKind.VENDOR)
        if dest_site == data.vendor_site_id:
            raise BusinessRuleViolation("Next vendor must differ from the current one")
    else:
        dest_site = data.receive_to_site_id or order.plant_id
        if db.get(ProductionSite, dest_site) and db.get(ProductionSite, dest_site).kind == SiteKind.VENDOR:
            raise ValidationFailed("Use 'ship directly to next vendor' to send material to another vendor")
    dest_loc = default_location(db, dest_site)
    number = next_number(db, "job_work_receipt")
    tx_ids: list[uuid.UUID] = []
    if data.units_scrapped > 0:
        tx_ids.append(_scrap(db, actor, order, part, data.units_scrapped, vendor_loc.id, f"{number}: {data.scrap_reason}"))
    if data.units_accepted > 0:
        if is_last:
            tx_ids += _transform(db, actor, order, part, stage, data.units_accepted, vendor_loc.id, dest_loc.id, number)
        else:
            lines: list[Line] = []
            for comp, qty in _kit_lines(db, part, data.units_accepted):
                lines.append(Line(product_id=comp.id, location_id=vendor_loc.id, quantity=-qty, movement_type=MovementType.JOB_WORK_RETURN,
                                  counterpart_location_id=dest_loc.id))
                lines.append(Line(product_id=comp.id, location_id=dest_loc.id, quantity=qty, movement_type=MovementType.JOB_WORK_RETURN,
                                  counterpart_location_id=vendor_loc.id))
            tx_ids.append(post(db, actor, Posting(MovementType.JOB_WORK_RETURN, lines, reference_type="job_work_receipt",
                                                  reference_number=number, reason=f"{order.order_number} · {stage.name}")).id)
    r = JobWorkReceipt(receipt_number=number, order_id=order.id, stage_id=stage.id, vendor_site_id=data.vendor_site_id,
                       receive_to_site_id=dest_site, direct_to_next=data.direct_to_next, units_accepted=data.units_accepted,
                       units_rework=data.units_rework, units_scrapped=data.units_scrapped, scrap_reason=data.scrap_reason,
                       vendor_document_number=data.vendor_document_number, received_date=data.received_date or utcnow().date(),
                       notes=data.notes, transaction_ids=[str(t) for t in tx_ids], created_by=actor.user_id, updated_by=actor.user_id)
    db.add(r)
    stage.units_completed += data.units_accepted
    stage.units_scrapped += data.units_scrapped
    _touch(stage, order)
    vendor, dest = db.get(Warehouse, data.vendor_site_id), db.get(Warehouse, dest_site)
    bits = []
    if data.units_accepted:
        bits.append(f"{fmt(data.units_accepted)} accepted" + (f", shipped directly to {dest.name}" if data.direct_to_next else f" into {dest.name}"))
    if data.units_rework:
        bits.append(f"{fmt(data.units_rework)} held for rework")
    if data.units_scrapped:
        bits.append(f"{fmt(data.units_scrapped)} scrapped ({data.scrap_reason})")
    _event(db, actor, order, "received_from_vendor", f"{part.product.name} · {stage.name} from {vendor.name}: " + "; ".join(bits) + f" ({number})",
           stage=stage, units=data.units_accepted, site_id=data.vendor_site_id)
    if data.direct_to_next and nxt is not None and data.units_accepted > 0:
        _touch(nxt, order)
        _event(db, actor, order, "sent_to_vendor", f"{fmt(data.units_accepted)} × {part.product.name} arrived at {dest.name} for {nxt.name}",
               stage=nxt, units=data.units_accepted, site_id=dest_site)
    _finish_check(db, actor, stage, part, order, st["target"])
    db.flush()
    audit(db, actor, "production.receipt", "job_work_receipt", r.id, data.model_dump())
    return r


def complete_in_house(db: Session, actor: Actor, stage_id: uuid.UUID, data: s.CompletionIn) -> StageCompletion:
    stage, part, order = _stage(db, stage_id)
    _require_active(order)
    if stage.performer != Performer.PLANT:
        raise BusinessRuleViolation("This stage is at a vendor; send material and record receipts instead")
    st = stage_stats(db, order)[stage.id]
    if data.units_completed + data.units_scrapped > st["waiting_at_plant"]:
        raise BusinessRuleViolation(f"Only {fmt(st['waiting_at_plant'])} units are available for {stage.name}",
                                    details={"waiting": format(st["waiting_at_plant"], "f")})
    plant_loc = default_location(db, order.plant_id)
    if data.units_scrapped > 0:
        _scrap(db, actor, order, part, data.units_scrapped, plant_loc.id, f"{stage.name}: {data.scrap_reason}")
    if data.units_completed > 0 and stage.sequence == len(part.stages):
        _transform(db, actor, order, part, stage, data.units_completed, plant_loc.id, plant_loc.id, stage.name)
    comp = StageCompletion(stage_id=stage.id, units_completed=data.units_completed, units_scrapped=data.units_scrapped,
                           scrap_reason=data.scrap_reason, notes=data.notes, completed_by=actor.user_id)
    db.add(comp)
    stage.units_completed += data.units_completed
    stage.units_scrapped += data.units_scrapped
    _touch(stage, order)
    msg = f"{part.product.name} · {stage.name}: {fmt(data.units_completed)} completed at {order.plant.name}"
    if data.units_scrapped:
        msg += f", {fmt(data.units_scrapped)} scrapped ({data.scrap_reason})"
    _event(db, actor, order, "stage_progress", msg, stage=stage, units=data.units_completed, site_id=order.plant_id)
    _finish_check(db, actor, stage, part, order, st["target"])
    db.flush()
    audit(db, actor, "production.stage_complete", "order_stage", stage.id, data.model_dump())
    return comp


# ---------------------------------------------------------------- vendor workload & summaries
def vendor_workload(db: Session, vendor_site_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    today = utcnow().date()
    sites = [x for x in list_sites(db, SiteKind.VENDOR) if vendor_site_id is None or x.warehouse_id == vendor_site_id]
    open_orders = list(db.execute(select(ProductionOrder).where(ProductionOrder.status.in_(ACTIVE_ORDER))).unique().scalars())
    holdings: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    for o in open_orders:
        stats = stage_stats(db, o)
        for p in o.parts:
            for st in p.stages:
                for v in stats[st.id]["at_vendors"]:
                    if v["units"] > 0:
                        holdings[v["site_id"]].append({
                            "order_id": o.id, "order_number": o.order_number, "part": p.product.name, "sku": p.product.sku,
                            "stage": st.name, "stage_id": st.id, "units": v["units"], "rework": v["rework"], "since": v["since"],
                            "expected_return": v["expected_return"],
                            "overdue": bool(v["expected_return"] and v["expected_return"] < today)})
    out = []
    for site in sites:
        rec = db.execute(select(func.coalesce(func.sum(JobWorkReceipt.units_accepted), 0), func.coalesce(func.sum(JobWorkReceipt.units_rework), 0),
                                func.coalesce(func.sum(JobWorkReceipt.units_scrapped), 0)).where(JobWorkReceipt.vendor_site_id == site.warehouse_id)).one()
        processed = rec[0] + rec[1] + rec[2]
        turnaround = db.execute(select(func.avg(JobWorkReceipt.received_date - JobWorkChallan.challan_date))
                                .join(JobWorkChallan, (JobWorkChallan.stage_id == JobWorkReceipt.stage_id) & (JobWorkChallan.vendor_site_id == JobWorkReceipt.vendor_site_id))
                                .where(JobWorkReceipt.vendor_site_id == site.warehouse_id)).scalar()
        value = db.execute(select(func.coalesce(func.sum(InventoryBalance.qty_on_hand * ProductValuation.average_cost), 0))
                           .join(ProductValuation, ProductValuation.product_id == InventoryBalance.product_id)
                           .where(InventoryBalance.warehouse_id == site.warehouse_id)).scalar_one()
        items = holdings.get(site.warehouse_id, [])
        out.append({**site_out(db, site), "open_jobs": items, "units_held": sum((i["units"] for i in items), ZERO),
                    "overdue_jobs": sum(1 for i in items if i["overdue"]), "material_value": Decimal(value).quantize(Q4),
                    "rejection_rate": float(((rec[1] + rec[2]) / processed * 100).quantize(Decimal("0.1"))) if processed else None,
                    "units_processed": processed,
                    "avg_turnaround_days": round(float(turnaround), 1) if turnaround is not None else None})
    return out


def delayed_stages(db: Session) -> list[dict[str, Any]]:
    today = utcnow().date()
    rows = []
    for o in db.execute(select(ProductionOrder).where(ProductionOrder.status.in_(ACTIVE_ORDER))).unique().scalars():
        for p in o.parts:
            for st in p.stages:
                if st.status != StageStatus.COMPLETED and st.planned_end and st.planned_end < today:
                    rows.append({"order_id": o.id, "order_number": o.order_number, "part": p.product.name, "stage": st.name,
                                 "where": st.site.name, "planned_end": st.planned_end, "days_late": (today - st.planned_end).days,
                                 "status": st.status.value, "units_done": st.units_completed, "target": p.quantity})
    rows.sort(key=lambda r: -r["days_late"])
    return rows


def summary(db: Session) -> dict[str, Any]:
    open_orders = list(db.execute(select(ProductionOrder).where(ProductionOrder.status.in_((OrderStatus.PLANNED, *ACTIVE_ORDER)))).unique().scalars())
    workload = vendor_workload(db)
    at_risk = 0
    for o in open_orders:
        d = order_detail(db, o)
        at_risk += 1 if d["at_risk"] else 0
    return {"open_orders": len(open_orders), "late_stages": len(delayed_stages(db)), "orders_at_risk": at_risk,
            "units_at_vendors": sum((w["units_held"] for w in workload), ZERO),
            "value_at_vendors": sum((w["material_value"] for w in workload), ZERO),
            "overdue_vendor_jobs": sum(w["overdue_jobs"] for w in workload)}


def challan_out(db: Session, c: JobWorkChallan) -> dict[str, Any]:
    stage = db.get(OrderStage, c.stage_id)
    part = db.get(OrderPart, stage.part_id)
    order = db.get(ProductionOrder, c.order_id)
    sup = db.get(ProductionSite, c.vendor_site_id)
    from app.modules.erp_core.service import get_company
    company = get_company(db)
    return {"id": c.id, "challan_number": c.challan_number, "order_id": c.order_id, "order_number": order.order_number,
            "part_name": part.product.name, "part_sku": part.product.sku, "stage_name": stage.name, "units": c.units,
            "from_site": c.from_site.name, "vendor_site_id": c.vendor_site_id, "vendor_name": c.vendor_site.name,
            "vendor_address": sup.supplier.address if sup and sup.supplier else None,
            "vendor_gstin": sup.supplier.tax_id if sup and sup.supplier else None,
            "challan_date": c.challan_date, "expected_return_date": c.expected_return_date, "vehicle_number": c.vehicle_number,
            "notes": c.notes, "created_at": c.created_at,
            "company": {"name": company.name, "address": ", ".join(filter(None, [company.address_line1, company.city, company.state])),
                        "tax_id": company.tax_id} if company else None,
            "lines": [{"sku": l.product.sku, "name": l.product.name, "uom": l.product.uom.code, "quantity": l.quantity,
                       "unit_value": l.unit_value, "value": (l.quantity * l.unit_value).quantize(Q4)} for l in c.lines],
            "total_value": sum(((l.quantity * l.unit_value) for l in c.lines), ZERO).quantize(Q4)}


# ---------------------------------------------------------------- dispatch
def dispatch_out(d: DispatchNote) -> dict[str, Any]:
    return {**{k: getattr(d, k) for k in ("id", "dispatch_number", "customer_id", "order_id", "warehouse_id", "dispatch_date",
                                          "vehicle_number", "transporter", "customer_reference", "notes", "dispatched_at",
                                          "transaction_id", "created_at")},
            "status": d.status.value, "customer_name": d.customer.name, "warehouse_code": d.warehouse.code,
            "lines": [{"id": l.id, "product_id": l.product_id, "sku": l.product.sku, "name": l.product.name, "uom": l.product.uom.code,
                       "quantity": l.quantity} for l in d.lines]}


def create_dispatch(db: Session, actor: Actor, data: s.DispatchCreate) -> DispatchNote:
    customer = _get(db, Customer, data.customer_id, "Customer")
    if not customer.is_active:
        raise BusinessRuleViolation("Customer is inactive")
    wh = _get(db, Warehouse, data.warehouse_id, "Warehouse")
    actor.require_warehouse(wh.id)
    if data.order_id:
        _get(db, ProductionOrder, data.order_id, "Production order")
    d = DispatchNote(dispatch_number=next_number(db, "dispatch_note"), customer_id=customer.id, order_id=data.order_id,
                     warehouse_id=wh.id, status=DispatchStatus.DRAFT, dispatch_date=data.dispatch_date or utcnow().date(),
                     vehicle_number=data.vehicle_number, transporter=data.transporter, customer_reference=data.customer_reference,
                     notes=data.notes, created_by=actor.user_id, updated_by=actor.user_id)
    for l in data.lines:
        _get(db, Product, l.product_id, "Product")
        d.lines.append(DispatchLine(product_id=l.product_id, quantity=l.quantity))
    db.add(d)
    db.flush()
    audit(db, actor, "dispatch.create", "dispatch_note", d.id, {"number": d.dispatch_number})
    return d


def dispatch(db: Session, actor: Actor, dispatch_id: uuid.UUID) -> DispatchNote:
    d = db.execute(select(DispatchNote).where(DispatchNote.id == dispatch_id).with_for_update(of=DispatchNote)).unique().scalar_one_or_none()
    if d is None:
        raise NotFound("Dispatch note not found")
    if d.status != DispatchStatus.DRAFT:
        raise InvalidState(f"Dispatch note is already {d.status.value}")
    lines: list[Line] = []
    for l in d.lines:
        for bal, take in allocate(db, l.product, d.warehouse_id, l.quantity):
            lines.append(Line(product_id=l.product_id, location_id=bal.location_id, quantity=-take, batch_id=bal.batch_id,
                              movement_type=MovementType.DISPATCH))
    tx = post(db, actor, Posting(MovementType.DISPATCH, lines, reference_type="dispatch_note", reference_id=d.id,
                                 reference_number=d.dispatch_number, reason=f"Dispatched to {d.customer.name}"))
    d.status, d.dispatched_at, d.transaction_id = DispatchStatus.DISPATCHED, utcnow(), tx.id
    if d.order_id:
        order = db.get(ProductionOrder, d.order_id)
        _event(db, actor, order, "dispatched", f"Dispatched to {d.customer.name} ({d.dispatch_number})")
    audit(db, actor, "dispatch.dispatch", "dispatch_note", d.id, {"transaction": tx.transaction_number})
    return d


def cancel_dispatch(db: Session, actor: Actor, dispatch_id: uuid.UUID, reason: str) -> DispatchNote:
    d = _get(db, DispatchNote, dispatch_id, "Dispatch note")
    if d.status != DispatchStatus.DRAFT:
        raise InvalidState("Only draft dispatch notes can be cancelled")
    d.status = DispatchStatus.CANCELLED
    d.notes = ((d.notes + "\n") if d.notes else "") + f"Cancelled: {reason}"
    audit(db, actor, "dispatch.cancel", "dispatch_note", d.id, {"reason": reason})
    return d
