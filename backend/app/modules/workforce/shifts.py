"""Shift templates, rotation patterns, dated shifts, positions and assignments.

A shift has one position per required person. An assignment occupies a position; the database allows only
one active assignment per position and never two overlapping active assignments for one employee.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.common import audit, diff
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import BusinessRuleViolation, Conflict, InvalidState, NotFound, ValidationFailed
from app.models import (
    AssignmentSource, AssignmentStatus, Department, Employee, LeaveRequest, LeaveStatus, OpportunityPosition, OpportunityStatus,
    OvertimeOpportunity, PositionKind, Shift, ShiftAssignment, ShiftPattern, ShiftPatternDay, ShiftPosition, ShiftStatus,
    ShiftTemplate, Skill, Team,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import (
    Scope, company_tz, day_start, employee_brief, get_employee, hm, shift_window, workforce_policy,
)
from app.modules.workforce.eligibility import evaluate, window_for_shift
from app.modules.workforce.notify import notify

ACTIVE_OPP = (OpportunityStatus.DRAFT, OpportunityStatus.PUBLISHED, OpportunityStatus.OPEN, OpportunityStatus.PARTIALLY_FILLED)


# ------------------------------------------------------------------ templates & patterns
def _skills(db: Session, ids: list[uuid.UUID]) -> list[Skill]:
    rows = db.execute(select(Skill).where(Skill.id.in_(ids))).scalars().all() if ids else []
    if len(rows) != len(set(ids)):
        raise ValidationFailed("One or more skills were not found")
    return list(rows)


def template_out(t: ShiftTemplate) -> dict[str, Any]:
    return {"id": t.id, "code": t.code, "name": t.name, "start_time": t.start_time.strftime("%H:%M"),
            "end_time": t.end_time.strftime("%H:%M"), "overnight": t.end_time <= t.start_time, "break_minutes": t.break_minutes,
            "department_id": t.department_id, "department_name": t.department.name, "work_location_id": t.work_location_id,
            "work_location_name": t.location.name if t.location else None, "team_id": t.team_id,
            "team_name": t.team.name if t.team else None, "required_count": t.required_count, "supervisor_id": t.supervisor_id,
            "overtime_policy_id": t.overtime_policy_id, "color": t.color, "is_active": t.is_active,
            "skills": [{"id": k.id, "name": k.name} for k in t.skills]}


def create_template(db: Session, actor: Actor, data: s.TemplateIn) -> ShiftTemplate:
    if db.execute(select(ShiftTemplate).where(func.lower(ShiftTemplate.code) == data.code.lower())).first():
        raise Conflict(f"Template code '{data.code}' already exists")
    if db.get(Department, data.department_id) is None:
        raise ValidationFailed("Department not found")
    t = ShiftTemplate(**data.model_dump(exclude={"skill_ids"}), created_by=actor.user_id)
    t.skills = _skills(db, data.skill_ids)
    db.add(t)
    db.flush()
    audit(db, actor, "workforce.template.create", "shift_template", t.id, data.model_dump())
    return t


def update_template(db: Session, actor: Actor, template_id: uuid.UUID, data: s.TemplateUpdate) -> ShiftTemplate:
    t = db.get(ShiftTemplate, template_id)
    if t is None:
        raise NotFound("Shift template not found")
    payload = data.model_dump(exclude_unset=True)
    skill_ids = payload.pop("skill_ids", None)
    changes = diff(t, payload)
    if t.start_time == t.end_time:
        raise ValidationFailed("Start and end time must differ")
    if skill_ids is not None:
        t.skills = _skills(db, skill_ids)
        changes["skill_ids"] = skill_ids
    audit(db, actor, "workforce.template.update", "shift_template", t.id, changes)
    return t  # existing dated shifts keep their own copy; new shifts use the new values


def pattern_out(p: ShiftPattern) -> dict[str, Any]:
    by_day = {d.day_index: d for d in p.days}
    return {"id": p.id, "code": p.code, "name": p.name, "cycle_days": p.cycle_days, "is_active": p.is_active,
            "days": [{"day_index": i, "template_id": by_day[i].template_id if i in by_day else None,
                      "template_name": by_day[i].template.name if i in by_day and by_day[i].template else None}
                     for i in range(p.cycle_days)]}


def save_pattern(db: Session, actor: Actor, data: s.PatternIn, pattern_id: uuid.UUID | None = None) -> ShiftPattern:
    for tid in {t for t in data.days if t}:
        if db.get(ShiftTemplate, tid) is None:
            raise ValidationFailed("Shift template not found", details={"template_id": str(tid)})
    if pattern_id:
        p = db.get(ShiftPattern, pattern_id)
        if p is None:
            raise NotFound("Shift pattern not found")
        p.name, p.cycle_days = data.name, data.cycle_days
    else:
        if db.execute(select(ShiftPattern).where(func.lower(ShiftPattern.code) == data.code.lower())).first():
            raise Conflict(f"Pattern code '{data.code}' already exists")
        p = ShiftPattern(code=data.code, name=data.name, cycle_days=data.cycle_days, created_by=actor.user_id)
        db.add(p)
    p.days = [ShiftPatternDay(day_index=i, template_id=t) for i, t in enumerate(data.days) if t]
    db.flush()
    audit(db, actor, "workforce.pattern.save", "shift_pattern", p.id, data.model_dump())
    return p


def pattern_template_on(emp: Employee, d: date, pattern: ShiftPattern) -> ShiftTemplate | None | bool:
    """Template the employee's pattern puts on date `d`; None = weekly off; False = no pattern applies."""
    if not emp.pattern_anchor_date or d < emp.pattern_anchor_date:
        return False
    idx = (d - emp.pattern_anchor_date).days % pattern.cycle_days
    day = next((x for x in pattern.days if x.day_index == idx), None)
    return day.template if day else None


