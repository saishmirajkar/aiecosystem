"""Production Agent tools (client/company-x). Read tools use the production service; the challan tool only drafts."""
from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import Field
from sqlalchemy import func, or_, select

from app.ai_tools.base import ToolContext, ToolError, ToolInput, ToolOutput, tool
from app.ai_tools.inventory_tools import _approval_card, fmt, resolve_product, src, table
from app.core.errors import NotFound
from app.models import OrderStatus, ProductionOrder, ProductionSite, SiteKind, Supplier, Warehouse
from app.modules.production import service as psvc
from app.orchestrator import approval_manager


def resolve_order(ctx: ToolContext, ref: str) -> ProductionOrder:
    ref = ref.strip()
    m = re.search(r"(?:MO-?)?0*(\d+)", ref, re.I)
    if m and (ref.upper().startswith("MO") or ref.isdigit()):
        number = f"MO-{int(m.group(1)):06d}"
        order = ctx.db.execute(select(ProductionOrder).where(ProductionOrder.order_number == number)).unique().scalar_one_or_none()
        if order:
            return order
        raise NotFound(f"Production order {number} not found")
    product = resolve_product(ctx, ref)
    order = ctx.db.execute(select(ProductionOrder).where(ProductionOrder.product_id == product.id,
                                                         ProductionOrder.status.notin_([OrderStatus.CANCELLED]))
                           .order_by(ProductionOrder.created_at.desc())).unique().scalars().first()
    if order is None:
        raise NotFound(f"No production order for {product.name}")
    return order


def resolve_vendor(ctx: ToolContext, ref: str) -> ProductionSite:
    like = f"%{ref.strip().lower()}%"
    site = ctx.db.execute(select(ProductionSite).join(Warehouse, Warehouse.id == ProductionSite.warehouse_id)
                          .outerjoin(Supplier, Supplier.id == ProductionSite.supplier_id)
                          .where(ProductionSite.kind == SiteKind.VENDOR,
                                 or_(func.lower(Warehouse.code) == ref.strip().lower(), func.lower(Warehouse.name).like(like),
                                     func.lower(Supplier.name).like(like), func.lower(Supplier.code) == ref.strip().lower()))).unique().scalars().first()
    if site is None:
        raise NotFound(f"No job-work vendor matches '{ref}'")
    return site


class ListOrdersIn(ToolInput):
    status: Literal["open", "completed", "all"] = "open"
    limit: int = Field(20, ge=1, le=50)


@tool("list_production_orders", "List production orders (open by default) with product, quantity, progress %, planned finish, "
      "due date and whether they are at risk or have late stages.", ListOrdersIn, "production.view")
def list_production_orders(ctx: ToolContext, a: ListOrdersIn) -> ToolOutput:
    stmt = select(ProductionOrder).order_by(ProductionOrder.created_at.desc()).limit(a.limit)
    if a.status == "open":
        stmt = stmt.where(ProductionOrder.status.in_([OrderStatus.PLANNED, OrderStatus.RELEASED, OrderStatus.IN_PROGRESS]))
    elif a.status == "completed":
        stmt = stmt.where(ProductionOrder.status == OrderStatus.COMPLETED)
    orders = list(ctx.db.execute(stmt).unique().scalars())
    rows = []
    for o in orders:
        r = psvc.order_row(ctx.db, o)
        rows.append({"order": r["order_number"], "product": r["product_name"], "qty": fmt(r["quantity"]), "status": r["status"],
                     "progress": f"{r['progress']}%", "finish": r["planned_finish"].isoformat() if r["planned_finish"] else "",
                     "due": r["due_date"].isoformat() if r["due_date"] else "", "risk": "at risk" if r["at_risk"] else "",
                     "late_stages": r["late_stages"]})
    summary = (f"{len(rows)} {a.status} production order(s): " + "; ".join(f"{r['order']} {r['product']} ×{r['qty']} ({r['progress']})"
               for r in rows[:8]) + ".") if rows else f"There are no {a.status} production orders."
    return ToolOutput({"orders": rows}, summary, table("Production orders", [("order", "Order"), ("product", "Product"), ("qty", "Qty"),
                      ("status", "Status"), ("progress", "Progress"), ("finish", "Planned finish"), ("due", "Due"), ("risk", "Risk"),
                      ("late_stages", "Late stages")], rows, "verified", [src("production_order", o.id, o.order_number) for o in orders]))


