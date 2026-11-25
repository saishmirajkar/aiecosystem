"""Eligibility rule engine.

One engine answers "may this employee work this window?" for: choosing who to notify, the marketplace's
eligible / not-eligible badge, the claim itself, and manual assignment (where soft failures can be overridden
by a manager with a reason; hard failures never can).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    AssignmentSource, AssignmentStatus, Employee, EmployeeStatus, LeaveRequest, LeaveStatus, Shift, ShiftAssignment,
    WorkforcePolicy,
)
from app.modules.workforce.core import company_tz, day_start, local_date, week_start

HARD_RULES = {"inactive", "on_leave", "shift_conflict", "already_claimed"}
RULE_LABELS = {
    "inactive": "Employee is not active",
    "not_overtime_eligible": "Not eligible for overtime",
    "wrong_group": "Not in the eligible department or team",
    "missing_skill": "Missing a required skill",
    "on_leave": "On approved leave",
    "shift_conflict": "Already scheduled at that time",
    "rest_period": "Not enough rest between shifts",
    "weekly_hours": "Would exceed the weekly hour limit",
    "monthly_cover_cap": "Would exceed the monthly overtime-cover limit",
    "already_claimed": "Already holds a position in this opportunity",
}


@dataclass
class Window:
    start_at: datetime
    end_at: datetime
    break_minutes: int
    shift_date: date
    department_id: uuid.UUID
    team_id: uuid.UUID | None = None
    skill_ids: list[uuid.UUID] = field(default_factory=list)
    opportunity_id: uuid.UUID | None = None

    @property
    def paid_minutes(self) -> int:
        return max(0, int((self.end_at - self.start_at).total_seconds() // 60) - self.break_minutes)


@dataclass
class Failure:
    code: str
    message: str

    @property
    def hard(self) -> bool:
        return self.code in HARD_RULES

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hard": self.hard}


def window_for_shift(shift: Shift, start_at: datetime | None = None, end_at: datetime | None = None,
                     opportunity_id: uuid.UUID | None = None) -> Window:
    partial = start_at is not None and (start_at, end_at) != (shift.start_at, shift.end_at)
    return Window(start_at or shift.start_at, end_at or shift.end_at, 0 if partial else shift.break_minutes, shift.shift_date,
                  shift.department_id, shift.team_id, [k.id for k in shift.skills], opportunity_id)


def _paid(a: ShiftAssignment) -> int:
    mins = int((a.end_at - a.start_at).total_seconds() // 60)
    full = a.start_at == a.shift.start_at and a.end_at == a.shift.end_at
    return max(0, mins - (a.shift.break_minutes if full else 0))


def _fmt_h(mins: int | Decimal) -> str:
    h = Decimal(mins) / 60
    return f"{h.normalize():f}" if h == h.to_integral() else f"{h.quantize(Decimal('0.1')):f}"


def evaluate(db: Session, emp: Employee, w: Window, policy: WorkforcePolicy, *, context: str = "overtime",
             ignore_assignment: uuid.UUID | None = None) -> list[Failure]:
    """Return every failing rule (empty list = eligible). `context` is 'overtime' or 'schedule'."""
    tz = company_tz(db)
    out: list[Failure] = []
    if emp.status != EmployeeStatus.ACTIVE:
        out.append(Failure("inactive", f"{emp.full_name} is {emp.status.value.replace('_', ' ')}"))
    if context == "overtime" and not emp.overtime_eligible:
        out.append(Failure("not_overtime_eligible", "Not marked as eligible for overtime"))
    if context == "overtime":
        if w.team_id is not None:
            if all(t.id != w.team_id for t in emp.teams):
                out.append(Failure("wrong_group", "Not a member of the team this shift is offered to"))
        elif emp.department_id != w.department_id:
            out.append(Failure("wrong_group", "Works in a different department"))
    elif emp.department_id != w.department_id:
        out.append(Failure("wrong_group", "Works in a different department"))

    if w.skill_ids:
        held = {k.skill_id: k for k in emp.skills}
        missing = [sid for sid in w.skill_ids if sid not in held or (held[sid].valid_until and held[sid].valid_until < w.shift_date)]
        if missing:
            from app.models import Skill
            names = [db.get(Skill, sid).name for sid in missing]
            out.append(Failure("missing_skill", "Missing or expired: " + ", ".join(names)))

    end_date = local_date(w.end_at - timedelta(seconds=1), tz)
    leave = db.execute(select(LeaveRequest.number).where(
        LeaveRequest.employee_id == emp.id, LeaveRequest.status == LeaveStatus.APPROVED,
        LeaveRequest.start_date <= end_date, LeaveRequest.end_date >= w.shift_date)).first()
    if leave:
        out.append(Failure("on_leave", "On approved leave on this date"))

    A = ShiftAssignment
    active = and_(A.employee_id == emp.id, A.status == AssignmentStatus.ASSIGNED)
    if ignore_assignment:
        active = and_(active, A.id != ignore_assignment)
    clash = db.execute(select(A).where(active, A.start_at < w.end_at, A.end_at > w.start_at)).unique().scalars().first()
    if clash:
        out.append(Failure("shift_conflict", f"Already working {clash.shift.name} on {clash.shift.shift_date:%d %b} "
                                             f"({clash.start_at.astimezone(tz):%H:%M}–{clash.end_at.astimezone(tz):%H:%M})"))
    if w.opportunity_id is not None:
        if db.execute(select(A.id).where(A.employee_id == emp.id, A.opportunity_id == w.opportunity_id,
                                         A.status == AssignmentStatus.ASSIGNED)).first():
            out.append(Failure("already_claimed", "You already hold a position in this opportunity"))

    rest = Decimal(policy.min_rest_hours or 0)
    if rest > 0 and not clash:
        gap = timedelta(hours=float(rest))
        prev = db.execute(select(A).where(active, A.end_at <= w.start_at, A.end_at > w.start_at - gap)).unique().scalars().first()
        nxt = db.execute(select(A).where(active, A.start_at >= w.end_at, A.start_at < w.end_at + gap)).unique().scalars().first()
        near = prev or nxt
        if near:
            have = (w.start_at - near.end_at) if prev else (near.start_at - w.end_at)
            out.append(Failure("rest_period", f"Only {_fmt_h(int(have.total_seconds() // 60))} h rest next to "
                                              f"{near.shift.name} on {near.shift.shift_date:%d %b}; minimum is {_fmt_h(rest * 60)} h"))

    cap = emp.weekly_hour_limit if emp.weekly_hour_limit is not None else policy.weekly_hour_cap
    if cap is not None:
        ws = week_start(w.shift_date)
        lo, hi = day_start(ws, tz), day_start(ws + timedelta(days=7), tz)
        week = db.execute(select(A).where(active, A.start_at >= lo, A.start_at < hi)).unique().scalars().all()
        total = sum(_paid(a) for a in week) + w.paid_minutes
        if total > Decimal(cap) * 60:
            out.append(Failure("weekly_hours", f"Would work {_fmt_h(total)} h this week; the limit is {_fmt_h(Decimal(cap) * 60)} h"))

    mcap = policy.monthly_cover_hours_cap
    if context == "overtime" and mcap is not None:
        ms = w.shift_date.replace(day=1)
        me = (ms + timedelta(days=32)).replace(day=1)
        cover = db.execute(select(A).where(active, A.source.in_([AssignmentSource.MARKETPLACE, AssignmentSource.MANUAL]),
                                           A.start_at >= day_start(ms, tz), A.start_at < day_start(me, tz))).unique().scalars().all()
        total = sum(_paid(a) for a in cover) + w.paid_minutes
        if total > Decimal(mcap) * 60:
            out.append(Failure("monthly_cover_cap", f"Would reach {_fmt_h(total)} h of covered shifts this month; "
                                                    f"the limit is {_fmt_h(Decimal(mcap) * 60)} h"))
    return out


def scheduled_minutes_in_week(db: Session, emp: Employee, d: date) -> int:
    tz = company_tz(db)
    ws = week_start(d)
    A = ShiftAssignment
    rows = db.execute(select(A).where(A.employee_id == emp.id, A.status == AssignmentStatus.ASSIGNED,
                                      A.start_at >= day_start(ws, tz), A.start_at < day_start(ws + timedelta(days=7), tz))).unique().scalars().all()
    return sum(_paid(a) for a in rows)