# ------------------------------------------------------------------ shifts
def _sync_positions(db: Session, shift: Shift, required: int) -> None:
    regular = [p for p in shift.positions if p.kind == PositionKind.REGULAR and p.is_active]
    if required > len(regular):
        next_slot = max([p.slot for p in shift.positions], default=0)
        for i in range(required - len(regular)):
            db.add(ShiftPosition(shift_id=shift.id, slot=next_slot + i + 1, kind=PositionKind.REGULAR))
    elif required < len(regular):
        removable = [p for p in sorted(regular, key=lambda p: -p.slot) if occupant(db, p.id) is None
                     and not db.execute(select(OpportunityPosition.position_id)
                                        .join(OvertimeOpportunity, OvertimeOpportunity.id == OpportunityPosition.opportunity_id)
                                        .where(OpportunityPosition.position_id == p.id, OpportunityPosition.is_active.is_(True),
                                               OvertimeOpportunity.status.in_(ACTIVE_OPP))).first()]
        need = len(regular) - required
        if len(removable) < need:
            raise BusinessRuleViolation("Release assignments (or cancel open overtime) before reducing the required count")
        for p in removable[:need]:
            p.is_active = False
    shift.required_count = required
    db.flush()
    db.refresh(shift)


def occupant(db: Session, position_id: uuid.UUID) -> ShiftAssignment | None:
    return db.execute(select(ShiftAssignment).where(ShiftAssignment.position_id == position_id,
                                                    ShiftAssignment.status == AssignmentStatus.ASSIGNED)).unique().scalar_one_or_none()


def create_shift(db: Session, actor: Actor, scope: Scope, data: s.ShiftCreate) -> Shift:
    tz = company_tz(db)
    t = db.get(ShiftTemplate, data.template_id) if data.template_id else None
    if data.template_id and t is None:
        raise ValidationFailed("Shift template not found")
    start = data.start_time or (t.start_time if t else None)
    end = data.end_time or (t.end_time if t else None)
    dept = data.department_id or (t.department_id if t else None)
    if start is None or end is None or dept is None:
        raise ValidationFailed("Choose a template, or give start time, end time and department")
    if start == end:
        raise ValidationFailed("Start and end time must differ")
    scope.require_department(dept, "workforce.schedule")
    if t and db.execute(select(Shift.id).where(Shift.template_id == t.id, Shift.shift_date == data.shift_date,
                                               Shift.status == ShiftStatus.SCHEDULED)).first():
        raise Conflict(f"{t.name} on {data.shift_date:%d %b %Y} already exists")
    sa, ea = shift_window(data.shift_date, start, end, tz)
    shift = Shift(template_id=t.id if t else None, name=data.name or (t.name if t else "Shift"), shift_date=data.shift_date,
                  start_at=sa, end_at=ea, department_id=dept,
                  break_minutes=data.break_minutes if data.break_minutes is not None else (t.break_minutes if t else 30),
                  work_location_id=data.work_location_id or (t.work_location_id if t else None),
                  team_id=data.team_id or (t.team_id if t else None),
                  supervisor_id=data.supervisor_id or (t.supervisor_id if t else None),
                  overtime_policy_id=t.overtime_policy_id if t else None, notes=data.notes, created_by=actor.user_id,
                  required_count=0)
    shift.skills = _skills(db, data.skill_ids) if data.skill_ids is not None else (list(t.skills) if t else [])
    db.add(shift)
    db.flush()
    _sync_positions(db, shift, data.required_count if data.required_count is not None else (t.required_count if t else 1))
    audit(db, actor, "workforce.shift.create", "shift", shift.id, {"name": shift.name, "date": shift.shift_date,
                                                                   "required": shift.required_count})
    return shift