class OrderRefIn(ToolInput):
    order: str = Field(min_length=1, max_length=100, description="Order number (e.g. MO-000012 or 12) or product name/SKU for its latest order")


def _positions_rows(detail: dict) -> list[dict]:
    rows = []
    for p in detail["parts"]:
        for pos in p["positions"]:
            if pos["kind"] == "stock":
                rows.append({"part": p["name"], "where": pos["site_name"], "units": fmt(pos["units"]),
                             "state": f"bought-in: need {fmt(pos['required'])}" + (f", short {fmt(pos['short'])}" if pos["short"] > 0 else "")})
            else:
                state = pos["label"] + (" · OVERDUE" if pos.get("overdue") else "") + (f" · {fmt(pos['rework'])} in rework" if pos.get("rework") else "")
                rows.append({"part": p["name"], "where": pos["site_name"], "units": fmt(pos["units"]), "state": state})
    return rows


@tool("get_production_order", "Get one production order: status, progress, planned finish vs due date, every part with its "
      "stages and progress, and late stages.", OrderRefIn, "production.view")
def get_production_order(ctx: ToolContext, a: OrderRefIn) -> ToolOutput:
    o = resolve_order(ctx, a.order)
    d = psvc.order_detail(ctx.db, o)
    rows = []
    for p in d["parts"]:
        if p["source"] != "make":
            continue
        route = " → ".join(f"{s['name']} @ {s['site_name'].replace(' (job work)', '')} [{s['status']}{', LATE' if s['late'] else ''}]" for s in p["stages"])
        rows.append({"part": p["name"], "qty": fmt(p["quantity"]), "progress": f"{p['progress']}%", "route": route})
    late = [f"{p['name']} · {s['name']}" for p in d["parts"] for s in p["stages"] if s["late"]]
    summary = (f"{d['order_number']}: {fmt(d['quantity'])} × {d['product_name']} is {d['status'].replace('_', ' ')}, {d['progress']}% complete; "
               f"planned finish {d['planned_finish']}" + (f", due {d['due_date']}" if d["due_date"] else "")
               + (" (AT RISK)" if d["at_risk"] else "") + (f". Late stages: {', '.join(late)}." if late else ". No late stages."))
    card = table(f"{d['order_number']} · {d['product_name']} × {fmt(d['quantity'])} · {d['progress']}%", [("part", "Part"), ("qty", "Qty"),
                 ("progress", "Progress"), ("route", "Route and status")], rows, "verified", [src("production_order", o.id, o.order_number)])
    return ToolOutput({"order": d["order_number"], "status": d["status"], "progress": d["progress"], "planned_finish": d["planned_finish"],
                       "due_date": d["due_date"], "at_risk": d["at_risk"], "parts": rows, "late_stages": late}, summary, card)


class WhereIn(ToolInput):
    order: str = Field(min_length=1, max_length=100, description="Order number or product name/SKU")


@tool("where_are_parts", "Show where every part of a production order physically is right now: waiting at the plant, at which "
      "vendor (and since when, overdue or in rework), finished, used in assembly, or bought-in stock.", WhereIn, "production.view")
