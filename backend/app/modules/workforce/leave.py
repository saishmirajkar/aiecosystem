"""Leave types, balances, requests and the approval workflow.

Approval never deletes a shift assignment: the employee's assignments become `on_leave` (kept for history) and
their positions become vacancies, which the marketplace turns into overtime opportunities.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from sqlalchemy import func, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.common import audit, diff, next_number
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, Conflict, InvalidState, NotFound, PermissionDenied, ValidationFailed
from app.models import (
    AssignmentStatus, Attachment, ConflictKind, Employee, HalfDay, Holiday, LeaveApproval, LeaveBalance, LeaveRequest,
    LeaveStatus, LeaveType, PositionKind, Shift, ShiftAssignment, ShiftPattern, ShiftPosition, ShiftStatus,
    WorkforceConflict, leave_request_shifts,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import (
    Scope, company_tz, employee_brief, get_employee, hm, require_me, users_managing, workforce_policy,
)
from app.modules.workforce.notify import notify

HALF = Decimal("0.5")


# ------------------------------------------------------------------ types
def type_out(t: LeaveType) -> dict[str, Any]:
    return {k: getattr(t, k) for k in ("id", "code", "name", "is_paid", "annual_days", "carry_forward_max", "unlimited",
                                       "requires_document", "allow_half_day", "color", "is_active")}


def create_type(db: Session, actor: Actor, data: s.LeaveTypeIn) -> LeaveType:
    if db.execute(select(LeaveType).where(func.lower(LeaveType.code) == data.code.lower())).first():
        raise Conflict(f"Leave type '{data.code}' already exists")
    t = LeaveType(**data.model_dump(), created_by=actor.user_id)
    db.add(t)
    db.flush()
    audit(db, actor, "workforce.leave_type.create", "leave_type", t.id, data.model_dump())
    return t


def update_type(db: Session, actor: Actor, type_id: uuid.UUID, data: s.LeaveTypeUpdate) -> LeaveType:
    t = db.get(LeaveType, type_id)
    if t is None:
        raise NotFound("Leave type not found")
    audit(db, actor, "workforce.leave_type.update", "leave_type", t.id, diff(t, data.model_dump(exclude_unset=True)))
    return t


# ------------------------------------------------------------------ working days & balances
def is_day_off(db: Session, emp: Employee, d: date) -> str | None:
    """'holiday' / 'weekly_off' / None. Weekly offs come from the employee's pattern, else the policy's weekdays."""
    hol = db.execute(select(Holiday.id).where(Holiday.holiday_date == d, or_(
        Holiday.work_location_id.is_(None), Holiday.work_location_id == emp.work_location_id))).first()
    if hol:
        return "holiday"
    if emp.shift_pattern_id and emp.pattern_anchor_date and d >= emp.pattern_anchor_date:
        pattern = db.get(ShiftPattern, emp.shift_pattern_id)
        idx = (d - emp.pattern_anchor_date).days % pattern.cycle_days
        return None if any(x.day_index == idx and x.template_id for x in pattern.days) else "weekly_off"
    days = workforce_policy(db, emp.department_id).weekly_off_days or []
    return "weekly_off" if d.isoweekday() in days else None


def working_days(db: Session, emp: Employee, start: date, end: date, half_day: HalfDay = HalfDay.NONE) -> Decimal:
    total = Decimal("0")
    d = start
    while d <= end:
        if is_day_off(db, emp, d) is None:
            total += HALF if half_day != HalfDay.NONE else Decimal("1")
        d += timedelta(days=1)
    return total


def _entitlement(t: LeaveType, emp: Employee, year: int) -> Decimal:
    if t.unlimited:
        return Decimal("0")
    annual = Decimal(t.annual_days)
    doj = emp.date_of_joining
    if doj and doj.year == year:
        months = Decimal(13 - doj.month)
        annual = (annual * months / 12 / HALF).to_integral_value(rounding=ROUND_FLOOR) * HALF
    elif doj and doj.year > year:
        annual = Decimal("0")
    return annual


def balance(db: Session, emp: Employee, t: LeaveType, year: int, *, lock: bool = False) -> LeaveBalance:
    row = db.get(LeaveBalance, (emp.id, t.id, year))
    if row is None:
        carried = Decimal("0")
        prev = db.get(LeaveBalance, (emp.id, t.id, year - 1))
        if prev is not None and t.carry_forward_max > 0:
            carried = min(max(prev.available, Decimal("0")), Decimal(t.carry_forward_max))
        db.execute(pg_insert(LeaveBalance).values(employee_id=emp.id, leave_type_id=t.id, year=year, entitled=_entitlement(t, emp, year),
                                                  carried_forward=carried, adjusted=0, used=0).on_conflict_do_nothing())
        db.flush()
    if lock:
        db.execute(select(LeaveBalance.employee_id).where(LeaveBalance.employee_id == emp.id, LeaveBalance.leave_type_id == t.id,
                                                          LeaveBalance.year == year).with_for_update())
    row = db.execute(select(LeaveBalance).where(LeaveBalance.employee_id == emp.id, LeaveBalance.leave_type_id == t.id,
                                                LeaveBalance.year == year).execution_options(populate_existing=True)).unique().scalar_one()
    return row


def balances_out(db: Session, emp: Employee, year: int) -> list[dict[str, Any]]:
    out = []
    for t in db.execute(select(LeaveType).where(LeaveType.is_active.is_(True)).order_by(LeaveType.code)).scalars():
        b = balance(db, emp, t, year)
        pending = db.execute(select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
            LeaveRequest.employee_id == emp.id, LeaveRequest.leave_type_id == t.id, LeaveRequest.status == LeaveStatus.PENDING,
            func.extract("year", LeaveRequest.start_date) == year)).scalar_one()
        out.append({"leave_type_id": t.id, "code": t.code, "name": t.name, "color": t.color, "unlimited": t.unlimited,
                    "year": year, "entitled": b.entitled, "carried_forward": b.carried_forward, "adjusted": b.adjusted,
                    "used": b.used, "pending": Decimal(pending), "available": None if t.unlimited else b.available})
    return out


def adjust_balance(db: Session, actor: Actor, data: s.BalanceAdjustIn) -> LeaveBalance:
    actor.require("workforce.employees.manage")
    emp = get_employee(db, data.employee_id)
    t = db.get(LeaveType, data.leave_type_id)
    if t is None:
        raise NotFound("Leave type not found")
    b = balance(db, emp, t, data.year, lock=True)
    b.adjusted += data.adjustment
    audit(db, actor, "workforce.leave_balance.adjust", "employee", emp.id,
          {"leave_type": t.code, "year": data.year, "adjustment": data.adjustment, "reason": data.reason})
    return b


# ------------------------------------------------------------------ requests
def affected_assignments(db: Session, emp: Employee, start: date, end: date, statuses=(AssignmentStatus.ASSIGNED,)) -> list[ShiftAssignment]:
    return list(db.execute(select(ShiftAssignment).join(Shift, Shift.id == ShiftAssignment.shift_id).where(
        ShiftAssignment.employee_id == emp.id, ShiftAssignment.status.in_(statuses), Shift.status == ShiftStatus.SCHEDULED,
        Shift.shift_date >= start, Shift.shift_date <= end).order_by(ShiftAssignment.start_at)).unique().scalars())


def preview(db: Session, scope: Scope, employee_id: uuid.UUID | None, start: date, end: date, half_day: HalfDay) -> dict[str, Any]:
    emp = get_employee(db, employee_id) if employee_id else require_me(scope)
    scope.require_see(emp)
    if end < start:
        raise ValidationFailed("End date must be on or after the start date")
    tz = company_tz(db)
    return {"days": working_days(db, emp, start, end, half_day),
            "affected_shifts": [{"shift_id": a.shift_id, "name": a.shift.name, "date": a.shift.shift_date,
                                 "start": hm(a.start_at, tz), "end": hm(a.end_at, tz)} for a in affected_assignments(db, emp, start, end)]}


def _log(db: Session, req: LeaveRequest, action: str, actor: Actor | None, note: str | None = None) -> None:
    db.add(LeaveApproval(leave_request_id=req.id, action=action, actor_user_id=actor.user_id if actor else None, note=note))


def get_request(db: Session, scope: Scope, request_id: uuid.UUID, *, lock: bool = False) -> LeaveRequest:
    if lock:
        db.execute(select(LeaveRequest.id).where(LeaveRequest.id == request_id).with_for_update())
    req = db.execute(select(LeaveRequest).where(LeaveRequest.id == request_id).execution_options(populate_existing=True)).unique().scalar_one_or_none()
    if req is None or not scope.can_see(req.employee):
        raise NotFound("Leave request not found")
    return req


def request_out(db: Session, req: LeaveRequest, scope: Scope) -> dict[str, Any]:
    tz = company_tz(db)
    linked = db.execute(select(ShiftAssignment).join(leave_request_shifts, leave_request_shifts.c.assignment_id == ShiftAssignment.id)
                        .where(leave_request_shifts.c.leave_request_id == req.id).order_by(ShiftAssignment.start_at)).unique().scalars().all()
    if not linked and req.status in (LeaveStatus.DRAFT, LeaveStatus.PENDING):
        linked = affected_assignments(db, req.employee, req.start_date, req.end_date)
    private = scope.is_self(req.employee) or scope.manages(req.employee)
    from app.modules.workforce.marketplace import opportunity_brief_for_assignment
    log = db.execute(select(LeaveApproval).where(LeaveApproval.leave_request_id == req.id).order_by(LeaveApproval.created_at)).scalars().all()
    attachments = db.execute(select(Attachment).where(Attachment.entity_type == "leave_request", Attachment.entity_id == str(req.id))).scalars().all()
    return {
        "id": req.id, "number": req.number, "employee": employee_brief(req.employee), "leave_type_id": req.leave_type_id,
        "leave_type": req.leave_type.name, "leave_type_code": req.leave_type.code, "start_date": req.start_date, "end_date": req.end_date,
        "half_day": req.half_day.value, "days": req.days, "reason": req.reason if private else None, "status": req.status.value,
        "submitted_at": req.submitted_at, "decided_at": req.decided_at, "decision_note": req.decision_note,
        "cancelled_at": req.cancelled_at, "cancel_reason": req.cancel_reason, "created_at": req.created_at,
        "requires_document": req.leave_type.requires_document,
        "attachments": [{"id": a.id, "filename": a.filename, "size_bytes": a.size_bytes} for a in attachments] if private else [],
        "affected_shifts": [{"assignment_id": a.id, "shift_id": a.shift_id, "name": a.shift.name, "date": a.shift.shift_date,
                             "start": hm(a.start_at, tz), "end": hm(a.end_at, tz), "assignment_status": a.status.value,
                             "opportunity": opportunity_brief_for_assignment(db, a)} for a in linked],
        "history": [{"action": x.action, "note": x.note, "at": x.created_at} for x in log],
        "can_decide": req.status == LeaveStatus.PENDING and scope.actor.has("workforce.leave.approve")
                      and scope.manages(req.employee) and not scope.is_self(req.employee),
        "can_cancel": req.status in (LeaveStatus.DRAFT, LeaveStatus.PENDING, LeaveStatus.APPROVED)
                      and (scope.is_self(req.employee) or (scope.actor.has("workforce.leave.approve") and scope.manages(req.employee))),
    }


def list_requests(db: Session, scope: Scope, *, status: str | None = None, mine: bool = False, employee_id: uuid.UUID | None = None,
                  date_from: date | None = None, date_to: date | None = None, page: int = 1, page_size: int = 25) -> dict[str, Any]:
    stmt = select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id)
    if mine or not scope.manages_anyone:
        stmt = stmt.where(LeaveRequest.employee_id == (scope.me.id if scope.me else None))
    else:
        stmt = scope.employee_filter(stmt)
        stmt = stmt.where(or_(LeaveRequest.status != LeaveStatus.DRAFT, LeaveRequest.employee_id == (scope.me.id if scope.me else None)))
    if status:
        stmt = stmt.where(LeaveRequest.status.in_(status.split(",")))
    if employee_id:
        stmt = stmt.where(LeaveRequest.employee_id == employee_id)
    if date_from:
        stmt = stmt.where(LeaveRequest.end_date >= date_from)
    if date_to:
        stmt = stmt.where(LeaveRequest.start_date <= date_to)
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(LeaveRequest.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).unique().scalars().all()
    return {"items": [request_out(db, r, scope) for r in rows], "total": total, "page": page, "page_size": page_size}


def create(db: Session, actor: Actor, scope: Scope, data: s.LeaveCreate) -> LeaveRequest:
    emp = get_employee(db, data.employee_id) if data.employee_id else require_me(scope)
    if not scope.is_self(emp):
        scope.require_manage(emp, "workforce.leave.approve")
    else:
        actor.require("workforce.self")
    t = db.get(LeaveType, data.leave_type_id)
    if t is None or not t.is_active:
        raise ValidationFailed("Leave type not found")
    if data.half_day != HalfDay.NONE and not t.allow_half_day:
        raise ValidationFailed(f"{t.name} cannot be taken as a half day")
    overlap = db.execute(select(LeaveRequest.number).where(
        LeaveRequest.employee_id == emp.id, LeaveRequest.status.in_([LeaveStatus.PENDING, LeaveStatus.APPROVED]),
        LeaveRequest.start_date <= data.end_date, LeaveRequest.end_date >= data.start_date)).first()
    if overlap:
        raise Conflict(f"These dates overlap leave request {overlap[0]}")
    days = working_days(db, emp, data.start_date, data.end_date, data.half_day)
    if days == 0:
        raise ValidationFailed("The selected dates are all weekly offs or holidays")
    req = LeaveRequest(number=next_number(db, "leave_request"), employee_id=emp.id, leave_type_id=t.id, start_date=data.start_date,
                       end_date=data.end_date, half_day=data.half_day, days=days, reason=data.reason, status=LeaveStatus.DRAFT,
                       created_by=actor.user_id)
    db.add(req)
    db.flush()
    _log(db, req, "created", actor)
    audit(db, actor, "workforce.leave.create", "leave_request", req.id,
          {"employee_id": emp.id, "type": t.code, "start": data.start_date, "end": data.end_date, "days": days})
    if data.submit:
        submit(db, actor, scope, req.id)
    db.refresh(req)
    return req


def submit(db: Session, actor: Actor, scope: Scope, request_id: uuid.UUID) -> LeaveRequest:
    req = get_request(db, scope, request_id, lock=True)
    if not (scope.is_self(req.employee) or scope.manages(req.employee)):
        raise PermissionDenied("You cannot submit this request")
    if req.status != LeaveStatus.DRAFT:
        raise InvalidState(f"This request is already {req.status.value}")
    t = req.leave_type
    if t.requires_document and not db.execute(select(Attachment.id).where(Attachment.entity_type == "leave_request",
                                                                          Attachment.entity_id == str(req.id))).first():
        raise ValidationFailed(f"{t.name} needs a supporting document. Attach it, then submit.", code="document_required")
    if not t.unlimited:
        b = balance(db, req.employee, t, req.start_date.year)
        pending = db.execute(select(func.coalesce(func.sum(LeaveRequest.days), 0)).where(
            LeaveRequest.employee_id == req.employee_id, LeaveRequest.leave_type_id == t.id, LeaveRequest.status == LeaveStatus.PENDING,
            func.extract("year", LeaveRequest.start_date) == req.start_date.year)).scalar_one()
        if b.available - Decimal(pending) < req.days:
            raise BusinessRuleViolation(f"Not enough {t.name}: {b.available - Decimal(pending):f} day(s) available, {req.days:f} requested",
                                        code="insufficient_balance")
    req.status, req.submitted_at = LeaveStatus.PENDING, utcnow()
    _log(db, req, "submitted", actor)
    emp = req.employee
    approvers = users_managing(db, emp.department_id, "workforce.leave.approve", exclude_user=emp.user_id, employee=emp)
    shifts = len(affected_assignments(db, emp, req.start_date, req.end_date))
    span = f"{req.start_date:%d %b}" + (f"–{req.end_date:%d %b}" if req.end_date != req.start_date else "")
    notify(db, approvers, "leave_submitted", f"Leave request from {emp.full_name}",
           f"{emp.full_name} applied for {t.name}, {span} ({req.days:f} day(s)). {shifts} scheduled shift(s) affected.",
           {"leave_request_id": str(req.id), "link": f"/workforce/leave/{req.id}"}, dedupe_key=f"leave_submitted:{req.id}")
    audit(db, actor, "workforce.leave.submit", "leave_request", req.id)
    return req


def approve(db: Session, actor: Actor, scope: Scope, request_id: uuid.UUID, note: str | None) -> dict[str, Any]:
    from app.modules.workforce import marketplace
    req = get_request(db, scope, request_id, lock=True)
    scope.require_manage(req.employee, "workforce.leave.approve")
    if req.status != LeaveStatus.PENDING:
        raise InvalidState(f"This request is {req.status.value}")
    t, emp = req.leave_type, req.employee
    if not t.unlimited:
        b = balance(db, emp, t, req.start_date.year, lock=True)
        if b.available < req.days:
            raise BusinessRuleViolation(f"Not enough {t.name}: {b.available:f} day(s) available", code="insufficient_balance")
        b.used += req.days
    req.status, req.decided_by, req.decided_at, req.decision_note = LeaveStatus.APPROVED, actor.user_id, utcnow(), note
    _log(db, req, "approved", actor, note)

    affected = affected_assignments(db, emp, req.start_date, req.end_date)
    policy = workforce_policy(db, emp.department_id)
    now = utcnow()
    opportunities: list[Any] = []
    by_shift: dict[uuid.UUID, list[ShiftPosition]] = defaultdict(list)
    shifts: dict[uuid.UUID, Shift] = {}
    for a in affected:
        db.execute(insert(leave_request_shifts).values(leave_request_id=req.id, assignment_id=a.id))
        if req.half_day == HalfDay.NONE:
            a.status, a.leave_request_id, a.ended_at, a.ended_by = AssignmentStatus.ON_LEAVE, req.id, now, actor.user_id
            a.end_reason = f"Approved leave {req.number}"
            if a.start_at > now:
                by_shift[a.shift_id].append(a.position)
                shifts[a.shift_id] = a.shift
        elif policy.half_day_auto_cover and a.start_at > now:
            pos = marketplace.add_cover_position(db, a.shift, req.half_day, req.id)
            by_shift[a.shift_id].append(pos)
            shifts[a.shift_id] = a.shift
    db.flush()
    for shift_id, positions in by_shift.items():
        opportunities.append(marketplace.create_for_positions(db, actor, shifts[shift_id], positions, source="leave"))

    span = f"{req.start_date:%d %b}" + (f"–{req.end_date:%d %b}" if req.end_date != req.start_date else "")
    if emp.user_id:
        notify(db, [emp.user_id], "leave_decided", f"Leave approved: {t.name}, {span}",
               f"Your {t.name} for {span} was approved." + (f" Note: {note}" if note else ""),
               {"leave_request_id": str(req.id)}, dedupe_key=f"leave_decided:{req.id}")
    audit(db, actor, "workforce.leave.approve", "leave_request", req.id,
          {"note": note, "assignments_on_leave": [str(a.id) for a in affected], "opportunities": [str(o.id) for o in opportunities]})
    return {"request": req, "opportunities": opportunities, "affected": affected}


def reject(db: Session, actor: Actor, scope: Scope, request_id: uuid.UUID, note: str) -> LeaveRequest:
    req = get_request(db, scope, request_id, lock=True)
    scope.require_manage(req.employee, "workforce.leave.approve")
    if req.status != LeaveStatus.PENDING:
        raise InvalidState(f"This request is {req.status.value}")
    req.status, req.decided_by, req.decided_at, req.decision_note = LeaveStatus.REJECTED, actor.user_id, utcnow(), note
    _log(db, req, "rejected", actor, note)
    if req.employee.user_id:
        notify(db, [req.employee.user_id], "leave_decided", f"Leave not approved: {req.leave_type.name}",
               f"Your {req.leave_type.name} request {req.number} was not approved. Reason: {note}",
               {"leave_request_id": str(req.id)}, dedupe_key=f"leave_decided:{req.id}")
    audit(db, actor, "workforce.leave.reject", "leave_request", req.id, {"note": note})
    return req


def cancel(db: Session, actor: Actor, scope: Scope, request_id: uuid.UUID, reason: str) -> dict[str, Any]:
    """Cancel a request. For approved leave, shifts are restored; where a replacement already works the shift,
    a conflict is raised for the manager instead of removing anyone silently."""
    from app.modules.workforce import marketplace
    from app.modules.workforce.shifts import occupant
    req = get_request(db, scope, request_id, lock=True)
    own = scope.is_self(req.employee)
    if not own:
        scope.require_manage(req.employee, "workforce.leave.approve")
    if req.status not in (LeaveStatus.DRAFT, LeaveStatus.PENDING, LeaveStatus.APPROVED):
        raise InvalidState(f"This request is already {req.status.value}")
    tz = company_tz(db)
    conflicts: list[WorkforceConflict] = []
    restored: list[ShiftAssignment] = []
    was_approved = req.status == LeaveStatus.APPROVED
    if was_approved:
        if utcnow().astimezone(tz).date() >= req.start_date:
            raise InvalidState("Leave that has already started cannot be cancelled here; ask HR to adjust it", code="leave_started")
        if not req.leave_type.unlimited:
            b = balance(db, req.employee, req.leave_type, req.start_date.year, lock=True)
            b.used = max(Decimal("0"), b.used - req.days)
        linked = db.execute(select(ShiftAssignment).join(leave_request_shifts, leave_request_shifts.c.assignment_id == ShiftAssignment.id)
                            .where(leave_request_shifts.c.leave_request_id == req.id)).unique().scalars().all()
        managers = users_managing(db, req.employee.department_id, "workforce.overtime.manage", employee=req.employee)
        for a in linked:
            if req.half_day == HalfDay.NONE and a.status != AssignmentStatus.ON_LEAVE:
                continue
            target_positions = [a.position] if req.half_day == HalfDay.NONE else list(db.execute(select(ShiftPosition).where(
                ShiftPosition.shift_id == a.shift_id, ShiftPosition.kind == PositionKind.COVER, ShiftPosition.is_active.is_(True))).scalars())
            for pos in target_positions:
                replacement = occupant(db, pos.id)
                if replacement is not None and replacement.employee_id != req.employee_id:
                    c = WorkforceConflict(kind=ConflictKind.LEAVE_CANCELLED_AFTER_COVER, department_id=a.shift.department_id, shift_id=a.shift_id,
                                          leave_request_id=req.id, original_assignment_id=a.id if req.half_day == HalfDay.NONE else None,
                                          replacement_assignment_id=replacement.id,
                                          summary=f"{req.employee.full_name} cancelled leave {req.number}, but {replacement.employee.full_name} "
                                                  f"already covers {a.shift.name} on {a.shift.shift_date:%d %b}. Decide who works it.")
                    db.add(c)
                    db.flush()
                    conflicts.append(c)
                    notify(db, managers, "workforce_conflict", "Leave cancelled after cover was assigned", c.summary,
                           {"conflict_id": str(c.id), "link": "/workforce/overtime?tab=conflicts"}, dedupe_key=f"conflict:{c.id}")
                    continue
                marketplace.withdraw_position(db, pos, reason=f"Leave {req.number} cancelled")
                if req.half_day == HalfDay.NONE:
                    try:
                        with db.begin_nested():
                            a.status, a.ended_at, a.end_reason, a.ended_by = AssignmentStatus.ASSIGNED, None, None, None
                            a.leave_request_id = None
                            db.flush()
                        restored.append(a)
                    except IntegrityError:
                        db.refresh(a)
                        a.status, a.end_reason = AssignmentStatus.RELEASED, f"Leave {req.number} cancelled; employee now works another shift"
                else:
                    pos.is_active = False
        if restored and req.employee.user_id:
            notify(db, [req.employee.user_id], "shift_changed", "Your shifts are back on your schedule",
                   "After cancelling your leave you are scheduled again for: " +
                   ", ".join(f"{a.shift.name} {a.shift.shift_date:%d %b} {hm(a.start_at, tz)}–{hm(a.end_at, tz)}" for a in restored),
                   {"leave_request_id": str(req.id)})
        notify(db, managers, "leave_cancelled", f"Leave cancelled: {req.employee.full_name}",
               f"{req.employee.full_name} cancelled approved leave {req.number}. {len(restored)} shift(s) restored, "
               f"{len(conflicts)} conflict(s) need a decision.", {"leave_request_id": str(req.id)}, dedupe_key=f"leave_cancelled:{req.id}")
    req.status, req.cancelled_at, req.cancel_reason = LeaveStatus.CANCELLED, utcnow(), reason
    _log(db, req, "cancelled", actor, reason)
    if not own and req.employee.user_id:
        notify(db, [req.employee.user_id], "leave_decided", f"Leave {req.number} was cancelled",
               f"Your leave {req.number} was cancelled by your manager. Reason: {reason}", {"leave_request_id": str(req.id)})
    audit(db, actor, "workforce.leave.cancel", "leave_request", req.id,
          {"reason": reason, "was_approved": was_approved, "restored": len(restored), "conflicts": len(conflicts)})
    return {"request": req, "restored": restored, "conflicts": conflicts}