def get_shift(db: Session, shift_id: uuid.UUID) -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise NotFound("Shift not found")
    return shift


def update_shift(db: Session, actor: Actor, scope: Scope, shift_id: uuid.UUID, data: s.ShiftUpdate) -> Shift:
    shift = get_shift(db, shift_id)
    scope.require_department(shift.department_id, "workforce.schedule")
    if shift.status == ShiftStatus.CANCELLED:
        raise InvalidState("This shift is cancelled")
    payload = data.model_dump(exclude_unset=True)
    required = payload.pop("required_count", None)
    st, et = payload.pop("start_time", None), payload.pop("end_time", None)
    changes = diff(shift, payload)
    if st or et:
        if shift.start_at <= utcnow():
            raise InvalidState("A shift that has started cannot be re-timed")
        tz = company_tz(db)
        sa, ea = shift_window(shift.shift_date, st or shift.start_at.astimezone(tz).time(), et or shift.end_at.astimezone(tz).time(), tz)
        old = (shift.start_at, shift.end_at)
        shift.start_at, shift.end_at = sa, ea
        for a in db.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == shift.id,
                                                          ShiftAssignment.status == AssignmentStatus.ASSIGNED)).unique().scalars():
            if (a.start_at, a.end_at) == old:
                a.start_at, a.end_at = sa, ea
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError:
            raise Conflict("The new times overlap another shift of an assigned employee") from None
        changes["window"] = {"from": [old[0], old[1]], "to": [sa, ea]}
    if required is not None and required != shift.required_count:
        changes["required_count"] = {"from": shift.required_count, "to": required}
        _sync_positions(db, shift, required)
    audit(db, actor, "workforce.shift.update", "shift", shift.id, changes)
    return shift


def cancel_shift(db: Session, actor: Actor, scope: Scope, shift_id: uuid.UUID, reason: str) -> Shift:
    shift = get_shift(db, shift_id)
    scope.require_department(shift.department_id, "workforce.schedule")
    if shift.status == ShiftStatus.CANCELLED:
        raise InvalidState("This shift is already cancelled")
    if shift.start_at <= utcnow():
        raise InvalidState("A shift that has started cannot be cancelled")
    tz = company_tz(db)
    shift.status = ShiftStatus.CANCELLED
    affected = []
    for a in db.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == shift.id,
                                                      ShiftAssignment.status == AssignmentStatus.ASSIGNED)).unique().scalars():
        a.status, a.ended_at, a.end_reason, a.ended_by = AssignmentStatus.CANCELLED, utcnow(), reason, actor.user_id
        affected.append(a)
    for o in db.execute(select(OvertimeOpportunity).where(OvertimeOpportunity.shift_id == shift.id,
                                                          OvertimeOpportunity.status.in_(ACTIVE_OPP))).unique().scalars():
        o.status, o.cancelled_at, o.cancel_reason = OpportunityStatus.CANCELLED, utcnow(), "Shift cancelled"
    users = [a.employee.user_id for a in affected if a.employee.user_id]
    notify(db, users, "shift_changed", f"Shift cancelled: {shift.name} on {shift.shift_date:%d %b}",
           f"Your {shift.name} shift on {shift.shift_date:%d %b} ({hm(shift.start_at, tz)}–{hm(shift.end_at, tz)}) was cancelled. "
           f"Reason: {reason}", {"shift_id": str(shift.id)}, dedupe_key=f"shiftcancel:{shift.id}")
    audit(db, actor, "workforce.shift.cancel", "shift", shift.id, {"reason": reason, "assignments": len(affected)})
    return shift


