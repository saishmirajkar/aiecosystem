"""Workforce Agent tools: typed, permission-checked, scope-limited views over the workforce services.

Every tool uses the signed-in user's scope: employees only ever see their own records; managers see their
departments. The two action tools only create approval requests; nothing is scheduled until a person approves.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import func, or_, select

from app.ai_tools.base import ToolContext, ToolError, ToolInput, ToolOutput, tool
from app.ai_tools.inventory_tools import metrics, table
from app.core.database import utcnow
from app.models import Department, Employee, Shift, ShiftStatus, ShiftTemplate
from app.modules.workforce import attendance as att
from app.modules.workforce import marketplace as mk
from app.modules.workforce import overtime as ot
from app.modules.workforce import reports as rp
from app.modules.workforce import schemas as ws
from app.modules.workforce import shifts as sh
from app.modules.workforce.core import Scope, company_tz, employee_brief, get_scope, hours, workforce_policy
from app.modules.workforce.eligibility import evaluate, window_for_shift
from app.orchestrator import approval_manager
from app.orchestrator.approval_manager import register_action


# ------------------------------------------------------------------ helpers
def _scope(ctx: ToolContext) -> Scope:
    return get_scope(ctx.db, ctx.actor)


def _today(ctx: ToolContext) -> date:
    return utcnow().astimezone(company_tz(ctx.db)).date()


def parse_day(ctx: ToolContext, value: str | None, default: date | None = None) -> date:
    if not value:
        return default or _today(ctx)
    v = value.strip().lower()
    base = _today(ctx)
    words = {"today": 0, "tomorrow": 1, "yesterday": -1, "day after tomorrow": 2}
    if v in words:
        return base + timedelta(days=words[v])
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise ToolError(f"Could not understand the date '{value}'. Use YYYY-MM-DD, today, tomorrow or yesterday.") from None


def week_range(ctx: ToolContext, which: str | None) -> tuple[date, date]:
    t = _today(ctx)
    start = t - timedelta(days=t.weekday())
    if which == "last":
        start -= timedelta(days=7)
    elif which == "next":
        start += timedelta(days=7)
    return start, start + timedelta(days=6)


def resolve_employee(ctx: ToolContext, ref: str | None) -> Employee:
    scope = _scope(ctx)
    if not ref or ref.strip().lower() in ("me", "my", "myself", "i"):
        if scope.me is None:
            raise ToolError("Your login is not linked to an employee record, so I can't look up your own schedule.")
        return scope.me
    r = ref.strip()
    stmt = scope.employee_filter(select(Employee).where(or_(
        func.lower(Employee.employee_code) == r.lower(),
        func.lower(Employee.first_name + " " + func.coalesce(Employee.last_name, "")).like(f"%{r.lower()}%"))))
    rows = ctx.db.execute(stmt.limit(6)).unique().scalars().all()
    if not rows:
        raise ToolError(f"No employee matching '{ref}' that you are allowed to see.")
    if len(rows) > 1 and not any(e.employee_code.lower() == r.lower() for e in rows):
        raise ToolError(f"'{ref}' matches several employees: " + ", ".join(f"{e.full_name} ({e.employee_code})" for e in rows) +
                        ". Which one?")
    return next((e for e in rows if e.employee_code.lower() == r.lower()), rows[0])


def resolve_department(ctx: ToolContext, ref: str | None) -> Department | None:
    if not ref:
        return None
    r = ref.strip().lower()
    d = ctx.db.execute(select(Department).where(or_(func.lower(Department.code) == r, func.lower(Department.name) == r))).scalar_one_or_none()
    if d is None:
        d = ctx.db.execute(select(Department).where(func.lower(Department.name).like(f"%{r}%"))).scalars().first()
    if d is None:
        raise ToolError(f"No department called '{ref}'.")
    return d


def resolve_shift(ctx: ToolContext, name: str | None, day: date, department: Department | None = None) -> Shift:
    stmt = select(Shift).where(Shift.shift_date == day, Shift.status == ShiftStatus.SCHEDULED)
    stmt = _scope(ctx).department_filter(stmt, Shift.department_id)
    if department:
        stmt = stmt.where(Shift.department_id == department.id)
    if name:
        stmt = stmt.where(func.lower(Shift.name).like(f"%{name.strip().lower()}%"))
    rows = ctx.db.execute(stmt.order_by(Shift.start_at)).unique().scalars().all()
    if not rows:
        raise ToolError(f"No {name + ' ' if name else ''}shift on {day:%d %b %Y} in your departments.")
    if len(rows) > 1:
        tz = company_tz(ctx.db)
        raise ToolError("Several shifts match: " + "; ".join(f"{s.name} {s.department.name} {s.start_at.astimezone(tz):%H:%M}" for s in rows)
                        + ". Name the shift or department.")
    return rows[0]


def _need_manager(ctx: ToolContext) -> Scope:
    scope = _scope(ctx)
    if not scope.manages_anyone:
        raise ToolError("This needs manager access to a department. You can ask about your own shifts, leave and overtime.")
    return scope


def _hm(minutes_: int) -> str:
    return f"{hours(minutes_):f}".rstrip("0").rstrip(".") + " h"


# ------------------------------------------------------------------ read tools
class EmployeeIn(ToolInput):
    employee: str | None = Field(None, max_length=100, description="Employee code or name; omit for yourself")


@tool("get_employee_details", "Profile of an employee you may see: department, team, designation, status, skills, overtime "
      "eligibility and leave balances.", EmployeeIn, "workforce.self")
def get_employee_details(ctx: ToolContext, a: EmployeeIn) -> ToolOutput:
    from app.modules.workforce.leave import balances_out
    e = resolve_employee(ctx, a.employee)
    bal = balances_out(ctx.db, e, _today(ctx).year)
    data = {**employee_brief(e), "status": e.status.value, "teams": [t.name for t in e.teams], "overtime_eligible": e.overtime_eligible,
            "skills": [k.skill.name for k in e.skills],
            "leave_balances": [{"type": b["name"], "available": b["available"], "used": b["used"]} for b in bal]}
    rows = [{"type": b["name"], "available": "unlimited" if b["unlimited"] else f"{b['available']:f}", "used": f"{b['used']:f}",
             "pending": f"{b['pending']:f}"} for b in bal]
    return ToolOutput(data, f"{e.full_name} ({e.employee_code}), {e.department.name}, {e.status.value}; teams: "
                      f"{', '.join(data['teams']) or 'none'}; overtime-eligible: {'yes' if e.overtime_eligible else 'no'}.",
                      table(f"{e.full_name} · leave balances", [("type", "Leave type"), ("available", "Available"), ("used", "Used"),
                                                                 ("pending", "Pending")], rows, "verified"))


class ScheduleIn(EmployeeIn):
    date_from: str | None = Field(None, description="Start date (YYYY-MM-DD, today, tomorrow); default today")
    days: int = Field(7, ge=1, le=31)


@tool("get_employee_schedule", "Shifts of one employee (default: yourself) over a period, including covered overtime shifts.",
      ScheduleIn, "workforce.self")
def get_employee_schedule(ctx: ToolContext, a: ScheduleIn) -> ToolOutput:
    from app.models import AssignmentStatus, ShiftAssignment
    e = resolve_employee(ctx, a.employee)
    d0 = parse_day(ctx, a.date_from)
    tz = company_tz(ctx.db)
    rows_q = ctx.db.execute(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        ShiftAssignment.employee_id == e.id, ShiftAssignment.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE]),
        Shift.shift_date >= d0, Shift.shift_date < d0 + timedelta(days=a.days)).order_by(ShiftAssignment.start_at)).unique().scalars().all()
    rows = [{"date": f"{x.shift.shift_date:%a %d %b}", "shift": x.shift.name, "time": f"{x.start_at.astimezone(tz):%H:%M}–{x.end_at.astimezone(tz):%H:%M}",
             "department": x.shift.department.name, "type": "overtime" if x.source.value != "schedule" else "regular",
             "status": "on leave" if x.status == AssignmentStatus.ON_LEAVE else "scheduled"} for x in rows_q]
    summary = (f"{e.full_name} has {sum(1 for r in rows if r['status'] == 'scheduled')} shift(s) from {d0:%d %b} for {a.days} day(s)"
               + (": " + "; ".join(f"{r['date']} {r['shift']} {r['time']}" + (" (on leave)" if r["status"] != "scheduled" else "")
                                   for r in rows[:8]) if rows else "."))
    return ToolOutput({"employee": employee_brief(e), "shifts": rows}, summary,
                      table(f"Schedule · {e.full_name}", [("date", "Date"), ("shift", "Shift"), ("time", "Time"), ("type", "Type"),
                                                          ("status", "Status")], rows, "verified"))


class DeptDayIn(ToolInput):
    department: str | None = Field(None, max_length=100, description="Department name or code; omit for all your departments")
    day: str | None = Field(None, description="Date (YYYY-MM-DD, today, tomorrow); default today")


@tool("get_department_schedule", "Every shift on a day with who works each position, who is on leave and which positions are vacant.",
      DeptDayIn, "workforce.view")
def get_department_schedule(ctx: ToolContext, a: DeptDayIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d = parse_day(ctx, a.day)
    dept = resolve_department(ctx, a.department)
    cal = sh.calendar(ctx.db, scope, d, d, dept.id if dept else None)
    rows = []
    for s in cal["shifts"]:
        for p in s["positions"]:
            rows.append({"shift": f"{s['name']} {s['start']}–{s['end']}", "department": s["department_name"],
                         "employee": p["assignment"]["employee"]["full_name"] if p["assignment"] else "— vacant —",
                         "type": (p["assignment"]["source"] if p["assignment"] else ""),
                         "on_leave": ", ".join(x["employee"]["full_name"] for x in p["on_leave"]),
                         "offered": p["opportunity"]["number"] if p["opportunity"] else ""})
    vacant = sum(1 for r in rows if r["employee"] == "— vacant —")
    return ToolOutput({"date": d, "shifts": cal["shifts"]}, f"{len(cal['shifts'])} shift(s) on {d:%d %b}, {len(rows)} position(s), {vacant} vacant.",
                      table(f"Schedule · {d:%a %d %b}", [("shift", "Shift"), ("department", "Department"), ("employee", "Employee"),
                                                          ("type", "Type"), ("on_leave", "On leave"), ("offered", "Overtime offer")], rows, "verified"))


class RangeIn(ToolInput):
    date_from: str | None = Field(None, description="Start date; default today")
    date_to: str | None = Field(None, description="End date; default same as start")


@tool("get_employees_on_leave", "Who is on approved leave, plus who is absent (no check-in) when the period includes today.",
      RangeIn, "workforce.view")
def get_employees_on_leave(ctx: ToolContext, a: RangeIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d0 = parse_day(ctx, a.date_from)
    d1 = parse_day(ctx, a.date_to, d0)
    leave = rp.on_leave(ctx.db, scope, d0, d1)
    absent = rp._absent_today(ctx.db, scope, _today(ctx)) if d0 <= _today(ctx) <= d1 else []
    rows = [{"employee": x["employee"]["full_name"], "department": x["employee"]["department_name"], "status": "On approved leave",
             "detail": f"{x['start_date']:%d %b}–{x['end_date']:%d %b}" + (" (half day)" if x["half_day"] != "none" else "")} for x in leave]
    rows += [{"employee": x["employee"]["full_name"], "department": x["employee"]["department_name"], "status": "Absent — no check-in",
              "detail": f"{x['shift']} {x['scheduled']}"} for x in absent]
    span = f"{d0:%d %b}" + (f"–{d1:%d %b}" if d1 != d0 else "")
    summary = (f"{len(leave)} on approved leave and {len(absent)} absent without check-in ({span})" +
               (": " + "; ".join(f"{r['employee']} ({r['status'].lower()})" for r in rows[:10]) if rows else "."))
    return ToolOutput({"on_leave": leave, "absent": absent}, summary,
                      table(f"Absence · {span}", [("employee", "Employee"), ("department", "Department"), ("status", "Status"),
                                                  ("detail", "Details")], rows, "verified"))


class Empty(ToolInput):
    pass


@tool("get_pending_leave_requests", "Leave requests waiting for a decision in your departments, with the shifts each affects.",
      Empty, "workforce.view")
def get_pending_leave_requests(ctx: ToolContext, a: Empty) -> ToolOutput:
    from app.modules.workforce.leave import list_requests
    scope = _need_manager(ctx)
    items = list_requests(ctx.db, scope, status="pending", page_size=50)["items"]
    rows = [{"number": r["number"], "employee": r["employee"]["full_name"], "type": r["leave_type"],
             "dates": f"{r['start_date']:%d %b}–{r['end_date']:%d %b}", "days": f"{r['days']:f}", "shifts": len(r["affected_shifts"])} for r in items]
    return ToolOutput({"requests": rows}, f"{len(rows)} leave request(s) waiting for a decision" +
                      (": " + "; ".join(f"{r['employee']} {r['type']} {r['dates']}" for r in rows[:8]) if rows else "."),
                      table("Pending leave", [("number", "Request"), ("employee", "Employee"), ("type", "Type"), ("dates", "Dates"),
                                              ("days", "Days"), ("shifts", "Shifts affected")], rows, "verified"))


class VacantIn(RangeIn):
    department: str | None = Field(None, max_length=100)


@tool("get_vacant_shifts", "Uncovered shift positions in a period and whether each is already offered as overtime.",
      VacantIn, "workforce.view")
def get_vacant_shifts(ctx: ToolContext, a: VacantIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d0 = parse_day(ctx, a.date_from)
    d1 = parse_day(ctx, a.date_to, d0)
    dept = resolve_department(ctx, a.department)
    vac = mk.vacancies(ctx.db, scope, d0, d1, dept.id if dept else None)
    rows = [{"date": f"{v['shift']['shift_date']:%a %d %b}", "shift": f"{v['shift']['name']} {v['shift']['start']}–{v['shift']['end']}",
             "department": v["shift"]["department_name"], "vacant": v["vacant_positions"], "offered": v["on_offer"],
             "status": ", ".join(f"{o['number']} {o['status']}" for o in v["opportunities"]) or "not offered"} for v in vac]
    total = sum(v["vacant_positions"] for v in vac)
    return ToolOutput({"vacancies": vac}, f"{total} vacant position(s) across {len(vac)} shift(s) between {d0:%d %b} and {d1:%d %b}" +
                      (": " + "; ".join(f"{r['date']} {r['shift']} ({r['department']}) {r['vacant']} vacant, {r['status']}" for r in rows[:8]) if rows else "."),
                      table("Vacant shifts", [("date", "Date"), ("shift", "Shift"), ("department", "Department"), ("vacant", "Vacant"),
                                              ("status", "Overtime offer")], rows, "verified"))


@tool("get_open_overtime_opportunities", "Open overtime opportunities. For an employee: the ones they may claim and whether they "
      "are eligible. For a manager: open, partially filled, draft and expired offers in their departments.", Empty, "workforce.self")
def get_open_overtime_opportunities(ctx: ToolContext, a: Empty) -> ToolOutput:
    scope = _scope(ctx)
    if scope.manages_anyone:
        items = mk.list_for_manager(ctx.db, scope, status="draft,published,open,partially_filled,expired", page_size=50)["items"]
        rows = [{"number": o["number"], "shift": f"{o['date']:%a %d %b} {o['shift_name']} {o['start']}–{o['end']}",
                 "department": o["department_name"], "filled": f"{o['positions_filled']}/{o['positions_total']}", "status": o["status"],
                 "deadline": o["claim_deadline"].astimezone(company_tz(ctx.db)).strftime("%d %b %H:%M")} for o in items]
    else:
        items = mk.marketplace_for_employee(ctx.db, scope)
        rows = [{"number": o["number"], "shift": f"{o['date']:%a %d %b} {o['shift_name']} {o['start']}–{o['end']}",
                 "department": o["department_name"], "filled": f"{o['positions_left']} left", "status": "eligible" if o["eligible"]
                 else "not eligible: " + "; ".join(o["ineligible_reasons"]),
                 "deadline": o["claim_deadline"].astimezone(company_tz(ctx.db)).strftime("%d %b %H:%M")} for o in items]
    return ToolOutput({"opportunities": rows}, f"{len(rows)} overtime opportunit{'y' if len(rows) == 1 else 'ies'}" +
                      (": " + "; ".join(f"{r['number']} {r['shift']} ({r['status']})" for r in rows[:8]) if rows else "."),
                      table("Overtime opportunities", [("number", "Offer"), ("shift", "Shift"), ("department", "Department"),
                                                       ("filled", "Positions"), ("status", "Status"), ("deadline", "Claim by")], rows, "verified",
                            note="Claiming happens in the employee portal; the first eligible claim to be saved wins."))


class AssignmentsIn(ToolInput):
    week: Literal["this", "last", "next"] | None = Field("this", description="Which week; ignored when dates are given")
    date_from: str | None = None
    date_to: str | None = None
    employee: str | None = Field(None, max_length=100)


@tool("get_overtime_assignments", "Who took overtime shifts (claimed or manually assigned) in a period, confirmed by the schedule.",
      AssignmentsIn, "workforce.self")
def get_overtime_assignments(ctx: ToolContext, a: AssignmentsIn) -> ToolOutput:
    from app.models import AssignmentSource, AssignmentStatus, ShiftAssignment
    scope = _scope(ctx)
    d0, d1 = (parse_day(ctx, a.date_from), parse_day(ctx, a.date_to, parse_day(ctx, a.date_from))) if a.date_from else week_range(ctx, a.week)
    stmt = select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).join(Employee, Employee.id == ShiftAssignment.employee_id).where(
        ShiftAssignment.source.in_([AssignmentSource.MARKETPLACE, AssignmentSource.MANUAL]), ShiftAssignment.status == AssignmentStatus.ASSIGNED,
        Shift.shift_date >= d0, Shift.shift_date <= d1)
    if a.employee or not scope.manages_anyone:
        stmt = stmt.where(ShiftAssignment.employee_id == resolve_employee(ctx, a.employee).id)
    else:
        stmt = scope.employee_filter(stmt)
    tz = company_tz(ctx.db)
    rows = [{"employee": x.employee.full_name, "date": f"{x.shift.shift_date:%a %d %b}", "shift": x.shift.name,
             "time": f"{x.start_at.astimezone(tz):%H:%M}–{x.end_at.astimezone(tz):%H:%M}",
             "how": "claimed" if x.source == AssignmentSource.MARKETPLACE else f"assigned by manager ({x.reason or 'no reason'})"}
            for x in ctx.db.execute(stmt.order_by(ShiftAssignment.start_at)).unique().scalars()]
    return ToolOutput({"assignments": rows, "from": d0, "to": d1}, f"{len(rows)} overtime shift(s) between {d0:%d %b} and {d1:%d %b}" +
                      (": " + "; ".join(f"{r['employee']} {r['date']} {r['shift']} ({r['how']})" for r in rows[:10]) if rows else "."),
                      table(f"Overtime shifts · {d0:%d %b}–{d1:%d %b}", [("employee", "Employee"), ("date", "Date"), ("shift", "Shift"),
                                                                       ("time", "Time"), ("how", "How")], rows, "verified"))


class AttendanceIn(EmployeeIn):
    date_from: str | None = None
    date_to: str | None = None


@tool("get_employee_attendance", "Attendance records (check-in, check-out, worked time, status) for an employee or, for managers "
      "without an employee, everyone in scope.", AttendanceIn, "workforce.self")
def get_employee_attendance(ctx: ToolContext, a: AttendanceIn) -> ToolOutput:
    scope = _scope(ctx)
    d1 = parse_day(ctx, a.date_to)
    d0 = parse_day(ctx, a.date_from, d1 - timedelta(days=6))
    emp = resolve_employee(ctx, a.employee) if (a.employee or not scope.manages_anyone) else None
    recs = att.list_records(ctx.db, scope, date_from=d0, date_to=d1, employee_id=emp.id if emp else None)
    rows = [{"employee": r["employee"]["full_name"], "date": f"{r['work_date']:%a %d %b}", "shift": r["shift_name"], "scheduled": r["scheduled"],
             "in": r["check_in"], "out": r["check_out"], "worked": _hm(r["worked_minutes"]), "status": r["status"]} for r in recs]
    counts: dict[str, int] = {}
    for r in recs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return ToolOutput({"records": recs, "counts": counts}, f"{len(recs)} attendance record(s) {d0:%d %b}–{d1:%d %b}: " +
                      (", ".join(f"{n} {k.replace('_', ' ')}" for k, n in counts.items()) or "none"),
                      table("Attendance", [("employee", "Employee"), ("date", "Date"), ("shift", "Shift"), ("scheduled", "Scheduled"),
                                           ("in", "In"), ("out", "Out"), ("worked", "Worked"), ("status", "Status")], rows, "verified"))


class OvertimeSummaryIn(ToolInput):
    date_from: str | None = Field(None, description="Default: first day of this month")
    date_to: str | None = Field(None, description="Default: today")
    group_by: Literal["employee", "department"] = "employee"


@tool("get_overtime_summary", "Overtime hours calculated from verified attendance (pending and approved), and amounts when the user "
      "may see pay. Employees get their own figures.", OvertimeSummaryIn, "workforce.self")
def get_overtime_summary(ctx: ToolContext, a: OvertimeSummaryIn) -> ToolOutput:
    scope = _scope(ctx)
    d1 = parse_day(ctx, a.date_to)
    d0 = parse_day(ctx, a.date_from, d1.replace(day=1))
    if not scope.manages_anyone:
        me = resolve_employee(ctx, None)
        calcs = ot.list_calculations(ctx.db, scope, date_from=d0, date_to=d1, mine=True)
        pending = sum(c["payable_minutes"] for c in calcs if c["status"] == "calculated")
        approved = sum(c["approved_minutes"] or 0 for c in calcs if c["status"] in ("approved", "exported"))
        amount = sum((c.get("amount") or 0) for c in calcs if c["status"] == "calculated") + sum(
            (c.get("approved_amount") or 0) for c in calcs if c["status"] in ("approved", "exported"))
        items = [{"label": "Approved overtime", "value": _hm(approved)}, {"label": "Awaiting approval", "value": _hm(pending)}]
        if me.base_hourly_rate is not None:
            items.append({"label": "Estimated earnings", "value": f"{amount:,.2f}"})
        return ToolOutput({"approved_minutes": approved, "pending_minutes": pending}, f"From {d0:%d %b} to {d1:%d %b}: {_hm(approved)} approved "
                          f"and {_hm(pending)} awaiting approval.", metrics("Your overtime", items, "calculated",
                                                                             note="Calculated from recorded attendance and the overtime policy."))
    rep = rp.overtime_report(ctx.db, scope, d0, d1, a.group_by)
    rows = [{"name": r["label"], "payable": f"{r['payable_hours']:f}", "approved": f"{r['approved_hours']:f}", "pending": f"{r['pending_hours']:f}",
             **({"amount": f"{r['amount']:,.2f}"} if "amount" in r else {})} for r in rep["rows"]]
    cols = [("name", a.group_by.title()), ("payable", "Overtime h"), ("approved", "Approved h"), ("pending", "Pending h")]
    if rep["rows"] and "amount" in rep["rows"][0]:
        cols.append(("amount", "Approved amount"))
    tot = rep["totals"]
    return ToolOutput(rep, f"Overtime {d0:%d %b}–{d1:%d %b}: {tot['payable_hours']} h calculated, {tot['approved_hours']} h approved"
                      + (f", approved amount {tot['amount']:,.2f}" if "amount" in tot else "") + " (from verified attendance).",
                      table(f"Overtime · {d0:%d %b}–{d1:%d %b}", cols, rows, "calculated",
                            note="Only attendance-based overtime counts; scheduled or claimed shifts alone are not overtime."))


@tool("get_department_staffing_status", "Staffing for a day: required vs assigned per department, vacancies, people on leave and absent.",
      DeptDayIn, "workforce.view")
def get_department_staffing_status(ctx: ToolContext, a: DeptDayIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d = parse_day(ctx, a.day)
    dept = resolve_department(ctx, a.department)
    cov = rp.coverage(ctx.db, scope, d, dept.id if dept else None)
    leave = rp.on_leave(ctx.db, scope, d, d)
    absent = rp._absent_today(ctx.db, scope, d) if d == _today(ctx) else []
    rows = [{"department": c["department_name"], "shifts": c["shifts"], "required": c["required"], "assigned": c["assigned"],
             "vacant": c["vacant"], "coverage": f"{c['coverage_pct']}%"} for c in cov]
    summary = (f"Staffing on {d:%a %d %b}: " + ("; ".join(f"{r['department']} {r['assigned']}/{r['required']} ({r['coverage']}), {r['vacant']} vacant"
                                                         for r in rows) or "no shifts scheduled") +
               f". {len(leave)} on leave" + (f", {len(absent)} absent without check-in" if d == _today(ctx) else "") + ".")
    return ToolOutput({"coverage": cov, "on_leave": len(leave), "absent": len(absent)}, summary,
                      table(f"Staffing · {d:%a %d %b}", [("department", "Department"), ("shifts", "Shifts"), ("required", "Required"),
                                                         ("assigned", "Assigned"), ("vacant", "Vacant"), ("coverage", "Coverage")], rows, "verified"))


class AvailableIn(ToolInput):
    shift: str | None = Field(None, max_length=100, description="Shift name, e.g. Morning")
    day: str | None = Field("tomorrow", description="Date of the shift")
    department: str | None = Field(None, max_length=100)


@tool("get_available_employees", "Who could work a given shift: checks leave, existing shifts, rest periods, weekly limits and skills "
      "for every employee in scope.", AvailableIn, "workforce.view")
def get_available_employees(ctx: ToolContext, a: AvailableIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d = parse_day(ctx, a.day)
    s = resolve_shift(ctx, a.shift, d, resolve_department(ctx, a.department))
    policy = workforce_policy(ctx.db, s.department_id)
    w = window_for_shift(s)
    emps = ctx.db.execute(scope.employee_filter(select(Employee).where(Employee.status == "active", Employee.department_id == s.department_id))
                          .order_by(Employee.first_name)).unique().scalars().all()
    rows, available = [], 0
    for e in emps:
        f = evaluate(ctx.db, e, w, policy)
        available += 0 if f else 1
        rows.append({"employee": e.full_name, "code": e.employee_code, "available": "yes" if not f else "no",
                     "reason": "" if not f else f[0].message})
    rows.sort(key=lambda r: r["available"] != "yes")
    tz = company_tz(ctx.db)
    return ToolOutput({"shift": s.name, "date": d, "available": available, "rows": rows},
                      f"{available} of {len(emps)} employee(s) in {s.department.name} could work {s.name} on {d:%a %d %b} "
                      f"({s.start_at.astimezone(tz):%H:%M}–{s.end_at.astimezone(tz):%H:%M}).",
                      table(f"Availability · {s.name} {d:%d %b}", [("employee", "Employee"), ("code", "Code"), ("available", "Available"),
                                                                   ("reason", "Why not")], rows, "calculated"))


class ReportIn(ToolInput):
    report: Literal["workforce", "overtime", "attendance"]
    date_from: str | None = None
    date_to: str | None = None


@tool("generate_workforce_report", "Workforce (headcount, leave, marketplace fill rate), overtime or attendance report for a period.",
      ReportIn, "workforce.view")
def generate_workforce_report(ctx: ToolContext, a: ReportIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d1 = parse_day(ctx, a.date_to)
    d0 = parse_day(ctx, a.date_from, d1.replace(day=1))
    if a.report == "overtime":
        return get_overtime_summary(ctx, OvertimeSummaryIn(date_from=str(d0), date_to=str(d1)))
    if a.report == "attendance":
        rep = rp.attendance_report(ctx.db, scope, d0, d1)
        rows = [{"employee": r["employee"]["full_name"], "shifts": r["shifts"], "present": r["present"] + r["late"], "late": r["late"],
                 "absent": r["absent"], "leave": r["on_leave"], "worked": f"{r['worked_hours']:f} h",
                 "pct": f"{r['attendance_pct']}%" if r["attendance_pct"] is not None else "—"} for r in rep["rows"]]
        return ToolOutput(rep, f"Attendance {d0:%d %b}–{d1:%d %b} for {len(rows)} employee(s).",
                          table(f"Attendance report · {d0:%d %b}–{d1:%d %b}", [("employee", "Employee"), ("shifts", "Shifts"), ("present", "Present"),
                                ("late", "Late"), ("absent", "Absent"), ("leave", "Leave"), ("worked", "Worked"), ("pct", "Attendance")], rows, "calculated"))
    rep = rp.workforce_report(ctx.db, scope, d0, d1)
    m = rep["overtime_marketplace"]
    items = [{"label": "Active employees", "value": rep["active_employees"]}, {"label": "Overtime offers", "value": m["total"]},
             {"label": "Filled", "value": m["filled"]}, {"label": "Expired unfilled", "value": m["expired"]},
             {"label": "Filled by claim / by manager", "value": f"{m['by_marketplace']} / {m['by_manager']}"},
             {"label": "Median time to fill", "value": f"{m['median_minutes_to_fill']} min" if m["median_minutes_to_fill"] is not None else "—"}]
    return ToolOutput(rep, f"Workforce {d0:%d %b}–{d1:%d %b}: {rep['active_employees']} active employees; {m['total']} overtime offers, "
                      f"{m['filled']} filled ({m['by_marketplace']} by claim, {m['by_manager']} by manager), {m['expired']} expired.",
                      metrics(f"Workforce · {d0:%d %b}–{d1:%d %b}", items, "calculated"))


# ------------------------------------------------------------------ action tools (approval required)
def _approval_card(appr, title: str, rows: list[dict], columns: list[tuple[str, str]], note: str) -> dict[str, Any]:
    return {"type": "approval", "title": title, "provenance": "draft", "approval_id": str(appr.id), "status": appr.status.value,
            "action_type": appr.action_type, "summary": appr.summary, "expires_at": appr.expires_at.isoformat(),
            "columns": [{"key": k, "label": l} for k, l in columns], "rows": rows, "note": note, "sources": [],
            "as_of": utcnow().isoformat()}


class ShiftDraftIn(ToolInput):
    template: str = Field(min_length=1, max_length=100, description="Shift template name or code, e.g. Morning")
    day: str = Field(description="Date of the shift")
    required_count: int | None = Field(None, ge=1, le=100)


@tool("create_shift_draft", "PREPARE (not execute) a new shift from a template. Validates the template, date and that it doesn't "
      "already exist; creates an approval request.", ShiftDraftIn, "workforce.schedule", "draft")
def create_shift_draft(ctx: ToolContext, a: ShiftDraftIn) -> ToolOutput:
    scope = _need_manager(ctx)
    d = parse_day(ctx, a.day)
    r = a.template.strip().lower()
    t = ctx.db.execute(select(ShiftTemplate).where(ShiftTemplate.is_active.is_(True), or_(
        func.lower(ShiftTemplate.code) == r, func.lower(ShiftTemplate.name).like(f"%{r}%")))).unique().scalars().all()
    t = [x for x in t if scope.manages_department(x.department_id)]
    if not t:
        raise ToolError(f"No shift template '{a.template}' in your departments.")
    if len(t) > 1:
        raise ToolError("Several templates match: " + ", ".join(f"{x.name} ({x.department.name})" for x in t))
    tpl = t[0]
    if d < _today(ctx):
        raise ToolError("That date is in the past.")
    if ctx.db.execute(select(Shift.id).where(Shift.template_id == tpl.id, Shift.shift_date == d, Shift.status == ShiftStatus.SCHEDULED)).first():
        raise ToolError(f"{tpl.name} on {d:%d %b} already exists.")
    count = a.required_count or tpl.required_count
    params = {"template_id": str(tpl.id), "shift_date": d.isoformat(), "required_count": count}
    rows = [{"shift": tpl.name, "department": tpl.department.name, "date": f"{d:%a %d %b %Y}",
             "time": f"{tpl.start_time:%H:%M}–{tpl.end_time:%H:%M}", "positions": count}]
    summary = f"Create shift {tpl.name} ({tpl.department.name}) on {d:%d %b %Y} with {count} position(s)"
    appr = approval_manager.create(ctx.db, ctx.actor, "workforce.create_shift", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval"}, f"Prepared: {summary}. Awaiting approval; nothing is "
                      "scheduled yet.", _approval_card(appr, "Proposed shift", rows, [("shift", "Shift"), ("department", "Department"),
                      ("date", "Date"), ("time", "Time"), ("positions", "Positions")], "Approving creates the shift with vacant positions."))


class OpportunityDraftIn(ToolInput):
    shift: str | None = Field(None, max_length=100, description="Shift name, e.g. Morning")
    day: str = Field(description="Date of the shift")
    department: str | None = Field(None, max_length=100)
    positions: int | None = Field(None, ge=1, le=50, description="Default: every vacant position not yet on offer")


@tool("create_overtime_opportunity_draft", "PREPARE (not execute) an overtime opportunity for vacant positions of a shift. Checks the "
      "vacancy and deadline; creates an approval request. Publishing happens only after approval.", OpportunityDraftIn,
      "workforce.overtime.manage", "draft")
def create_overtime_opportunity_draft(ctx: ToolContext, a: OpportunityDraftIn) -> ToolOutput:
    from app.models import OpportunityPosition, OvertimeOpportunity, PositionKind
    from app.modules.workforce.shifts import occupant
    _need_manager(ctx)
    d = parse_day(ctx, a.day)
    s = resolve_shift(ctx, a.shift, d, resolve_department(ctx, a.department))
    if s.start_at <= utcnow():
        raise ToolError("That shift has already started.")
    offered = set(ctx.db.execute(select(OpportunityPosition.position_id).join(OvertimeOpportunity, OvertimeOpportunity.id == OpportunityPosition.opportunity_id)
                                 .where(OvertimeOpportunity.shift_id == s.id, OvertimeOpportunity.status.in_(mk.ACTIVE),
                                        OpportunityPosition.is_active.is_(True))).scalars())
    vacant = [p for p in s.positions if p.is_active and p.kind == PositionKind.REGULAR and p.id not in offered and occupant(ctx.db, p.id) is None]
    if not vacant:
        raise ToolError(f"{s.name} on {d:%d %b} has no vacant positions that aren't already offered.")
    n = min(a.positions or len(vacant), len(vacant))
    deadline = mk.default_deadline(ctx.db, s.department_id, s.start_at)
    tz = company_tz(ctx.db)
    params = {"shift_id": str(s.id), "positions": n}
    rows = [{"shift": s.name, "department": s.department.name, "date": f"{d:%a %d %b}",
             "time": f"{s.start_at.astimezone(tz):%H:%M}–{s.end_at.astimezone(tz):%H:%M}", "positions": n,
             "claim_by": deadline.astimezone(tz).strftime("%d %b %H:%M")}]
    summary = f"Offer {n} overtime position(s) on {s.name} ({s.department.name}) {d:%d %b}"
    appr = approval_manager.create(ctx.db, ctx.actor, "workforce.create_opportunity", params, summary, preview={"rows": rows},
                                   agent_id=ctx.agent_id, conversation_id=ctx.conversation_id)
    ctx.approvals.append(approval_manager.approval_out(appr))
    return ToolOutput({"approval_id": str(appr.id), "status": "pending_approval", "vacant": len(vacant)},
                      f"Prepared: {summary}, claims close {rows[0]['claim_by']}. Awaiting approval; nothing has been published yet.",
                      _approval_card(appr, "Proposed overtime opportunity", rows, [("shift", "Shift"), ("department", "Department"),
                                     ("date", "Date"), ("time", "Time"), ("positions", "Positions"), ("claim_by", "Claim by")],
                                     "Approving publishes the opportunity to eligible employees; the first eligible claim wins."))


@register_action("workforce.create_shift", "workforce.schedule")
def _exec_create_shift(db, actor, p: dict[str, Any]) -> dict[str, Any]:
    shift = sh.create_shift(db, actor, get_scope(db, actor), ws.ShiftCreate(template_id=uuid.UUID(p["template_id"]),
                                                                          shift_date=date.fromisoformat(p["shift_date"]),
                                                                          required_count=p["required_count"]))
    return {"shift_id": str(shift.id), "name": shift.name, "date": shift.shift_date.isoformat(), "positions": shift.required_count}


@register_action("workforce.create_opportunity", "workforce.overtime.manage")
def _exec_create_opportunity(db, actor, p: dict[str, Any]) -> dict[str, Any]:
    opp = mk.create_manual(db, actor, get_scope(db, actor), ws.OpportunityCreate(shift_id=uuid.UUID(p["shift_id"]), positions=p["positions"],
                                                                                publish=True), source="ai")
    return {"opportunity_id": str(opp.id), "number": opp.number, "status": opp.status.value, "positions": opp.positions_total}
