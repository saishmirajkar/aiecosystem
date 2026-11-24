"""Overtime calculation from verified attendance, manager approval, and payroll export batches.

Nothing is overtime just because it was scheduled or claimed: each ISO week is recalculated from attendance
against the employee's overtime policy. Exported records are immutable; a later change produces a correction
line (a new revision holding the difference) for the next payroll batch.
"""
from __future__ import annotations

import csv
import io
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import audit, next_number
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, InvalidState, NotFound, ValidationFailed
from app.models import (
    AttendanceRecord, AttendanceStatus, CalcStatus, DayRule, Department, Employee, OvertimeCalculation, OvertimePolicy,
    PayrollExportBatch, RoundingMode,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import Scope, company_tz, employee_brief, hours, overtime_policy, week_start

NUMERIC = ("scheduled_minutes", "worked_minutes", "regular_minutes", "ot_daily_minutes", "ot_weekly_minutes",
           "ot_weekly_off_minutes", "ot_holiday_minutes", "night_minutes", "payable_minutes", "night_allowance", "amount")
CENT = Decimal("0.01")


# ------------------------------------------------------------------ calculation
def _round(mins: int, policy: OvertimePolicy) -> int:
    step = policy.rounding_minutes or 0
    if step <= 0 or mins <= 0:
        return max(0, mins)
    q, r = divmod(mins, step)
    if policy.rounding_mode == RoundingMode.UP and r:
        q += 1
    elif policy.rounding_mode == RoundingMode.NEAREST and r * 2 >= step:
        q += 1
    return q * step


def night_minutes(start: datetime | None, end: datetime | None, policy: OvertimePolicy, tz) -> int:
    if not start or not end or policy.night_start is None or policy.night_end is None or policy.night_start == policy.night_end:
        return 0
    total = 0
    d = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    while d <= last:
        ns = datetime.combine(d, policy.night_start, tz)
        ne = datetime.combine(d if policy.night_end > policy.night_start else d + timedelta(days=1), policy.night_end, tz)
        lo, hi = max(ns, start), min(ne, end)
        if hi > lo:
            total += int((hi - lo).total_seconds() // 60)
        d += timedelta(days=1)
    return total


def snapshot(p: OvertimePolicy) -> dict[str, Any]:
    return {k: (getattr(p, k).value if hasattr(getattr(p, k), "value") else str(getattr(p, k)) if getattr(p, k) is not None else None)
            for k in ("code", "daily_threshold_hours", "weekly_threshold_hours", "ot_multiplier", "weekly_off_rule", "weekly_off_multiplier",
                      "holiday_rule", "holiday_multiplier", "night_start", "night_end", "night_allowance_per_hour", "rounding_minutes",
                      "rounding_mode", "min_overtime_minutes")}


def compute_week(db: Session, emp: Employee, any_day: date) -> dict[date, dict[str, Any]]:
    """Pure calculation for one ISO week (Monday–Sunday) from attendance. Returns values per day that had work."""
    from app.modules.workforce.leave import is_day_off
    tz = company_tz(db)
    policy = overtime_policy(db, emp)
    if policy is None:
        return {}
    ws = week_start(any_day)
    recs = db.execute(select(AttendanceRecord).where(AttendanceRecord.employee_id == emp.id, AttendanceRecord.work_date >= ws,
                                                     AttendanceRecord.work_date < ws + timedelta(days=7))).unique().scalars().all()
    worked: dict[date, int] = defaultdict(int)
    sched: dict[date, int] = defaultdict(int)
    night: dict[date, int] = defaultdict(int)
    for r in recs:
        if r.scheduled_start and r.scheduled_end:
            sched[r.work_date] += max(0, int((r.scheduled_end - r.scheduled_start).total_seconds() // 60) - (r.break_minutes or 0))
        if r.status in (AttendanceStatus.ABSENT, AttendanceStatus.ON_LEAVE):
            continue
        worked[r.work_date] += r.worked_minutes
        night[r.work_date] += night_minutes(r.check_in_at, r.check_out_at, policy, tz)
    daily_thr = int(Decimal(policy.daily_threshold_hours) * 60)
    weekly_thr = int(Decimal(policy.weekly_threshold_hours) * 60)
    rate = Decimal(emp.base_hourly_rate) if emp.base_hourly_rate is not None else None
    running = 0
    out: dict[date, dict[str, Any]] = {}
    for i in range(7):
        d = ws + timedelta(days=i)
        w = worked.get(d, 0)
        if w == 0 and sched.get(d, 0) == 0:
            continue
        day_type = is_day_off(db, emp, d) or "regular"
        daily = weekly = off = hol = 0
        regular = w
        if day_type == "holiday" and policy.holiday_rule == DayRule.ALL_OVERTIME:
            hol, regular = w, 0
        elif day_type == "weekly_off" and policy.weekly_off_rule == DayRule.ALL_OVERTIME:
            off, regular = w, 0
        else:
            daily = max(0, w - daily_thr)
            regular = w - daily
            if running + regular > weekly_thr:
                weekly = min(regular, running + regular - weekly_thr)
                regular -= weekly
            running += regular
        raw = daily + weekly + off + hol
        payable = _round(raw, policy)
        if payable < (policy.min_overtime_minutes or 0):
            payable = 0
        factor = Decimal(payable) / Decimal(raw) if raw else Decimal("0")
        weighted = (Decimal(daily + weekly) * Decimal(policy.ot_multiplier) + Decimal(off) * Decimal(policy.weekly_off_multiplier)
                    + Decimal(hol) * Decimal(policy.holiday_multiplier))
        multiplier = (weighted / Decimal(raw)).quantize(Decimal("0.001")) if raw else Decimal("0")
        night_pay = (Decimal(night.get(d, 0)) / 60 * Decimal(policy.night_allowance_per_hour)).quantize(CENT)
        amount = Decimal("0")
        if rate is not None:
            amount = (rate / 60 * weighted * factor).quantize(CENT, rounding=ROUND_HALF_UP) + night_pay
        out[d] = {"day_type": day_type, "scheduled_minutes": sched.get(d, 0), "worked_minutes": w, "regular_minutes": regular,
                  "ot_daily_minutes": daily, "ot_weekly_minutes": weekly, "ot_weekly_off_minutes": off, "ot_holiday_minutes": hol,
                  "night_minutes": night.get(d, 0), "payable_minutes": payable, "multiplier": multiplier,
                  "night_allowance": night_pay, "amount": amount, "base_rate": rate, "policy": policy}
    return out


def _sum(rows: list[OvertimeCalculation]) -> dict[str, Any]:
    tot: dict[str, Any] = {k: (Decimal("0") if k in ("night_allowance", "amount") else 0) for k in NUMERIC}
    for r in rows:
        for k in NUMERIC:
            tot[k] += getattr(r, k)
    return tot


def recalculate_week(db: Session, emp: Employee, any_day: date) -> int:
    """Bring stored calculations for the week in line with attendance. Returns the number of rows touched."""
    ws = week_start(any_day)
    computed = compute_week(db, emp, any_day)
    existing = db.execute(select(OvertimeCalculation).where(OvertimeCalculation.employee_id == emp.id, OvertimeCalculation.work_date >= ws,
                                                            OvertimeCalculation.work_date < ws + timedelta(days=7))
                          .order_by(OvertimeCalculation.work_date, OvertimeCalculation.revision)).unique().scalars().all()
    by_day: dict[date, list[OvertimeCalculation]] = defaultdict(list)
    for r in existing:
        by_day[r.work_date].append(r)
    touched = 0
    for d in sorted(set(computed) | set(by_day)):
        vals = computed.get(d)
        rows = by_day.get(d, [])
        policy = vals["policy"] if vals else overtime_policy(db, emp)
        target = {k: (vals[k] if vals else (Decimal("0") if k in ("night_allowance", "amount") else 0)) for k in NUMERIC}
        exported = [r for r in rows if r.status == CalcStatus.EXPORTED]
        done = _sum(exported)
        delta = {k: target[k] - done[k] for k in NUMERIC}
        latest = rows[-1] if rows else None
        if not rows and target["worked_minutes"] == 0:
            continue
        open_row = latest if latest is not None and latest.status != CalcStatus.EXPORTED else None
        if open_row is None:
            if exported and all(v == 0 for v in delta.values()):
                continue
            if not exported and all(target[k] == 0 for k in NUMERIC):
                continue
            open_row = OvertimeCalculation(employee_id=emp.id, work_date=d, revision=(latest.revision + 1) if latest else 0,
                                           is_correction=bool(exported), department_id=emp.department_id,
                                           day_type=vals["day_type"] if vals else "regular", status=CalcStatus.CALCULATED)
            db.add(open_row)
        new = delta if exported else target
        changed = any(getattr(open_row, k, None) != new[k] for k in NUMERIC)
        for k in NUMERIC:
            setattr(open_row, k, new[k])
        open_row.day_type = vals["day_type"] if vals else open_row.day_type
        open_row.multiplier = vals["multiplier"] if vals else Decimal("0")
        open_row.base_rate = vals["base_rate"] if vals else emp.base_hourly_rate
        if policy is not None:
            open_row.policy_id, open_row.policy_snapshot = policy.id, snapshot(policy)
        if changed or open_row.id is None:
            touched += 1
            if open_row.status in (CalcStatus.APPROVED, CalcStatus.REJECTED):
                open_row.status, open_row.decided_by, open_row.decided_at = CalcStatus.CALCULATED, None, None
                open_row.approved_minutes, open_row.decision_note = None, "Recalculated after an attendance change"
            if policy is not None and not policy.requires_approval and open_row.status == CalcStatus.CALCULATED:
                open_row.status, open_row.approved_minutes, open_row.decided_at = CalcStatus.APPROVED, open_row.payable_minutes, utcnow()
    db.flush()
    return touched


def recalculate_range(db: Session, actor: Actor, scope: Scope, data: s.RecalcIn) -> dict[str, int]:
    actor.require("workforce.overtime.approve")
    if (data.date_to - data.date_from).days > 92 or data.date_to < data.date_from:
        raise ValidationFailed("Choose a range of at most 92 days")
    stmt = scope.employee_filter(select(Employee))
    if data.department_id:
        stmt = stmt.where(Employee.department_id == data.department_id)
    touched, employees = 0, 0
    for emp in db.execute(stmt).unique().scalars():
        employees += 1
        d = week_start(data.date_from)
        while d <= data.date_to:
            touched += recalculate_week(db, emp, d)
            d += timedelta(days=7)
    audit(db, actor, "workforce.overtime.recalculate", "overtime_calculation", None,
          {"from": data.date_from, "to": data.date_to, "employees": employees, "rows": touched})
    return {"employees": employees, "rows_changed": touched}


# ------------------------------------------------------------------ approval
def approved_amount(c: OvertimeCalculation) -> Decimal:
    if c.approved_minutes is None:
        return Decimal("0")
    ot_amount = c.amount - c.night_allowance
    if c.payable_minutes and c.approved_minutes != c.payable_minutes:
        ot_amount = (ot_amount * Decimal(c.approved_minutes) / Decimal(c.payable_minutes)).quantize(CENT)
    return ot_amount + c.night_allowance


def calc_out(c: OvertimeCalculation, scope: Scope) -> dict[str, Any]:
    show_money = scope.actor.has("workforce.payroll") or scope.is_self(c.employee_id)
    out = {"id": c.id, "employee": employee_brief(c.employee), "work_date": c.work_date, "revision": c.revision,
           "is_correction": c.is_correction, "day_type": c.day_type, "scheduled_minutes": c.scheduled_minutes,
           "worked_minutes": c.worked_minutes, "regular_minutes": c.regular_minutes, "ot_daily_minutes": c.ot_daily_minutes,
           "ot_weekly_minutes": c.ot_weekly_minutes, "ot_weekly_off_minutes": c.ot_weekly_off_minutes,
           "ot_holiday_minutes": c.ot_holiday_minutes, "night_minutes": c.night_minutes, "payable_minutes": c.payable_minutes,
           "approved_minutes": c.approved_minutes, "multiplier": c.multiplier, "status": c.status.value,
           "decision_note": c.decision_note, "decided_at": c.decided_at, "export_batch_id": c.export_batch_id,
           "policy_code": (c.policy_snapshot or {}).get("code"),
           "can_decide": c.status == CalcStatus.CALCULATED and scope.actor.has("workforce.overtime.approve")
                         and scope.manages(c.employee) and not scope.is_self(c.employee)}
    if show_money:
        out.update({"base_rate": c.base_rate, "amount": c.amount, "night_allowance": c.night_allowance,
                    "approved_amount": approved_amount(c) if c.status in (CalcStatus.APPROVED, CalcStatus.EXPORTED) else None})
    return out


def list_calculations(db: Session, scope: Scope, *, date_from: date, date_to: date, status: str | None = None,
                      employee_id: uuid.UUID | None = None, department_id: uuid.UUID | None = None, mine: bool = False,
                      overtime_only: bool = True) -> list[dict[str, Any]]:
    stmt = select(OvertimeCalculation).join(Employee, Employee.id == OvertimeCalculation.employee_id).where(
        OvertimeCalculation.work_date >= date_from, OvertimeCalculation.work_date <= date_to)
    if mine or not scope.manages_anyone:
        stmt = stmt.where(OvertimeCalculation.employee_id == (scope.me.id if scope.me else None))
    else:
        stmt = scope.employee_filter(stmt)
    if status:
        stmt = stmt.where(OvertimeCalculation.status.in_(status.split(",")))
    if employee_id:
        stmt = stmt.where(OvertimeCalculation.employee_id == employee_id)
    if department_id:
        stmt = stmt.where(OvertimeCalculation.department_id == department_id)
    if overtime_only:
        stmt = stmt.where((OvertimeCalculation.payable_minutes != 0) | (OvertimeCalculation.night_minutes != 0))
    rows = db.execute(stmt.order_by(OvertimeCalculation.work_date.desc(), Employee.first_name, OvertimeCalculation.revision)
                      .limit(2000)).unique().scalars().all()
    return [calc_out(c, scope) for c in rows]


def _lock_calc(db: Session, calc_id: uuid.UUID) -> OvertimeCalculation:
    db.execute(select(OvertimeCalculation.id).where(OvertimeCalculation.id == calc_id).with_for_update())
    c = db.execute(select(OvertimeCalculation).where(OvertimeCalculation.id == calc_id).execution_options(populate_existing=True)).unique().scalar_one_or_none()
    if c is None:
        raise NotFound("Overtime record not found")
    return c


def approve(db: Session, actor: Actor, scope: Scope, calc_id: uuid.UUID, data: s.CalcDecisionIn) -> OvertimeCalculation:
    c = _lock_calc(db, calc_id)
    scope.require_manage(c.employee, "workforce.overtime.approve")
    if c.status != CalcStatus.CALCULATED:
        raise InvalidState(f"This record is {c.status.value}")
    minutes_ok = c.payable_minutes if data.approved_minutes is None else data.approved_minutes
    if c.payable_minutes >= 0 and minutes_ok > c.payable_minutes:
        raise ValidationFailed("You can approve at most the calculated overtime")
    if c.payable_minutes < 0:
        minutes_ok = c.payable_minutes  # a negative correction is approved as calculated
    c.status, c.approved_minutes, c.decided_by, c.decided_at, c.decision_note = CalcStatus.APPROVED, minutes_ok, actor.user_id, utcnow(), data.note
    audit(db, actor, "workforce.overtime.approve", "overtime_calculation", c.id,
          {"approved_minutes": minutes_ok, "payable_minutes": c.payable_minutes, "note": data.note})
    db.flush()  # autoflush is off: make the decision visible to later queries in the same transaction
    return c


def reject(db: Session, actor: Actor, scope: Scope, calc_id: uuid.UUID, note: str) -> OvertimeCalculation:
    c = _lock_calc(db, calc_id)
    scope.require_manage(c.employee, "workforce.overtime.approve")
    if c.status != CalcStatus.CALCULATED:
        raise InvalidState(f"This record is {c.status.value}")
    c.status, c.approved_minutes, c.decided_by, c.decided_at, c.decision_note = CalcStatus.REJECTED, 0, actor.user_id, utcnow(), note
    audit(db, actor, "workforce.overtime.reject", "overtime_calculation", c.id, {"note": note})
    db.flush()
    return c


def bulk_approve(db: Session, actor: Actor, scope: Scope, ids: list[uuid.UUID]) -> dict[str, Any]:
    done, skipped = 0, []
    for cid in ids:
        try:
            with db.begin_nested():
                approve(db, actor, scope, cid, s.CalcDecisionIn())
            done += 1
        except Exception as exc:
            skipped.append({"id": str(cid), "reason": getattr(exc, "message", str(exc))})
    return {"approved": done, "skipped": skipped}


# ------------------------------------------------------------------ payroll export
def batch_out(db: Session, b: PayrollExportBatch) -> dict[str, Any]:
    return {"id": b.id, "number": b.number, "period_start": b.period_start, "period_end": b.period_end,
            "department_id": b.department_id, "department_name": db.get(Department, b.department_id).name if b.department_id else None,
            "employee_count": b.employee_count, "line_count": b.line_count, "total_minutes": b.total_minutes,
            "total_hours": hours(b.total_minutes), "total_amount": b.total_amount, "created_at": b.created_at}


def create_export(db: Session, actor: Actor, scope: Scope, data: s.ExportIn) -> PayrollExportBatch:
    actor.require("workforce.payroll")
    db.flush()
    stmt = scope.employee_filter(select(OvertimeCalculation.id).join(Employee, Employee.id == OvertimeCalculation.employee_id).where(
        OvertimeCalculation.status == CalcStatus.APPROVED, OvertimeCalculation.export_batch_id.is_(None),
        OvertimeCalculation.work_date >= data.period_start, OvertimeCalculation.work_date <= data.period_end))
    if data.department_id:
        stmt = stmt.where(OvertimeCalculation.department_id == data.department_id)
    ids = db.execute(stmt.with_for_update(of=OvertimeCalculation)).scalars().all()
    if not ids:
        raise BusinessRuleViolation("There is no approved, unexported overtime in this period", code="nothing_to_export")
    rows = db.execute(select(OvertimeCalculation).where(OvertimeCalculation.id.in_(ids))).unique().scalars().all()
    b = PayrollExportBatch(number=next_number(db, "payroll_export"), period_start=data.period_start, period_end=data.period_end,
                           department_id=data.department_id, employee_count=len({r.employee_id for r in rows}), line_count=len(rows),
                           total_minutes=sum(r.approved_minutes or 0 for r in rows),
                           total_amount=sum((approved_amount(r) for r in rows), Decimal("0")), created_by=actor.user_id)
    db.add(b)
    db.flush()
    for r in rows:
        r.status, r.export_batch_id = CalcStatus.EXPORTED, b.id
    audit(db, actor, "workforce.payroll.export", "payroll_export_batch", b.id,
          {"period": [data.period_start, data.period_end], "lines": len(rows), "department_id": data.department_id})
    return b


def list_exports(db: Session, actor: Actor) -> list[dict[str, Any]]:
    actor.require("workforce.payroll")
    return [batch_out(db, b) for b in db.execute(select(PayrollExportBatch).order_by(PayrollExportBatch.created_at.desc()).limit(100)).scalars()]


COLUMNS = [("employee_code", "Employee code"), ("employee_name", "Employee"), ("department", "Department"), ("work_date", "Date"),
           ("day_type", "Day type"), ("worked_hours", "Worked h"), ("regular_hours", "Regular h"), ("ot_daily_hours", "Daily OT h"),
           ("ot_weekly_hours", "Weekly OT h"), ("ot_weekly_off_hours", "Weekly-off OT h"), ("ot_holiday_hours", "Holiday OT h"),
           ("approved_ot_hours", "Approved OT h"), ("multiplier", "Multiplier"), ("base_rate", "Base rate"), ("night_hours", "Night h"),
           ("night_allowance", "Night allowance"), ("amount", "Amount"), ("correction", "Correction"), ("record_id", "Record id")]


def export_rows(db: Session, actor: Actor, batch_id: uuid.UUID) -> tuple[PayrollExportBatch, list[dict[str, Any]]]:
    actor.require("workforce.payroll")
    b = db.get(PayrollExportBatch, batch_id)
    if b is None:
        raise NotFound("Payroll export not found")
    rows = db.execute(select(OvertimeCalculation).join(Employee, Employee.id == OvertimeCalculation.employee_id)
                      .where(OvertimeCalculation.export_batch_id == b.id)
                      .order_by(Employee.employee_code, OvertimeCalculation.work_date, OvertimeCalculation.revision)).unique().scalars().all()
    out = []
    for r in rows:
        out.append({"employee_code": r.employee.employee_code, "employee_name": r.employee.full_name,
                    "department": r.employee.department.name, "work_date": r.work_date.isoformat(), "day_type": r.day_type,
                    "worked_hours": hours(r.worked_minutes), "regular_hours": hours(r.regular_minutes),
                    "ot_daily_hours": hours(r.ot_daily_minutes), "ot_weekly_hours": hours(r.ot_weekly_minutes),
                    "ot_weekly_off_hours": hours(r.ot_weekly_off_minutes), "ot_holiday_hours": hours(r.ot_holiday_minutes),
                    "approved_ot_hours": hours(r.approved_minutes or 0), "multiplier": r.multiplier,
                    "base_rate": r.base_rate if r.base_rate is not None else "", "night_hours": hours(r.night_minutes),
                    "night_allowance": r.night_allowance, "amount": approved_amount(r), "correction": "yes" if r.is_correction else "",
                    "record_id": str(r.id)})
    return b, out


def _cell(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        try:
            Decimal(v)
        except Exception:
            return "'" + v  # spreadsheet formula-injection guard
    return v


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 correctly
    w = csv.writer(buf)
    w.writerow([label for _, label in COLUMNS])
    for r in rows:
        w.writerow([_cell(str(r[k]) if isinstance(r[k], Decimal) else r[k]) for k, _ in COLUMNS])
    return buf.getvalue()


def to_xlsx(b: PayrollExportBatch, rows: list[dict[str, Any]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Overtime lines"
    ws.append([label for _, label in COLUMNS])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([float(r[k]) if isinstance(r[k], Decimal) else _cell(r[k]) for k, _ in COLUMNS])
    summary = wb.create_sheet("Employee summary")
    summary.append(["Employee code", "Employee", "Department", "Approved OT h", "Amount"])
    for c in summary[1]:
        c.font = Font(bold=True)
    agg: dict[str, list[Any]] = {}
    for r in rows:
        a = agg.setdefault(r["employee_code"], [r["employee_code"], r["employee_name"], r["department"], Decimal("0"), Decimal("0")])
        a[3] += Decimal(r["approved_ot_hours"])
        a[4] += Decimal(r["amount"])
    for a in agg.values():
        summary.append([_cell(a[0]), _cell(a[1]), _cell(a[2]), float(a[3]), float(a[4])])
    info = wb.create_sheet("Batch")
    for k, v in (("Batch", b.number), ("Period", f"{b.period_start} to {b.period_end}"), ("Lines", b.line_count),
                 ("Employees", b.employee_count), ("Approved OT hours", float(hours(b.total_minutes))), ("Total amount", float(b.total_amount))):
        info.append([k, v])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def month_summary(db: Session, emp: Employee, month_start: date) -> dict[str, Any]:
    nxt = (month_start + timedelta(days=32)).replace(day=1)
    rows = db.execute(select(OvertimeCalculation).where(OvertimeCalculation.employee_id == emp.id, OvertimeCalculation.work_date >= month_start,
                                                        OvertimeCalculation.work_date < nxt)).unique().scalars().all()
    pending = [r for r in rows if r.status == CalcStatus.CALCULATED]
    approved = [r for r in rows if r.status in (CalcStatus.APPROVED, CalcStatus.EXPORTED)]
    return {"worked_minutes": sum(r.worked_minutes for r in rows if not r.is_correction),
            "pending_minutes": sum(r.payable_minutes for r in pending),
            "approved_minutes": sum(r.approved_minutes or 0 for r in approved),
            "estimated_amount": sum((r.amount for r in pending), Decimal("0")) + sum((approved_amount(r) for r in approved), Decimal("0"))
            if emp.base_hourly_rate is not None else None}