# ------------------------------------------------------------------ assignments
def free_position(db: Session, shift: Shift, position_id: uuid.UUID | None = None) -> ShiftPosition:
    positions = [p for p in shift.positions if p.is_active and p.kind == PositionKind.REGULAR]
    if position_id:
        positions = [p for p in shift.positions if p.id == position_id and p.is_active]
        if not positions:
            raise NotFound("Position not found on this shift")
    for p in positions:
        if occupant(db, p.id) is None:
            return p
    raise BusinessRuleViolation("Every position on this shift is filled. Increase the required count to add someone.",
                                code="shift_full")


def insert_assignment(db: Session, *, position: ShiftPosition, shift: Shift, employee: Employee, source: AssignmentSource,
                      start_at: datetime | None = None, end_at: datetime | None = None, actor: Actor | None = None,
                      opportunity_id: uuid.UUID | None = None, reason: str | None = None,
                      overrides: list[str] | None = None) -> ShiftAssignment:
    """Insert inside a savepoint so the database guards (one per position, no overlap) report cleanly."""
    a = ShiftAssignment(position_id=position.id, shift_id=shift.id, employee_id=employee.id,
                        start_at=start_at or position.window_start or shift.start_at, end_at=end_at or position.window_end or shift.end_at,
                        source=source, status=AssignmentStatus.ASSIGNED, opportunity_id=opportunity_id, reason=reason,
                        rule_overrides=overrides or None, assigned_by=actor.user_id if actor else None)
    try:
        with db.begin_nested():
            db.add(a)
            db.flush()
    except IntegrityError as exc:
        msg = str(exc.orig)
        if "ex_assignments_no_overlap" in msg:
            raise Conflict(f"{employee.full_name} is already scheduled at that time", code="shift_conflict") from None
        if "uq_assignments_active_position" in msg:
            raise Conflict("That position was filled a moment ago", code="position_taken") from None
        raise
    return a


def assign(db: Session, actor: Actor, scope: Scope, shift_id: uuid.UUID, data: s.AssignIn) -> ShiftAssignment:
    shift = get_shift(db, shift_id)
    scope.require_department(shift.department_id, "workforce.schedule")
    if shift.status == ShiftStatus.CANCELLED:
        raise InvalidState("This shift is cancelled")
    emp = get_employee(db, data.employee_id)
    if not scope.manages(emp) and not scope.manages_department(shift.department_id):
        raise NotFound("Employee not found")
    failures = evaluate(db, emp, window_for_shift(shift), workforce_policy(db, shift.department_id), context="schedule")
    hard = [f for f in failures if f.hard]
    if hard:
        raise BusinessRuleViolation(hard[0].message, code=hard[0].code, details={"failures": [f.as_dict() for f in failures]})
    soft = [f for f in failures if f.code not in data.override_rules]
    if soft:
        raise BusinessRuleViolation("Assignment breaks scheduling rules; confirm with an override and a reason",
                                    code="rules_failed", details={"failures": [f.as_dict() for f in failures]})
    if failures and not data.reason:
        raise ValidationFailed("Give a reason for overriding the rules", details={"field": "reason"})
    position = free_position(db, shift, data.position_id)
    a = insert_assignment(db, position=position, shift=shift, employee=emp, source=AssignmentSource.SCHEDULE, actor=actor,
                          reason=data.reason, overrides=[f.code for f in failures])
    audit(db, actor, "workforce.assignment.create", "shift_assignment", a.id,
          {"shift_id": shift.id, "employee_id": emp.id, "overrides": [f.code for f in failures], "reason": data.reason})
    return a


def release(db: Session, actor: Actor, scope: Scope, assignment_id: uuid.UUID, data: s.ReleaseIn) -> ShiftAssignment:
    a = db.get(ShiftAssignment, assignment_id)
    if a is None:
        raise NotFound("Assignment not found")
    scope.require_department(a.shift.department_id, "workforce.schedule")
    if a.status != AssignmentStatus.ASSIGNED:
        raise InvalidState("Only an active assignment can be released")
    if a.start_at <= utcnow():
        raise InvalidState("This shift has started; record attendance instead")
    a.status, a.ended_at, a.end_reason, a.ended_by = AssignmentStatus.RELEASED, utcnow(), data.reason, actor.user_id
    db.flush()
    from app.modules.workforce import marketplace
    tz = company_tz(db)
    if a.employee.user_id:
        notify(db, [a.employee.user_id], "shift_changed", f"Removed from {a.shift.name} on {a.shift.shift_date:%d %b}",
               f"You are no longer scheduled for {a.shift.name} on {a.shift.shift_date:%d %b} "
               f"({hm(a.start_at, tz)}–{hm(a.end_at, tz)}). Reason: {data.reason}", {"shift_id": str(a.shift_id)})
    if data.create_opportunity:
        marketplace.create_for_positions(db, actor, a.shift, [a.position], source="manual")
    audit(db, actor, "workforce.assignment.release", "shift_assignment", a.id, {"reason": data.reason})
    return a