def where_are_parts(ctx: ToolContext, a: WhereIn) -> ToolOutput:
    o = resolve_order(ctx, a.order)
    d = psvc.order_detail(ctx.db, o)
    rows = _positions_rows(d)
    at_vendor = [r for r in rows if "At vendor" in r["state"]]
    summary = f"{d['order_number']} ({d['product_name']} × {fmt(d['quantity'])}): " + "; ".join(
        f"{r['part']}: {r['units']} {r['state'].lower()} at {r['where']}" for r in rows[:12]) + "."
    return ToolOutput({"order": d["order_number"], "positions": rows, "units_at_vendors": len(at_vendor)}, summary,
                      table(f"Where the parts are · {d['order_number']}", [("part", "Part"), ("where", "Where"), ("units", "Units"),
                            ("state", "State")], rows, "verified", [src("production_order", o.id, o.order_number)],
                            note="Computed from issued challans, vendor receipts and stage completions."))


class VendorIn(ToolInput):
    vendor: str | None = Field(None, max_length=100, description="Vendor name or code; omit for all vendors")


@tool("get_vendor_workload", "Show what each job-work vendor is holding: open jobs by order/part/stage, units, since when, "
      "overdue returns, material value at the vendor, rejection rate and average turnaround.", VendorIn, "production.view")
def get_vendor_workload(ctx: ToolContext, a: VendorIn) -> ToolOutput:
    site = resolve_vendor(ctx, a.vendor) if a.vendor else None
    wl = psvc.vendor_workload(ctx.db, site.warehouse_id if site else None)
    rows = []
    for w in wl:
        for j in w["open_jobs"]:
            rows.append({"vendor": w["name"].replace(" (job work)", ""), "order": j["order_number"], "part": j["part"], "stage": j["stage"],
                         "units": fmt(j["units"]), "since": j["since"].isoformat() if j["since"] else "",
                         "due_back": j["expected_return"].isoformat() if j["expected_return"] else "", "overdue": "yes" if j["overdue"] else ""})
    stats = [{"label": w["name"].replace(" (job work)", ""), "value": f"{fmt(w['units_held'])} units",
              "unit": f"rejection {w['rejection_rate']}%" if w["rejection_rate"] is not None else ""} for w in wl]
    summary = "; ".join(f"{w['name']}: {fmt(w['units_held'])} units on {len(w['open_jobs'])} job(s), {w['overdue_jobs']} overdue, "
                        f"material value {w['material_value']:.2f}" + (f", rejection rate {w['rejection_rate']}%" if w["rejection_rate"] is not None else "")
                        for w in wl) or "No job-work vendors are set up."
    return ToolOutput({"vendors": [{"name": w["name"], "units_held": fmt(w["units_held"]), "overdue_jobs": w["overdue_jobs"],
                                    "rejection_rate": w["rejection_rate"], "avg_turnaround_days": w["avg_turnaround_days"]} for w in wl],
                       "jobs": rows}, summary,
                      table("Material at job-work vendors", [("vendor", "Vendor"), ("order", "Order"), ("part", "Part"), ("stage", "Stage"),
                            ("units", "Units"), ("since", "Since"), ("due_back", "Due back"), ("overdue", "Overdue")], rows, "verified",
                            note="Material at a vendor remains company stock (valued in the ledger)."))


class EmptyIn(ToolInput):
    pass


@tool("get_delayed_stages", "List production stages that are past their planned end and not completed, most delayed first.",
      EmptyIn, "production.view")
def get_delayed_stages(ctx: ToolContext, a: EmptyIn) -> ToolOutput:
    rows = [{"order": r["order_number"], "part": r["part"], "stage": r["stage"], "where": r["where"].replace(" (job work)", ""),
             "planned_end": r["planned_end"].isoformat(), "days_late": r["days_late"],
             "done": f"{fmt(r['units_done'])}/{fmt(r['target'])}"} for r in psvc.delayed_stages(ctx.db)]
    summary = (f"{len(rows)} stage(s) are late: " + "; ".join(f"{r['order']} {r['part']} · {r['stage']} at {r['where']} ({r['days_late']} days)"
               for r in rows[:10]) + ".") if rows else "No production stages are late."
    return ToolOutput({"delayed": rows}, summary, table("Late production stages", [("order", "Order"), ("part", "Part"), ("stage", "Stage"),
                      ("where", "Where"), ("planned_end", "Planned end"), ("days_late", "Days late"), ("done", "Done")], rows, "calculated"))


