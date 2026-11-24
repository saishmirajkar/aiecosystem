"""Role dashboards and workforce / overtime / attendance reports."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ValidationFailed
from app.models import (
    AssignmentStatus, AttendanceCorrection, AttendanceRecord, AttendanceStatus, CalcStatus, CorrectionStatus, Department,
    Employee, EmployeeStatus, LeaveRequest, LeaveStatus, OpportunityStatus, OvertimeCalculation, OvertimeClaim,
    OvertimeOpportunity, Shift, ShiftAssignment, ShiftStatus, WorkforceConflict,
)
from app.modules.workforce.core import Scope, company_tz, employee_brief, hm, hours, require_me, workforce_policy


def _today(db: Session) -> date:
    return utcnow().astimezone(company_tz(db)).date()


def _absent_today(db: Session, scope: Scope, d: date) -> list[dict[str, Any]]:
    """Absent = recorded absent, or a shift that started more than the grace period ago with no check-in."""
    now = utcnow()
    tz = company_tz(db)
    stmt = scope.department_filter(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        Shift.shift_date == d, Shift.status == ShiftStatus.SCHEDULED, ShiftAssignment.status == AssignmentStatus.ASSIGNED,
        ShiftAssignment.start_at <= now), Shift.department_id)
    out = []
    for a in db.execute(stmt).unique().scalars():
        r = db.execute(select(AttendanceRecord).where(AttendanceRecord.assignment_id == a.id)).unique().scalar_one_or_none()
        grace = timedelta(minutes=workforce_policy(db, a.shift.department_id).late_grace_minutes)
        if (r is None and now > a.start_at + grace) or (r is not None and r.status == AttendanceStatus.ABSENT):
            out.append({"employee": employee_brief(a.employee), "shift": a.shift.name, "scheduled": f"{hm(a.start_at, tz)}–{hm(a.end_at, tz)}",
                        "assignment_id": a.id, "recorded": r is not None})
    return out


def on_leave(db: Session, scope: Scope, d_from: date, d_to: date) -> list[dict[str, Any]]:
    stmt = scope.employee_filter(select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id).where(
        LeaveRequest.status == LeaveStatus.APPROVED, LeaveRequest.start_date <= d_to, LeaveRequest.end_date >= d_from))
    private = scope.actor.has("workforce.leave.approve")
    return [{"employee": employee_brief(r.employee), "leave_type": r.leave_type.name if private else None, "start_date": r.start_date,
             "end_date": r.end_date, "half_day": r.half_day.value, "request_id": r.id}
            for r in db.execute(stmt.order_by(LeaveRequest.start_date)).unique().scalars()]


def coverage(db: Session, scope: Scope, d: date, department_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    stmt = scope.department_filter(select(Shift).where(Shift.shift_date == d, Shift.status == ShiftStatus.SCHEDULED), Shift.department_id)
    if department_id:
        stmt = stmt.where(Shift.department_id == department_id)
    agg: dict[uuid.UUID, dict[str, Any]] = {}
    for sh in db.execute(stmt).unique().scalars():
        a = agg.setdefault(sh.department_id, {"department_id": sh.department_id, "department_name": sh.department.name,
                                              "required": 0, "assigned": 0, "shifts": 0})
        a["shifts"] += 1
        a["required"] += sum(1 for p in sh.positions if p.is_active)
        a["assigned"] += db.execute(select(func.count()).where(ShiftAssignment.shift_id == sh.id,
                                                               ShiftAssignment.status == AssignmentStatus.ASSIGNED)).scalar_one()
    for a in agg.values():
        a["vacant"] = max(0, a["required"] - a["assigned"])
        a["coverage_pct"] = round(100 * a["assigned"] / a["required"]) if a["required"] else 100
    return sorted(agg.values(), key=lambda x: x["department_name"])


def manager_dashboard(db: Session, scope: Scope, d: date | None = None, department_id: uuid.UUID | None = None) -> dict[str, Any]:
    from app.modules.workforce.marketplace import list_for_manager, vacancies
    if not scope.manages_anyone:
        raise ValidationFailed("The manager dashboard needs workforce access to a department")
    tz = company_tz(db)
    d = d or _today(db)
    month_start = d.replace(day=1)
    sched = scope.department_filter(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        Shift.shift_date == d, Shift.status == ShiftStatus.SCHEDULED, ShiftAssignment.status == AssignmentStatus.ASSIGNED), Shift.department_id)
    if department_id:
        sched = sched.where(Shift.department_id == department_id)
    scheduled = db.execute(sched.order_by(ShiftAssignment.start_at)).unique().scalars().all()
    pend_leave = scope.employee_filter(select(func.count()).select_from(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id)
                                       .where(LeaveRequest.status == LeaveStatus.PENDING))
    pend_corr = scope.employee_filter(select(func.count()).select_from(AttendanceCorrection).join(Employee, Employee.id == AttendanceCorrection.employee_id)
                                      .where(AttendanceCorrection.status == CorrectionStatus.PENDING))
    pend_ot = scope.employee_filter(select(func.count()).select_from(OvertimeCalculation).join(Employee, Employee.id == OvertimeCalculation.employee_id)
                                    .where(OvertimeCalculation.status == CalcStatus.CALCULATED, OvertimeCalculation.payable_minutes != 0))
    conflicts = scope.department_filter(select(func.count()).select_from(WorkforceConflict).where(WorkforceConflict.status == "open"),
                                        WorkforceConflict.department_id)
    ot_by_emp = scope.employee_filter(select(Employee, func.sum(OvertimeCalculation.payable_minutes),
                                             func.sum(func.coalesce(OvertimeCalculation.approved_minutes, 0)))
                                      .join(OvertimeCalculation, OvertimeCalculation.employee_id == Employee.id)
                                      .where(OvertimeCalculation.work_date >= month_start, OvertimeCalculation.work_date <= d,
                                             OvertimeCalculation.status != CalcStatus.REJECTED)
                                      .group_by(Employee.id).order_by(func.sum(OvertimeCalculation.payable_minutes).desc()).limit(10))
    recent_claims = scope.department_filter(select(OvertimeClaim, OvertimeOpportunity).join(OvertimeOpportunity, OvertimeOpportunity.id == OvertimeClaim.opportunity_id)
                                            .order_by(OvertimeClaim.created_at.desc()).limit(12), OvertimeOpportunity.department_id)
    opps = list_for_manager(db, scope, status="draft,published,open,partially_filled,expired", page_size=50)["items"]
    week_ago = utcnow() - timedelta(days=7)
    filled = scope.department_filter(select(func.count()).select_from(OvertimeOpportunity).where(
        OvertimeOpportunity.status == OpportunityStatus.FILLED, OvertimeOpportunity.filled_at >= week_ago), OvertimeOpportunity.department_id)
    return {
        "date": d,
        "scheduled_today": [{"employee": employee_brief(a.employee), "shift": a.shift.name, "scheduled": f"{hm(a.start_at, tz)}–{hm(a.end_at, tz)}",
                             "source": a.source.value} for a in scheduled],
        "on_leave_today": on_leave(db, scope, d, d),
        "absent_today": _absent_today(db, scope, d),
        "vacancies": vacancies(db, scope, d, d + timedelta(days=7), department_id),
        "opportunities": opps,
        "filled_last_7_days": db.execute(filled).scalar_one(),
        "pending": {"leave": db.execute(pend_leave).scalar_one(), "corrections": db.execute(pend_corr).scalar_one(),
                    "overtime": db.execute(pend_ot).scalar_one(), "conflicts": db.execute(conflicts).scalar_one()},
        "overtime_by_employee": [{"employee": employee_brief(e), "hours": hours(int(p or 0)), "approved_hours": hours(int(ap or 0))}
                                 for e, p, ap in db.execute(ot_by_emp).unique().all()],
        "coverage": coverage(db, scope, d, department_id),
        "recent_claims": [{"employee": employee_brief(c.employee), "opportunity": o.number, "title": o.title, "outcome": c.outcome.value,
                           "reason": c.reason, "at": c.created_at} for c, o in db.execute(recent_claims).unique().all()],
    }


def employee_home(db: Session, scope: Scope) -> dict[str, Any]:
    from app.modules.workforce import leave as leave_svc
    from app.modules.workforce.marketplace import marketplace_for_employee, my_assignments
    from app.modules.workforce.overtime import month_summary
    from app.modules.workforce.shifts import assignment_out, upcoming_for
    emp = require_me(scope)
    tz = company_tz(db)
    d = _today(db)
    upcoming = upcoming_for(db, emp, 14)
    today = [a for a in upcoming if a.start_at.astimezone(tz).date() == d or (a.start_at <= utcnow() <= a.end_at)]
    att = db.execute(select(AttendanceRecord.status, func.count()).where(AttendanceRecord.employee_id == emp.id,
                                                                         AttendanceRecord.work_date >= d.replace(day=1), AttendanceRecord.work_date <= d)
                     .group_by(AttendanceRecord.status)).all()
    policy = workforce_policy(db, emp.department_id)
    my_requests = db.execute(select(LeaveRequest).where(LeaveRequest.employee_id == emp.id).order_by(LeaveRequest.created_at.desc()).limit(5)).unique().scalars().all()
    return {
        "employee": {**employee_brief(emp), "team_names": [t.name for t in emp.teams]},
        "today": [assignment_out(db, a) for a in today],
        "upcoming": [assignment_out(db, a) for a in upcoming],
        "opportunities": marketplace_for_employee(db, scope)[:10],
        "my_overtime": my_assignments(db, scope, upcoming_only=True),
        "leave_balances": leave_svc.balances_out(db, emp, d.year),
        "leave_requests": [{"id": r.id, "number": r.number, "leave_type": r.leave_type.name, "start_date": r.start_date, "end_date": r.end_date,
                            "days": r.days, "status": r.status.value} for r in my_requests],
        "attendance_month": {k.value: n for k, n in att},
        "overtime_month": month_summary(db, emp, d.replace(day=1)),
        "self_check_in": policy.allow_self_check_in,
        "show_pay_estimates": policy.show_pay_estimates and emp.base_hourly_rate is not None,
    }


# ------------------------------------------------------------------ reports
def _period(date_from: date, date_to: date) -> None:
    if date_to < date_from or (date_to - date_from).days > 370:
        raise ValidationFailed("Choose a period of at most one year")


def overtime_report(db: Session, scope: Scope, date_from: date, date_to: date, group_by: str = "employee",
                    department_id: uuid.UUID | None = None) -> dict[str, Any]:
    from app.modules.workforce.overtime import approved_amount
    _period(date_from, date_to)
    stmt = scope.employee_filter(select(OvertimeCalculation).join(Employee, Employee.id == OvertimeCalculation.employee_id).where(
        OvertimeCalculation.work_date >= date_from, OvertimeCalculation.work_date <= date_to, OvertimeCalculation.status != CalcStatus.REJECTED))
    if department_id:
        stmt = stmt.where(OvertimeCalculation.department_id == department_id)
    money = scope.actor.has("workforce.payroll")
    groups: dict[Any, dict[str, Any]] = {}
    for c in db.execute(stmt).unique().scalars():
        if group_by == "department":
            key, label = c.department_id, db.get(Department, c.department_id).name
        else:
            key, label = c.employee_id, f"{c.employee.full_name} ({c.employee.employee_code})"
        g = groups.setdefault(key, {"key": key, "label": label, "worked_minutes": 0, "regular_minutes": 0, "ot_daily_minutes": 0,
                                    "ot_weekly_minutes": 0, "ot_weekly_off_minutes": 0, "ot_holiday_minutes": 0, "payable_minutes": 0,
                                    "approved_minutes": 0, "pending_minutes": 0, "amount": Decimal("0")})
        for k in ("worked_minutes", "regular_minutes", "ot_daily_minutes", "ot_weekly_minutes", "ot_weekly_off_minutes",
                  "ot_holiday_minutes", "payable_minutes"):
            g[k] += getattr(c, k)
        if c.status in (CalcStatus.APPROVED, CalcStatus.EXPORTED):
            g["approved_minutes"] += c.approved_minutes or 0
            g["amount"] += approved_amount(c)
        else:
            g["pending_minutes"] += c.payable_minutes
    rows = sorted(groups.values(), key=lambda g: -g["payable_minutes"])
    for g in rows:
        g.update({k.replace("_minutes", "_hours"): hours(v) for k, v in list(g.items()) if k.endswith("_minutes")})
        if not money:
            g.pop("amount")
    totals = {"payable_hours": hours(sum(g["payable_minutes"] for g in rows)), "approved_hours": hours(sum(g["approved_minutes"] for g in rows))}
    if money:
        totals["amount"] = sum((g["amount"] for g in rows), Decimal("0"))
    return {"date_from": date_from, "date_to": date_to, "group_by": group_by, "rows": rows, "totals": totals}


def attendance_report(db: Session, scope: Scope, date_from: date, date_to: date, department_id: uuid.UUID | None = None) -> dict[str, Any]:
    _period(date_from, date_to)
    stmt = scope.employee_filter(select(AttendanceRecord).join(Employee, Employee.id == AttendanceRecord.employee_id).where(
        AttendanceRecord.work_date >= date_from, AttendanceRecord.work_date <= date_to))
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    groups: dict[uuid.UUID, dict[str, Any]] = {}
    for r in db.execute(stmt).unique().scalars():
        g = groups.setdefault(r.employee_id, {"employee": employee_brief(r.employee), "shifts": 0, "worked_minutes": 0, "late_minutes": 0,
                                              **{s.value: 0 for s in AttendanceStatus}})
        g["shifts"] += 1
        g[r.status.value] += 1
        g["worked_minutes"] += r.worked_minutes
        g["late_minutes"] += r.late_minutes
    rows = sorted(groups.values(), key=lambda g: g["employee"]["full_name"])
    for g in rows:
        g["worked_hours"] = hours(g["worked_minutes"])
        attended = g["present"] + g["late"] + g["half_day"]
        due = g["shifts"] - g["on_leave"] - g["holiday"] - g["weekly_off"]
        g["attendance_pct"] = round(100 * attended / due) if due else None
    return {"date_from": date_from, "date_to": date_to, "rows": rows}


def workforce_report(db: Session, scope: Scope, date_from: date, date_to: date) -> dict[str, Any]:
    _period(date_from, date_to)
    head = scope.employee_filter(select(Department.name, Employee.status, func.count()).join(Department, Department.id == Employee.department_id)
                                 .group_by(Department.name, Employee.status))
    headcount: dict[str, dict[str, int]] = defaultdict(dict)
    for dept, status, n in db.execute(head).all():
        headcount[dept][status.value if hasattr(status, "value") else status] = n
    leave = scope.employee_filter(select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id).where(
        LeaveRequest.status == LeaveStatus.APPROVED, LeaveRequest.start_date <= date_to, LeaveRequest.end_date >= date_from))
    by_type: dict[str, Decimal] = defaultdict(Decimal)
    for r in db.execute(leave).unique().scalars():
        by_type[r.leave_type.name] += r.days
    opps = scope.department_filter(select(OvertimeOpportunity).join(Shift, Shift.id == OvertimeOpportunity.shift_id).where(
        Shift.shift_date >= date_from, Shift.shift_date <= date_to), OvertimeOpportunity.department_id)
    stats = {"total": 0, "filled": 0, "expired": 0, "cancelled": 0, "open": 0, "positions": 0, "by_marketplace": 0, "by_manager": 0}
    fill_times = []
    for o in db.execute(opps).unique().scalars():
        stats["total"] += 1
        stats["positions"] += o.positions_total
        key = {"filled": "filled", "expired": "expired", "cancelled": "cancelled"}.get(o.status.value, "open")
        stats[key] += 1
        if o.filled_at and o.published_at:
            fill_times.append((o.filled_at - o.published_at).total_seconds() / 60)
    for src, n in db.execute(scope.department_filter(
            select(ShiftAssignment.source, func.count()).join(Shift, Shift.id == ShiftAssignment.shift_id)
            .where(Shift.shift_date >= date_from, Shift.shift_date <= date_to, ShiftAssignment.opportunity_id.is_not(None),
                   ShiftAssignment.status == AssignmentStatus.ASSIGNED).group_by(ShiftAssignment.source), Shift.department_id)).all():
        stats["by_marketplace" if src.value == "marketplace" else "by_manager"] += n
    stats["median_minutes_to_fill"] = round(sorted(fill_times)[len(fill_times) // 2]) if fill_times else None
    return {"date_from": date_from, "date_to": date_to,
            "headcount": [{"department": k, **v, "total": sum(v.values())} for k, v in sorted(headcount.items())],
            "leave_days_by_type": [{"leave_type": k, "days": v} for k, v in sorted(by_type.items())],
            "overtime_marketplace": stats,
            "active_employees": sum(v.get(EmployeeStatus.ACTIVE.value, 0) for v in headcount.values())}