# ------------------------------------------------------------------ generation
def generate(db: Session, actor: Actor, scope: Scope, data: s.GenerateIn) -> dict[str, Any]:
    """Create shifts and assignments from employees' rotation patterns. Never double-books; reports what it skipped."""
    actor.require("workforce.schedule")
    stmt = scope.employee_filter(select(Employee).where(Employee.shift_pattern_id.is_not(None), Employee.status == "active"))
    if data.department_id:
        stmt = stmt.where(Employee.department_id == data.department_id)
    employees = db.execute(stmt).unique().scalars().all()
    created_shifts, created_assignments, skipped = 0, 0, []
    cache: dict[tuple[uuid.UUID, date], Shift] = {}
    d = data.date_from
    while d <= data.date_to:
        for emp in employees:
            pattern = db.get(ShiftPattern, emp.shift_pattern_id)
            if pattern is None or not pattern.is_active:
                continue
            t = pattern_template_on(emp, d, pattern)
            if not t:
                continue
            if not scope.manages_department(t.department_id):
                continue
            key = (t.id, d)
            shift = cache.get(key) or db.execute(select(Shift).where(Shift.template_id == t.id, Shift.shift_date == d,
                                                                     Shift.status == ShiftStatus.SCHEDULED)).unique().scalar_one_or_none()
            if shift is None:
                shift = create_shift(db, actor, scope, s.ShiftCreate(template_id=t.id, shift_date=d))
                created_shifts += 1
            cache[key] = shift
            if db.execute(select(ShiftAssignment.id).where(ShiftAssignment.shift_id == shift.id, ShiftAssignment.employee_id == emp.id,
                                                           ShiftAssignment.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE]))).first():
                continue
            failures = [f for f in evaluate(db, emp, window_for_shift(shift), workforce_policy(db, shift.department_id),
                                            context="schedule") if f.code in ("inactive", "on_leave", "shift_conflict")]
            if failures:
                skipped.append({"employee": emp.full_name, "date": d, "shift": shift.name, "reason": failures[0].message})
                continue
            try:
                pos = free_position(db, shift)
                insert_assignment(db, position=pos, shift=shift, employee=emp, source=AssignmentSource.SCHEDULE, actor=actor)
                created_assignments += 1
            except (BusinessRuleViolation, Conflict) as exc:
                skipped.append({"employee": emp.full_name, "date": d, "shift": shift.name, "reason": exc.message})
        d += timedelta(days=1)
    audit(db, actor, "workforce.schedule.generate", "shift", None,
          {"from": data.date_from, "to": data.date_to, "shifts": created_shifts, "assignments": created_assignments, "skipped": len(skipped)})
    return {"shifts_created": created_shifts, "assignments_created": created_assignments, "skipped": skipped}


