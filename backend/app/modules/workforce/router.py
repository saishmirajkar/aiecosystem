"""Workforce REST API (all under /api/v1). Authorization is enforced in the services through the actor's scope."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy import select

from app.core.common import audit, diff, jsonable
from app.core.database import utcnow
from app.core.deps import DB, Actor, CurrentActor, require_any_permission, require_permission
from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.core.idempotency import get_key, run_idempotent
from app.models import (
    Attachment, Department, HalfDay, Holiday, LeaveType, OvertimePolicy, ShiftPattern, ShiftTemplate, Team, WorkforcePolicy,
)
from app.modules.erp_core.attachments import save_upload, storage_dir
from app.modules.workforce import attendance as att
from app.modules.workforce import leave as lv
from app.modules.workforce import marketplace as mk
from app.modules.workforce import overtime as ot
from app.modules.workforce import people as pp
from app.modules.workforce import reports as rp
from app.modules.workforce import schemas as s
from app.modules.workforce import shifts as sh
from app.modules.workforce.core import company_tz, get_employee, get_scope, require_me
from app.modules.workforce.eligibility import evaluate, window_for_shift
from app.modules.workforce.notify import channel_statuses

router = APIRouter(tags=["workforce"])
ANY = require_any_permission("workforce.self", "workforce.view")
VIEW = require_permission("workforce.view")
CONFIG = require_permission("workforce.config")


def _today(db) -> date:
    return utcnow().astimezone(company_tz(db)).date()


def ok(db, payload: Any) -> Any:
    db.commit()
    return jsonable(payload)


# ------------------------------------------------------------------ me
@router.get("/workforce/me")
def me(db: DB, actor: Actor = Depends(ANY)) -> dict:
    return jsonable(rp.employee_home(db, get_scope(db, actor)))


@router.get("/workforce/me/schedule")
def my_schedule(db: DB, actor: Actor = Depends(ANY), date_from: date | None = None, date_to: date | None = None) -> dict:
    d0 = date_from or _today(db)
    return jsonable(sh.calendar(db, get_scope(db, actor), d0, date_to or d0 + timedelta(days=27), mine=True))


# ------------------------------------------------------------------ reference data
def _ref_routes(kind: str) -> None:
    model = pp.REF_MODELS[kind]
    schema = {"locations": s.LocationIn, "designations": s.DesignationIn, "skills": s.SkillIn}[kind]

    @router.get(f"/workforce/{kind}", name=f"list_{kind}")
    def list_ref(db: DB, actor: Actor = Depends(ANY), include_inactive: bool = False) -> list:
        stmt = select(model).order_by(model.name)
        if not include_inactive:
            stmt = stmt.where(model.is_active.is_(True))
        return jsonable([pp.ref_out(x) for x in db.execute(stmt).unique().scalars()])

    def create_ref(data, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
        return ok(db, pp.ref_out(pp.create_ref(db, actor, kind, data)))

    # Annotations are strings under `from __future__ import annotations`; give FastAPI the real body type.
    create_ref.__annotations__["data"] = schema
    router.post(f"/workforce/{kind}", status_code=201, name=f"create_{kind}")(create_ref)

    @router.patch(f"/workforce/{kind}/{{obj_id}}", name=f"update_{kind}")
    def update_ref(obj_id: uuid.UUID, data: s.RefUpdate, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
        return ok(db, pp.ref_out(pp.update_ref(db, actor, kind, obj_id, data)))


for _kind in ("locations", "designations", "skills"):
    _ref_routes(_kind)


@router.get("/workforce/teams")
def list_teams(db: DB, actor: Actor = Depends(ANY), department_id: uuid.UUID | None = None) -> list:
    stmt = select(Team).where(Team.is_active.is_(True)).order_by(Team.name)
    if department_id:
        stmt = stmt.where(Team.department_id == department_id)
    detail = actor.has("workforce.view")
    rows = db.execute(stmt).unique().scalars().all()
    return jsonable([pp.team_out(t, db) if detail else {"id": t.id, "code": t.code, "name": t.name, "department_id": t.department_id}
                     for t in rows])


@router.post("/workforce/teams", status_code=201)
def create_team(data: s.TeamIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    t = pp.create_team(db, actor, data)
    db.flush()
    return ok(db, pp.team_out(t, db))


@router.patch("/workforce/teams/{team_id}")
def update_team(team_id: uuid.UUID, data: s.TeamUpdate, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    t = pp.update_team(db, actor, team_id, data)
    db.flush()
    return ok(db, pp.team_out(t, db))


@router.get("/workforce/holidays")
def list_holidays(db: DB, actor: Actor = Depends(ANY), year: int | None = None) -> list:
    y = year or _today(db).year
    rows = db.execute(select(Holiday).where(Holiday.holiday_date >= date(y, 1, 1), Holiday.holiday_date <= date(y, 12, 31))
                      .order_by(Holiday.holiday_date)).scalars().all()
    return jsonable([{"id": h.id, "holiday_date": h.holiday_date, "name": h.name, "work_location_id": h.work_location_id} for h in rows])


@router.post("/workforce/holidays", status_code=201)
def create_holiday(data: s.HolidayIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    h = pp.create_holiday(db, actor, data)
    return ok(db, {"id": h.id, "holiday_date": h.holiday_date, "name": h.name, "work_location_id": h.work_location_id})


@router.delete("/workforce/holidays/{holiday_id}", status_code=204)
def delete_holiday(holiday_id: uuid.UUID, db: DB, actor: Actor = Depends(CONFIG)) -> Response:
    pp.delete_holiday(db, actor, holiday_id)
    db.commit()
    return Response(status_code=204)


@router.get("/shift-templates")
def list_templates(db: DB, actor: Actor = Depends(ANY), department_id: uuid.UUID | None = None, include_inactive: bool = False) -> list:
    stmt = select(ShiftTemplate).order_by(ShiftTemplate.start_time)
    if not include_inactive:
        stmt = stmt.where(ShiftTemplate.is_active.is_(True))
    if department_id:
        stmt = stmt.where(ShiftTemplate.department_id == department_id)
    return jsonable([sh.template_out(t) for t in db.execute(stmt).unique().scalars()])


@router.post("/shift-templates", status_code=201)
def create_template(data: s.TemplateIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, sh.template_out(sh.create_template(db, actor, data)))


@router.patch("/shift-templates/{template_id}")
def update_template(template_id: uuid.UUID, data: s.TemplateUpdate, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, sh.template_out(sh.update_template(db, actor, template_id, data)))


@router.get("/shift-patterns")
def list_patterns(db: DB, actor: Actor = Depends(VIEW)) -> list:
    return jsonable([sh.pattern_out(p) for p in db.execute(select(ShiftPattern).order_by(ShiftPattern.name)).unique().scalars()])


@router.post("/shift-patterns", status_code=201)
def create_pattern(data: s.PatternIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, sh.pattern_out(sh.save_pattern(db, actor, data)))


@router.put("/shift-patterns/{pattern_id}")
def update_pattern(pattern_id: uuid.UUID, data: s.PatternIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, sh.pattern_out(sh.save_pattern(db, actor, data, pattern_id)))


POLICY_FIELDS = [f for f in s.WorkforcePolicyIn.model_fields if f != "department_id"]


def _policy_out(db, p: WorkforcePolicy) -> dict:
    return {"id": p.id, "department_id": p.department_id,
            "department_name": db.get(Department, p.department_id).name if p.department_id else None,
            **{f: getattr(p, f) for f in POLICY_FIELDS}}


@router.get("/workforce/policies")
def list_policies(db: DB, actor: Actor = Depends(ANY)) -> list:
    rows = db.execute(select(WorkforcePolicy).order_by(WorkforcePolicy.department_id.is_not(None))).scalars().all()
    return jsonable([_policy_out(db, p) for p in rows])


@router.put("/workforce/policies")
def save_policy(data: s.WorkforcePolicyIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    p = db.execute(select(WorkforcePolicy).where(WorkforcePolicy.department_id.is_(None) if data.department_id is None
                                                 else WorkforcePolicy.department_id == data.department_id)).scalar_one_or_none()
    if data.department_id and db.get(Department, data.department_id) is None:
        raise ValidationFailed("Department not found")
    if p is None:
        p = WorkforcePolicy(department_id=data.department_id, created_by=actor.user_id)
        db.add(p)
    changes = diff(p, data.model_dump(exclude={"department_id"}))
    p.updated_by = actor.user_id
    db.flush()
    audit(db, actor, "workforce.policy.save", "workforce_policy", p.id, {"department_id": data.department_id, **changes})
    return ok(db, _policy_out(db, p))


@router.delete("/workforce/policies/{policy_id}", status_code=204)
def delete_policy(policy_id: uuid.UUID, db: DB, actor: Actor = Depends(CONFIG)) -> Response:
    p = db.get(WorkforcePolicy, policy_id)
    if p is None or p.department_id is None:
        raise ValidationFailed("Only a department override can be removed")
    audit(db, actor, "workforce.policy.delete", "workforce_policy", p.id, {"department_id": p.department_id})
    db.delete(p)
    db.commit()
    return Response(status_code=204)


OT_FIELDS = list(s.OvertimePolicyIn.model_fields)


def _ot_out(p: OvertimePolicy) -> dict:
    return {"id": p.id, "is_active": p.is_active, **{f: getattr(p, f) for f in OT_FIELDS}}


@router.get("/overtime/policies")
def list_ot_policies(db: DB, actor: Actor = Depends(ANY)) -> list:
    return jsonable([_ot_out(p) for p in db.execute(select(OvertimePolicy).order_by(OvertimePolicy.code)).scalars()])


def _save_ot(db, actor, data: s.OvertimePolicyIn, p: OvertimePolicy | None) -> OvertimePolicy:
    if data.is_default:
        for other in db.execute(select(OvertimePolicy).where(OvertimePolicy.is_default.is_(True))).scalars():
            if p is None or other.id != p.id:
                other.is_default = False
        db.flush()
    if p is None:
        if db.execute(select(OvertimePolicy).where(OvertimePolicy.code == data.code)).first():
            raise ValidationFailed(f"Code '{data.code}' already exists")
        p = OvertimePolicy(**data.model_dump(), created_by=actor.user_id)
        db.add(p)
        changes = data.model_dump()
    else:
        changes = diff(p, data.model_dump(exclude={"code"}))
    db.flush()
    audit(db, actor, "workforce.overtime_policy.save", "overtime_policy", p.id, changes)
    return p


@router.post("/overtime/policies", status_code=201)
def create_ot_policy(data: s.OvertimePolicyIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, _ot_out(_save_ot(db, actor, data, None)))


@router.put("/overtime/policies/{policy_id}")
def update_ot_policy(policy_id: uuid.UUID, data: s.OvertimePolicyIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    p = db.get(OvertimePolicy, policy_id)
    if p is None:
        raise NotFound("Overtime policy not found")
    return ok(db, _ot_out(_save_ot(db, actor, data, p)))


@router.get("/workforce/notification-channels")
def notification_channels(actor: CurrentActor) -> list:
    return channel_statuses()


# ------------------------------------------------------------------ employees
@router.get("/employees")
def list_employees(db: DB, actor: Actor = Depends(ANY), q: str | None = None, department_id: uuid.UUID | None = None,
                   team_id: uuid.UUID | None = None, status: str | None = None, page: int = Query(1, ge=1),
                   page_size: int = Query(50, ge=1, le=200)) -> dict:
    return jsonable(pp.list_employees(db, get_scope(db, actor), q=q, department_id=department_id, team_id=team_id, status=status,
                                      page=page, page_size=page_size))


@router.post("/employees", status_code=201)
def create_employee(data: s.EmployeeCreate, db: DB, actor: Actor = Depends(require_permission("workforce.employees.manage"))) -> dict:
    e = pp.create_employee(db, actor, data)
    db.flush()
    db.refresh(e)
    return ok(db, pp.employee_out(db, e, get_scope(db, actor), detail=True))


@router.get("/employees/{employee_id}")
def get_employee_detail(employee_id: uuid.UUID, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    e = get_employee(db, employee_id)
    scope.require_see(e)
    out = pp.employee_out(db, e, scope, detail=True)
    out["leave_balances"] = lv.balances_out(db, e, _today(db).year)
    db.commit()
    return jsonable(out)


@router.patch("/employees/{employee_id}")
def update_employee(employee_id: uuid.UUID, data: s.EmployeeUpdate, db: DB,
                    actor: Actor = Depends(require_permission("workforce.employees.manage"))) -> dict:
    e = pp.update_employee(db, actor, employee_id, data)
    db.flush()
    db.refresh(e)
    return ok(db, pp.employee_out(db, e, get_scope(db, actor), detail=True))


@router.post("/employees/{employee_id}/portal-access", status_code=201)
def portal_access(employee_id: uuid.UUID, data: s.PortalAccessIn, db: DB,
                  actor: Actor = Depends(require_permission("workforce.employees.manage"))) -> dict:
    e = pp.grant_portal_access(db, actor, employee_id, data)
    return ok(db, pp.employee_out(db, e, get_scope(db, actor), detail=True))


@router.post("/employees/{employee_id}/link-user")
def link_user(employee_id: uuid.UUID, data: s.LinkUserIn, db: DB,
              actor: Actor = Depends(require_permission("workforce.employees.manage"))) -> dict:
    e = pp.link_user(db, actor, employee_id, data.user_id)
    return ok(db, pp.employee_out(db, e, get_scope(db, actor), detail=True))


# ------------------------------------------------------------------ shifts
@router.get("/shifts")
def list_shifts(db: DB, actor: Actor = Depends(ANY), date_from: date | None = None, date_to: date | None = None,
                department_id: uuid.UUID | None = None) -> list:
    d0 = date_from or _today(db)
    return jsonable(sh.list_shifts(db, get_scope(db, actor), d0, date_to or d0 + timedelta(days=6), department_id))


@router.get("/shifts/calendar")
def shift_calendar(db: DB, actor: Actor = Depends(ANY), date_from: date | None = None, date_to: date | None = None,
                   department_id: uuid.UUID | None = None, mine: bool = False) -> dict:
    d0 = date_from or _today(db)
    return jsonable(sh.calendar(db, get_scope(db, actor), d0, date_to or d0 + timedelta(days=6), department_id, mine))


@router.post("/shifts", status_code=201)
def create_shift(data: s.ShiftCreate, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.shift_out(db, sh.create_shift(db, actor, scope, data)))


@router.get("/shifts/{shift_id}")
def get_shift(shift_id: uuid.UUID, db: DB, actor: Actor = Depends(VIEW)) -> dict:
    scope = get_scope(db, actor)
    shift = sh.get_shift(db, shift_id)
    if not scope.manages_department(shift.department_id):
        raise NotFound("Shift not found")
    return jsonable(sh.shift_out(db, shift))


@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: uuid.UUID, data: s.ShiftUpdate, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.shift_out(db, sh.update_shift(db, actor, scope, shift_id, data)))


@router.post("/shifts/{shift_id}/cancel")
def cancel_shift(shift_id: uuid.UUID, data: s.CancelIn, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.shift_out(db, sh.cancel_shift(db, actor, scope, shift_id, data.reason)))


@router.get("/shifts/{shift_id}/candidates")
def shift_candidates(shift_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> list:
    scope = get_scope(db, actor)
    shift = sh.get_shift(db, shift_id)
    scope.require_department(shift.department_id, "workforce.schedule")
    return jsonable(_candidates(db, scope, window_for_shift(shift), shift.department_id, "schedule"))


def _candidates(db, scope, w, department_id, context) -> list[dict]:
    from app.models import Employee
    from app.modules.workforce.core import employee_brief, workforce_policy
    policy = workforce_policy(db, department_id)
    rows = db.execute(scope.employee_filter(select(Employee).where(Employee.status == "active")).order_by(Employee.first_name)).unique().scalars().all()
    out = []
    for e in rows:
        fails = evaluate(db, e, w, policy, context=context)
        out.append({"employee": employee_brief(e), "eligible": not fails, "failures": [f.as_dict() for f in fails],
                    "same_department": e.department_id == department_id})
    out.sort(key=lambda x: (not x["same_department"], not x["eligible"], x["employee"]["full_name"]))
    return out


@router.post("/shifts/{shift_id}/assignments", status_code=201)
def assign(shift_id: uuid.UUID, data: s.AssignIn, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.assignment_out(db, sh.assign(db, actor, scope, shift_id, data)))


@router.post("/assignments/{assignment_id}/release")
def release(assignment_id: uuid.UUID, data: s.ReleaseIn, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.assignment_out(db, sh.release(db, actor, scope, assignment_id, data)))


@router.post("/shifts/generate")
def generate(data: s.GenerateIn, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    return ok(db, sh.generate(db, actor, get_scope(db, actor), data))


@router.post("/shifts/copy-week")
def copy_week(data: s.CopyWeekIn, db: DB, actor: Actor = Depends(require_permission("workforce.schedule"))) -> dict:
    return ok(db, sh.copy_week(db, actor, get_scope(db, actor), data))


# ------------------------------------------------------------------ leave
@router.get("/leave/types")
def leave_types(db: DB, actor: Actor = Depends(ANY), include_inactive: bool = False) -> list:
    stmt = select(LeaveType).order_by(LeaveType.code)
    if not include_inactive:
        stmt = stmt.where(LeaveType.is_active.is_(True))
    return jsonable([lv.type_out(t) for t in db.execute(stmt).scalars()])


@router.post("/leave/types", status_code=201)
def create_leave_type(data: s.LeaveTypeIn, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, lv.type_out(lv.create_type(db, actor, data)))


@router.patch("/leave/types/{type_id}")
def update_leave_type(type_id: uuid.UUID, data: s.LeaveTypeUpdate, db: DB, actor: Actor = Depends(CONFIG)) -> dict:
    return ok(db, lv.type_out(lv.update_type(db, actor, type_id, data)))


@router.get("/leave/balances")
def leave_balances(db: DB, actor: Actor = Depends(ANY), employee_id: uuid.UUID | None = None, year: int | None = None) -> list:
    scope = get_scope(db, actor)
    emp = get_employee(db, employee_id) if employee_id else require_me(scope)
    scope.require_see(emp)
    return ok(db, lv.balances_out(db, emp, year or _today(db).year))


@router.post("/leave/balances/adjust")
def adjust_balance(data: s.BalanceAdjustIn, db: DB, actor: Actor = Depends(require_permission("workforce.employees.manage"))) -> dict:
    lv.adjust_balance(db, actor, data)
    return ok(db, {"ok": True})


@router.get("/leave/preview")
def leave_preview(start_date: date, end_date: date, db: DB, actor: Actor = Depends(ANY), half_day: HalfDay = HalfDay.NONE,
                  employee_id: uuid.UUID | None = None) -> dict:
    return jsonable(lv.preview(db, get_scope(db, actor), employee_id, start_date, end_date, half_day))


@router.get("/leave/requests")
def leave_requests(db: DB, actor: Actor = Depends(ANY), status: str | None = None, mine: bool = False, employee_id: uuid.UUID | None = None,
                   date_from: date | None = None, date_to: date | None = None, page: int = Query(1, ge=1),
                   page_size: int = Query(25, ge=1, le=100)) -> dict:
    return jsonable(lv.list_requests(db, get_scope(db, actor), status=status, mine=mine, employee_id=employee_id, date_from=date_from,
                                     date_to=date_to, page=page, page_size=page_size))


@router.post("/leave/requests", status_code=201)
def create_leave(data: s.LeaveCreate, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    req = lv.create(db, actor, scope, data)
    return ok(db, lv.request_out(db, req, scope))


@router.get("/leave/requests/{request_id}")
def get_leave(request_id: uuid.UUID, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    return jsonable(lv.request_out(db, lv.get_request(db, scope, request_id), scope))


@router.post("/leave/requests/{request_id}/submit")
def submit_leave(request_id: uuid.UUID, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    return ok(db, lv.request_out(db, lv.submit(db, actor, scope, request_id), scope))


@router.post("/leave/requests/{request_id}/approve")
def approve_leave(request_id: uuid.UUID, db: DB, data: s.DecisionIn | None = None,
                  actor: Actor = Depends(require_permission("workforce.leave.approve"))) -> dict:
    scope = get_scope(db, actor)
    res = lv.approve(db, actor, scope, request_id, data.note if data else None)
    return ok(db, {**lv.request_out(db, res["request"], scope),
                   "opportunities_created": [{"id": o.id, "number": o.number, "status": o.status.value, "title": o.title}
                                             for o in res["opportunities"]]})


@router.post("/leave/requests/{request_id}/reject")
def reject_leave(request_id: uuid.UUID, data: s.RejectIn, db: DB, actor: Actor = Depends(require_permission("workforce.leave.approve"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, lv.request_out(db, lv.reject(db, actor, scope, request_id, data.note), scope))


@router.post("/leave/requests/{request_id}/cancel")
def cancel_leave(request_id: uuid.UUID, data: s.CancelIn, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    res = lv.cancel(db, actor, scope, request_id, data.reason)
    return ok(db, {**lv.request_out(db, res["request"], scope), "restored_shifts": len(res["restored"]),
                   "conflicts": [mk.conflict_out(db, c) for c in res["conflicts"]]})


@router.post("/leave/requests/{request_id}/attachments", status_code=201)
def upload_leave_document(request_id: uuid.UUID, db: DB, file: UploadFile = File(...), actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    req = lv.get_request(db, scope, request_id)
    if not scope.is_self(req.employee) and not (actor.has("workforce.leave.approve") and scope.manages(req.employee)):
        raise PermissionDenied("Only the employee or their manager can attach documents")
    a = save_upload(db, actor, file, entity_type="leave_request", entity_id=str(req.id))
    return ok(db, {"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes})


@router.get("/leave/requests/{request_id}/attachments/{attachment_id}")
def download_leave_document(request_id: uuid.UUID, attachment_id: uuid.UUID, db: DB, actor: Actor = Depends(ANY)):
    """Leave documents may contain medical details: only the employee and managers who decide their leave may open them."""
    scope = get_scope(db, actor)
    req = lv.get_request(db, scope, request_id)
    if not scope.is_self(req.employee) and not (actor.has("workforce.leave.approve") and scope.manages(req.employee)):
        raise PermissionDenied("You cannot open this document")
    a = db.get(Attachment, attachment_id)
    if a is None or a.entity_type != "leave_request" or a.entity_id != str(req.id):
        raise NotFound("Attachment not found")
    path = storage_dir() / a.storage_name
    if not path.is_file():
        raise NotFound("File is missing from storage")
    audit(db, actor, "workforce.leave.document_view", "leave_request", req.id, {"attachment_id": a.id})
    db.commit()
    return FileResponse(path, media_type=a.content_type, filename=a.filename)


# ------------------------------------------------------------------ overtime marketplace
@router.get("/overtime/marketplace")
def marketplace(db: DB, actor: Actor = Depends(require_permission("workforce.self")), date_from: date | None = None,
                date_to: date | None = None, location: str | None = None, shift: str | None = None, min_hours: float | None = None,
                max_hours: float | None = None, eligible_only: bool = False) -> list:
    return jsonable(mk.marketplace_for_employee(db, get_scope(db, actor), date_from=date_from, date_to=date_to, location=location,
                                                shift_name=shift, min_hours=min_hours, max_hours=max_hours, eligible_only=eligible_only))


@router.get("/overtime/opportunities")
def list_opportunities(db: DB, actor: Actor = Depends(VIEW), status: str | None = None, date_from: date | None = None,
                       date_to: date | None = None, department_id: uuid.UUID | None = None, page: int = Query(1, ge=1),
                       page_size: int = Query(25, ge=1, le=100)) -> dict:
    return jsonable(mk.list_for_manager(db, get_scope(db, actor), status=status, date_from=date_from, date_to=date_to,
                                        department_id=department_id, page=page, page_size=page_size))


@router.post("/overtime/opportunities", status_code=201)
def create_opportunity(data: s.OpportunityCreate, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    scope = get_scope(db, actor)
    o = mk.create_manual(db, actor, scope, data)
    return ok(db, mk.opportunity_out(db, o, scope, detail=True))


@router.get("/overtime/opportunities/{opp_id}")
def get_opportunity(opp_id: uuid.UUID, db: DB, actor: Actor = Depends(ANY)) -> dict:
    scope = get_scope(db, actor)
    o = mk.get_opportunity(db, scope, opp_id)
    return jsonable(mk.opportunity_out(db, o, scope, for_employee=scope.me, detail=True))


@router.post("/overtime/opportunities/{opp_id}/claim")
def claim(opp_id: uuid.UUID, request: Request, db: DB, actor: Actor = Depends(require_permission("workforce.self"))) -> JSONResponse:
    """First eligible claim to commit wins. Requires an Idempotency-Key so retries never create a second claim."""
    scope = get_scope(db, actor)
    key = get_key(request)
    return run_idempotent(db, actor, request, {"opportunity": opp_id},
                          lambda: mk.claim(db, actor, scope, opp_id, key))


@router.post("/overtime/opportunities/{opp_id}/publish")
def publish(opp_id: uuid.UUID, db: DB, data: s.PublishIn | None = None,
            actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    scope = get_scope(db, actor)
    o = mk.publish_existing(db, actor, scope, opp_id, data or s.PublishIn())
    return ok(db, mk.opportunity_out(db, o, scope, detail=True))


@router.post("/overtime/opportunities/{opp_id}/extend")
def extend(opp_id: uuid.UUID, data: s.ExtendIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, mk.opportunity_out(db, mk.extend(db, actor, scope, opp_id, data), scope, detail=True))


@router.post("/overtime/opportunities/{opp_id}/renotify")
def renotify(opp_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    return ok(db, mk.renotify(db, actor, get_scope(db, actor), opp_id))


@router.post("/overtime/opportunities/{opp_id}/cancel")
def cancel_opportunity(opp_id: uuid.UUID, data: s.CancelIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, mk.opportunity_out(db, mk.cancel(db, actor, scope, opp_id, data.reason), scope, detail=True))


@router.get("/overtime/opportunities/{opp_id}/candidates")
def opportunity_candidates(opp_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> list:
    scope = get_scope(db, actor)
    o = mk.get_opportunity(db, scope, opp_id, manage=True)
    return jsonable(_candidates(db, scope, mk.window_of(o), o.department_id, "overtime"))


@router.post("/overtime/opportunities/{opp_id}/assign", status_code=201)
def manual_assign(opp_id: uuid.UUID, data: s.ManualAssignIn, db: DB,
                  actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, sh.assignment_out(db, mk.manual_assign(db, actor, scope, opp_id, data)))


@router.get("/overtime/my-assignments")
def my_assignments(db: DB, actor: Actor = Depends(require_permission("workforce.self")), upcoming_only: bool = False) -> list:
    return jsonable(mk.my_assignments(db, get_scope(db, actor), upcoming_only=upcoming_only))


@router.post("/assignments/{assignment_id}/withdraw")
def withdraw(assignment_id: uuid.UUID, data: s.WithdrawIn, db: DB, actor: Actor = Depends(require_permission("workforce.self"))) -> dict:
    return ok(db, mk.withdraw(db, actor, get_scope(db, actor), assignment_id, data.reason))


@router.get("/overtime/vacancies")
def vacancies(db: DB, actor: Actor = Depends(VIEW), date_from: date | None = None, date_to: date | None = None,
              department_id: uuid.UUID | None = None) -> list:
    d0 = date_from or _today(db)
    return jsonable(mk.vacancies(db, get_scope(db, actor), d0, date_to or d0 + timedelta(days=7), department_id))


@router.get("/workforce/conflicts")
def conflicts(db: DB, actor: Actor = Depends(VIEW), status: str | None = "open") -> list:
    return jsonable(mk.list_conflicts(db, get_scope(db, actor), status or None))


@router.post("/workforce/conflicts/{conflict_id}/resolve")
def resolve(conflict_id: uuid.UUID, data: s.ResolveIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.manage"))) -> dict:
    return ok(db, mk.conflict_out(db, mk.resolve_conflict(db, actor, get_scope(db, actor), conflict_id, data)))


# ------------------------------------------------------------------ attendance
@router.get("/attendance")
def attendance(db: DB, actor: Actor = Depends(ANY), date_from: date | None = None, date_to: date | None = None,
               employee_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None, status: str | None = None,
               mine: bool = False) -> list:
    d1 = date_to or _today(db)
    return jsonable(att.list_records(db, get_scope(db, actor), date_from=date_from or d1 - timedelta(days=30), date_to=d1,
                                     employee_id=employee_id, department_id=department_id, status=status, mine=mine))


@router.get("/attendance/day-sheet")
def day_sheet(db: DB, actor: Actor = Depends(VIEW), day: date | None = None, department_id: uuid.UUID | None = None) -> list:
    return jsonable(att.day_sheet(db, get_scope(db, actor), day or _today(db), department_id))


@router.post("/attendance/check-in", status_code=201)
def check_in(request: Request, db: DB, data: s.CheckIn | None = None, actor: Actor = Depends(ANY)) -> JSONResponse:
    scope = get_scope(db, actor)
    body = data or s.CheckIn()
    return run_idempotent(db, actor, request, body.model_dump(), lambda: att.record_out(db, att.check_in(db, actor, scope, body)))


@router.post("/attendance/check-out", status_code=201)
def check_out(request: Request, db: DB, data: s.CheckIn | None = None, actor: Actor = Depends(ANY)) -> JSONResponse:
    scope = get_scope(db, actor)
    body = data or s.CheckIn()
    return run_idempotent(db, actor, request, body.model_dump(), lambda: att.record_out(db, att.check_out(db, actor, scope, body)))


@router.post("/attendance/record")
def record_attendance(data: s.RecordIn, db: DB, actor: Actor = Depends(require_permission("workforce.attendance.manage"))) -> dict:
    return ok(db, att.record_out(db, att.record(db, actor, get_scope(db, actor), data)))


@router.post("/attendance/import")
def import_attendance(db: DB, file: UploadFile = File(...), actor: Actor = Depends(require_permission("workforce.attendance.manage"))) -> dict:
    raw = file.file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValidationFailed("CSV files are limited to 2 MB")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValidationFailed("The file must be UTF-8 encoded CSV") from None
    return ok(db, att.import_csv(db, actor, get_scope(db, actor), content))


@router.get("/attendance/corrections")
def corrections(db: DB, actor: Actor = Depends(ANY), status: str | None = "pending", mine: bool = False) -> list:
    return jsonable(att.list_corrections(db, get_scope(db, actor), status or None, mine))


@router.post("/attendance/corrections", status_code=201)
def request_correction(data: s.CorrectionIn, db: DB, actor: Actor = Depends(ANY)) -> dict:
    return ok(db, att.correction_out(db, att.request_correction(db, actor, get_scope(db, actor), data)))


@router.post("/attendance/corrections/{correction_id}/approve")
def approve_correction(correction_id: uuid.UUID, db: DB, data: s.DecisionIn | None = None,
                       actor: Actor = Depends(require_permission("workforce.attendance.manage"))) -> dict:
    c = att.decide_correction(db, actor, get_scope(db, actor), correction_id, True, data.note if data else None)
    return ok(db, att.correction_out(db, c))


@router.post("/attendance/corrections/{correction_id}/reject")
def reject_correction(correction_id: uuid.UUID, data: s.RejectIn, db: DB,
                      actor: Actor = Depends(require_permission("workforce.attendance.manage"))) -> dict:
    return ok(db, att.correction_out(db, att.decide_correction(db, actor, get_scope(db, actor), correction_id, False, data.note)))


# ------------------------------------------------------------------ overtime records & payroll
@router.get("/overtime/calculations")
def calculations(db: DB, actor: Actor = Depends(ANY), date_from: date | None = None, date_to: date | None = None,
                 status: str | None = None, employee_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None,
                 mine: bool = False) -> list:
    d1 = date_to or _today(db)
    return jsonable(ot.list_calculations(db, get_scope(db, actor), date_from=date_from or d1.replace(day=1), date_to=d1, status=status,
                                         employee_id=employee_id, department_id=department_id, mine=mine))


@router.post("/overtime/calculations/recalculate")
def recalculate(data: s.RecalcIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.approve"))) -> dict:
    return ok(db, ot.recalculate_range(db, actor, get_scope(db, actor), data))


@router.post("/overtime/calculations/bulk-approve")
def bulk_approve(data: s.BulkApproveIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.approve"))) -> dict:
    return ok(db, ot.bulk_approve(db, actor, get_scope(db, actor), data.ids))


@router.post("/overtime/calculations/{calc_id}/approve")
def approve_calc(calc_id: uuid.UUID, db: DB, data: s.CalcDecisionIn | None = None,
                 actor: Actor = Depends(require_permission("workforce.overtime.approve"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, ot.calc_out(ot.approve(db, actor, scope, calc_id, data or s.CalcDecisionIn()), scope))


@router.post("/overtime/calculations/{calc_id}/reject")
def reject_calc(calc_id: uuid.UUID, data: s.RejectIn, db: DB, actor: Actor = Depends(require_permission("workforce.overtime.approve"))) -> dict:
    scope = get_scope(db, actor)
    return ok(db, ot.calc_out(ot.reject(db, actor, scope, calc_id, data.note), scope))


@router.get("/payroll/exports")
def payroll_exports(db: DB, actor: Actor = Depends(require_permission("workforce.payroll"))) -> list:
    return jsonable(ot.list_exports(db, actor))


@router.post("/payroll/exports", status_code=201)
def create_payroll_export(data: s.ExportIn, request: Request, db: DB, actor: Actor = Depends(require_permission("workforce.payroll"))) -> JSONResponse:
    scope = get_scope(db, actor)
    return run_idempotent(db, actor, request, data.model_dump(), lambda: ot.batch_out(db, ot.create_export(db, actor, scope, data)))


@router.get("/payroll/exports/{batch_id}/download")
def download_payroll(batch_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("workforce.payroll")),
                     format: str = Query("csv", pattern="^(csv|xlsx)$")) -> Response:
    b, rows = ot.export_rows(db, actor, batch_id)
    audit(db, actor, "workforce.payroll.download", "payroll_export_batch", b.id, {"format": format})
    db.commit()
    if format == "xlsx":
        return Response(ot.to_xlsx(b, rows), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{b.number}.xlsx"'})
    return Response(ot.to_csv(rows), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{b.number}.csv"'})


# ------------------------------------------------------------------ dashboards & reports
@router.get("/workforce/dashboard")
def dashboard(db: DB, actor: Actor = Depends(VIEW), day: date | None = None, department_id: uuid.UUID | None = None) -> dict:
    return jsonable(rp.manager_dashboard(db, get_scope(db, actor), day, department_id))


@router.get("/workforce/reports/workforce")
def workforce_report(db: DB, actor: Actor = Depends(VIEW), date_from: date | None = None, date_to: date | None = None) -> dict:
    d1 = date_to or _today(db)
    return jsonable(rp.workforce_report(db, get_scope(db, actor), date_from or d1.replace(day=1), d1))


@router.get("/workforce/reports/overtime")
def overtime_report(db: DB, actor: Actor = Depends(VIEW), date_from: date | None = None, date_to: date | None = None,
                    group_by: str = Query("employee", pattern="^(employee|department)$"), department_id: uuid.UUID | None = None) -> dict:
    d1 = date_to or _today(db)
    return jsonable(rp.overtime_report(db, get_scope(db, actor), date_from or d1.replace(day=1), d1, group_by, department_id))


@router.get("/workforce/reports/attendance")
def attendance_report(db: DB, actor: Actor = Depends(VIEW), date_from: date | None = None, date_to: date | None = None,
                      department_id: uuid.UUID | None = None) -> dict:
    d1 = date_to or _today(db)
    return jsonable(rp.attendance_report(db, get_scope(db, actor), date_from or d1.replace(day=1), d1, department_id))