class ChallanDraftIn(ToolInput):
    order: str = Field(min_length=1, max_length=100)
    part: str = Field(min_length=1, max_length=100, description="Part name or SKU within the order")
    stage: str | None = Field(None, max_length=120, description="Stage name; defaults to the next vendor stage with units waiting")
    vendor: str | None = Field(None, max_length=100, description="Vendor; defaults to the stage's planned vendor")
    units: Decimal = Field(gt=0, max_digits=18, decimal_places=4)


@tool("create_job_work_challan_draft", "PREPARE (not issue) a job-work challan sending units of a part to a vendor for a stage. "
      "Checks units waiting at the plant and creates a pending approval; no material moves until approved.",
      ChallanDraftIn, "production.execute", "draft")
def create_job_work_challan_draft(ctx: ToolContext, a: ChallanDraftIn) -> ToolOutput:
    o = resolve_order(ctx, a.order)
    d = psvc.order_detail(ctx.db, o)
    q = a.part.strip().lower()
    part = next((p for p in d["parts"] if p["source"] == "make" and (p["sku"].lower() == q or q in p["name"].lower())), None)
    if part is None:
        raise ToolError(f"No made part matching '{a.part}' in {d['order_number']}",
                        details={"candidates": [{"sku": p["sku"], "name": p["name"]} for p in d["parts"] if p["source"] == "make"]})
    stages = [s for s in part["stages"] if s["performer"] == "vendor"]
    stage = next((s for s in stages if a.stage and a.stage.lower() in s["name"].lower()), None) if a.stage else \
        next((s for s in stages if s["waiting_at_plant"] > 0), None)
    if stage is None:
        raise ToolError("No vendor stage with units waiting matches; specify the stage name",
                        details={"stages": [{"name": s["name"], "waiting": fmt(s["waiting_at_plant"])} for s in stages]})
    if a.units > stage["waiting_at_plant"]:
        raise ToolError(f"Only {fmt(stage['waiting_at_plant'])} units are waiting at the plant for {stage['name']}")
    vendor = resolve_vendor(ctx, a.vendor) if a.vendor else ctx.db.get(ProductionSite, stage["site_id"])
    params = {"stage_id": str(stage["id"]), "vendor_site_id": str(vendor.warehouse_id), "units": format(a.units, "f")}
    summary = f"Issue job-work challan: {fmt(a.units)} × {part['name']} to {vendor.warehouse.name} for {stage['name']} ({d['order_number']})"
    rows = [{"order": d["order_number"], "part": part["name"], "stage": stage["name"], "vendor": vendor.warehouse.name,
             "units": fmt(a.units), "waiting_at_plant": fmt(stage["waiting_at_plant"])}]
    appr = approval_manager.create(ctx.db, ctx.actor, "production.issue_challan", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval", "proposal": rows},
                      f"Prepared a challan draft ({summary}). It is awaiting approval; no material has moved.",
                      _approval_card(appr, "Proposed job-work challan", rows, [("order", "Order"), ("part", "Part"), ("stage", "Stage"),
                                     ("vendor", "Vendor"), ("units", "Units"), ("waiting_at_plant", "Waiting at plant")],
                                     "Approving issues the challan and moves the material to the vendor."))


@approval_manager.register_action("production.issue_challan", "production.execute")
def _exec_issue_challan(db, actor, p: dict) -> dict:
    from app.modules.production import schemas as ps
    ch = psvc.issue_challan(db, actor, uuid.UUID(p["stage_id"]), ps.ChallanIn(vendor_site_id=p["vendor_site_id"], units=p["units"]))
    return {"record_type": "job_work_challan", "record_id": str(ch.id), "number": ch.challan_number, "status": "issued",
            "link": f"/production/orders/{ch.order_id}"}