def copy_week(db: Session, actor: Actor, scope: Scope, data: s.CopyWeekIn) -> dict[str, Any]:
    scope.require_department(data.department_id, "workforce.schedule")
    if data.source_week_start.weekday() != 0 or data.target_week_start.weekday() != 0:
        raise ValidationFailed("Weeks start on Monday")
    if data.source_week_start == data.target_week_start:
        raise ValidationFailed("Choose a different target week")
    offset = data.target_week_start - data.source_week_start
    tz = company_tz(db)
    src = db.execute(select(Shift).where(Shift.department_id == data.department_id, Shift.status == ShiftStatus.SCHEDULED,
                                         Shift.shift_date >= data.source_week_start,
                                         Shift.shift_date < data.source_week_start + timedelta(days=7))
                     .order_by(Shift.start_at)).unique().scalars().all()
    shifts_created, assigned, skipped = 0, 0, []
    for sh in src:
        target_date = sh.shift_date + offset
        existing = None
        if sh.template_id:
            existing = db.execute(select(Shift).where(Shift.template_id == sh.template_id, Shift.shift_date == target_date,
                                                      Shift.status == ShiftStatus.SCHEDULED)).unique().scalar_one_or_none()
        target = existing or create_shift(db, actor, scope, s.ShiftCreate(
            template_id=sh.template_id, shift_date=target_date, name=sh.name, start_time=sh.start_at.astimezone(tz).time(),
            end_time=sh.end_at.astimezone(tz).time(), break_minutes=sh.break_minutes, department_id=sh.department_id,
            work_location_id=sh.work_location_id, team_id=sh.team_id, required_count=sh.required_count,
            supervisor_id=sh.supervisor_id, skill_ids=[k.id for k in sh.skills], notes=sh.notes))
        shifts_created += 0 if existing else 1
        if not data.include_assignments:
            continue
        sources = db.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == sh.id, ShiftAssignment.source == AssignmentSource.SCHEDULE,
                                                           ShiftAssignment.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE]))).unique().scalars().all()
        for a in sources:
            emp = a.employee
            if db.execute(select(ShiftAssignment.id).where(ShiftAssignment.shift_id == target.id, ShiftAssignment.employee_id == emp.id,
                                                           ShiftAssignment.status == AssignmentStatus.ASSIGNED)).first():
                continue
            hard = [f for f in evaluate(db, emp, window_for_shift(target), workforce_policy(db, target.department_id), context="schedule")
                    if f.code in ("inactive", "on_leave", "shift_conflict")]
            if hard:
                skipped.append({"employee": emp.full_name, "date": target_date, "shift": target.name, "reason": hard[0].message})
                continue
            try:
                insert_assignment(db, position=free_position(db, target), shift=target, employee=emp, source=AssignmentSource.SCHEDULE, actor=actor)
                assigned += 1
            except (BusinessRuleViolation, Conflict) as exc:
                skipped.append({"employee": emp.full_name, "date": target_date, "shift": target.name, "reason": exc.message})
    audit(db, actor, "workforce.schedule.copy_week", "shift", None,
          {"source": data.source_week_start, "target": data.target_week_start, "shifts": shifts_created, "assignments": assigned})
    return {"shifts_created": shifts_created, "assignments_created": assigned, "skipped": skipped}


# ------------------------------------------------------------------ views
def assignment_out(db: Session, a: ShiftAssignment) -> dict[str, Any]:
    tz = company_tz(db)
    return {"id": a.id, "shift_id": a.shift_id, "position_id": a.position_id, "employee": employee_brief(a.employee),
            "start_at": a.start_at, "end_at": a.end_at, "start": hm(a.start_at, tz), "end": hm(a.end_at, tz),
            "source": a.source.value, "status": a.status.value, "opportunity_id": a.opportunity_id,
            "leave_request_id": a.leave_request_id, "reason": a.reason, "rule_overrides": a.rule_overrides or [],
            "created_at": a.created_at, "end_reason": a.end_reason, "shift_name": a.shift.name, "shift_date": a.shift.shift_date,
            "department_name": a.shift.department.name}


def shift_out(db: Session, shift: Shift, scope: Scope | None = None) -> dict[str, Any]:
    tz = company_tz(db)
    rows = db.execute(select(ShiftAssignment).where(ShiftAssignment.shift_id == shift.id)
                      .order_by(ShiftAssignment.created_at)).unique().scalars().all()
    opps = db.execute(select(OvertimeOpportunity).where(OvertimeOpportunity.shift_id == shift.id)
                      .order_by(OvertimeOpportunity.created_at)).unique().scalars().all()
    positions = []
    for p in shift.positions:
        if not p.is_active:
            continue
        hist = [a for a in rows if a.position_id == p.id]
        current = next((a for a in hist if a.status == AssignmentStatus.ASSIGNED), None)
        on_leave = [a for a in hist if a.status == AssignmentStatus.ON_LEAVE]
        opp = next((o for o in opps if o.status in ACTIVE_OPP and any(op.position_id == p.id and op.is_active for op in o.positions)), None)
        positions.append({
            "id": p.id, "slot": p.slot, "kind": p.kind.value, "window_start": p.window_start, "window_end": p.window_end,
            "assignment": assignment_out(db, current) if current else None,
            "on_leave": [{"employee": employee_brief(a.employee), "leave_request_id": a.leave_request_id} for a in on_leave],
            "vacant": current is None,
            "opportunity": {"id": opp.id, "number": opp.number, "status": opp.status.value} if opp else None,
        })
    filled = sum(1 for p in positions if not p["vacant"])
    return {"id": shift.id, "template_id": shift.template_id, "name": shift.name, "shift_date": shift.shift_date,
            "start_at": shift.start_at, "end_at": shift.end_at, "start": hm(shift.start_at, tz), "end": hm(shift.end_at, tz),
            "overnight": shift.end_at.astimezone(tz).date() != shift.start_at.astimezone(tz).date(),
            "break_minutes": shift.break_minutes, "department_id": shift.department_id, "department_name": shift.department.name,
            "work_location_id": shift.work_location_id, "work_location_name": shift.location.name if shift.location else None,
            "team_id": shift.team_id, "team_name": shift.team.name if shift.team else None, "required_count": shift.required_count,
            "supervisor_id": shift.supervisor_id, "status": shift.status.value, "notes": shift.notes,
            "skills": [{"id": k.id, "name": k.name} for k in shift.skills],
            "color": shift.template.color if shift.template else None,
            "filled": filled, "vacant": len(positions) - filled, "positions": positions}


def calendar(db: Session, scope: Scope, date_from: date, date_to: date, department_id: uuid.UUID | None = None,
             mine: bool = False) -> dict[str, Any]:
    if (date_to - date_from).days > 62:
        raise ValidationFailed("Choose a range of at most 62 days")
    stmt = select(Shift).where(Shift.shift_date >= date_from, Shift.shift_date <= date_to, Shift.status == ShiftStatus.SCHEDULED)
    if mine or not scope.manages_anyone:
        if scope.me is None:
            return {"shifts": [], "leave": []}
        stmt = stmt.where(Shift.id.in_(select(ShiftAssignment.shift_id).where(ShiftAssignment.employee_id == scope.me.id,
                                                                              ShiftAssignment.status.in_([AssignmentStatus.ASSIGNED, AssignmentStatus.ON_LEAVE]))))
    else:
        stmt = scope.department_filter(stmt, Shift.department_id)
    if department_id:
        stmt = stmt.where(Shift.department_id == department_id)
    shifts = db.execute(stmt.order_by(Shift.start_at)).unique().scalars().all()
    out = [shift_out(db, sh) for sh in shifts]
    if mine or not scope.manages_anyone:  # employees only see their own position, not colleagues'
        for sh in out:
            sh["positions"] = [p for p in sh["positions"] if (p["assignment"] and scope.me and p["assignment"]["employee"]["id"] == scope.me.id)
                               or any(x["employee"]["id"] == scope.me.id for x in p["on_leave"])]
    leave_stmt = scope.employee_filter(select(LeaveRequest).join(Employee, Employee.id == LeaveRequest.employee_id).where(
        LeaveRequest.status == LeaveStatus.APPROVED, LeaveRequest.start_date <= date_to, LeaveRequest.end_date >= date_from))
    if mine:
        leave_stmt = leave_stmt.where(LeaveRequest.employee_id == (scope.me.id if scope.me else None))
    if department_id:
        leave_stmt = leave_stmt.where(Employee.department_id == department_id)
    leave = [{"id": r.id, "employee": employee_brief(r.employee), "start_date": r.start_date, "end_date": r.end_date,
              "half_day": r.half_day.value} for r in db.execute(leave_stmt).unique().scalars()]
    return {"shifts": out, "leave": leave}


def list_shifts(db: Session, scope: Scope, date_from: date, date_to: date, department_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    return calendar(db, scope, date_from, date_to, department_id)["shifts"]


def upcoming_for(db: Session, emp: Employee, days: int = 14) -> list[ShiftAssignment]:
    tz = company_tz(db)
    now = utcnow()
    return list(db.execute(select(ShiftAssignment).where(
        ShiftAssignment.employee_id == emp.id, ShiftAssignment.status == AssignmentStatus.ASSIGNED,
        ShiftAssignment.end_at > now, ShiftAssignment.start_at < day_start(now.astimezone(tz).date() + timedelta(days=days), tz))
        .order_by(ShiftAssignment.start_at)).unique().scalars())


def team_brief(t: Team | None) -> dict[str, Any] | None:
    return {"id": t.id, "name": t.name} if t else None
