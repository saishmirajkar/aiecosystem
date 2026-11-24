"""Shared workforce building blocks: company time, access scope, policy resolution, formatting."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from app.core.deps import Actor
from app.core.errors import NotFound, PermissionDenied
from app.models import (
    Company, Employee, OvertimePolicy, User, UserDepartment, WorkforcePolicy,
)


# ------------------------------------------------------------------ time
def company_tz(db: Session) -> ZoneInfo:
    cached = db.info.get("company_tz")
    if cached is None:
        name = db.execute(select(Company.timezone).limit(1)).scalar_one_or_none() or "UTC"
        cached = db.info["company_tz"] = ZoneInfo(name)
    return cached


def at(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tz)


def day_start(d: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, time.min, tz)


def local_date(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def shift_window(d: date, start: time, end: time, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Absolute window of a shift starting on local date `d`; an end at or before the start is on the next day."""
    s = at(d, start, tz)
    e = at(d, end, tz)
    if e <= s:
        e = at(d + timedelta(days=1), end, tz)
    return s, e


def minutes(a: datetime, b: datetime) -> int:
    return max(0, int((b - a).total_seconds() // 60))


def hours(mins: int | Decimal) -> Decimal:
    return (Decimal(mins) / Decimal(60)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def hm(dt: datetime | None, tz: ZoneInfo) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(tz).strftime("%H:%M")


def friendly_date(d: date) -> str:
    return f"{d.day} {d.strftime('%B')}"


def friendly_time(dt: datetime, tz: ZoneInfo) -> str:
    """6 AM, 2:30 PM — the format used in employee notifications."""
    local = dt.astimezone(tz)
    h = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{h} {suffix}" if local.minute == 0 else f"{h}:{local.minute:02d} {suffix}"


# ------------------------------------------------------------------ scope
@dataclass
class Scope:
    """What the acting user may see. Employees see themselves; managers their departments and direct reports."""
    actor: Actor
    me: Employee | None
    all: bool
    department_ids: frozenset[uuid.UUID]
    report_ids: frozenset[uuid.UUID]

    @property
    def manages_anyone(self) -> bool:
        return self.all or bool(self.department_ids) or bool(self.report_ids)

    def is_self(self, emp: Employee | uuid.UUID) -> bool:
        eid = emp if isinstance(emp, uuid.UUID) else emp.id
        return self.me is not None and self.me.id == eid

    def manages(self, emp: Employee) -> bool:
        return self.all or emp.department_id in self.department_ids or emp.id in self.report_ids

    def manages_department(self, department_id: uuid.UUID | None) -> bool:
        return self.all or (department_id is not None and department_id in self.department_ids)

    def can_see(self, emp: Employee) -> bool:
        return self.is_self(emp) or self.manages(emp)

    def require_see(self, emp: Employee) -> None:
        if not self.can_see(emp):
            raise NotFound("Employee not found")

    def require_manage(self, emp: Employee, permission: str, *, allow_self: bool = False) -> None:
        self.actor.require(permission)
        if self.is_self(emp) and not allow_self:
            raise PermissionDenied("You cannot do this for your own record", code="self_approval_forbidden")
        if not self.manages(emp):
            raise PermissionDenied("This employee is outside your departments")

    def require_department(self, department_id: uuid.UUID, permission: str) -> None:
        self.actor.require(permission)
        if not self.manages_department(department_id):
            raise PermissionDenied("This department is outside your scope")

    def employee_filter(self, stmt: Select, column=Employee.id, dept_column=Employee.department_id) -> Select:
        if self.all:
            return stmt
        conds = []
        if self.department_ids:
            conds.append(dept_column.in_(self.department_ids))
        if self.report_ids:
            conds.append(column.in_(self.report_ids))
        if self.me is not None:
            conds.append(column == self.me.id)
        return stmt.where(or_(*conds)) if conds else stmt.where(column.is_(None))

    def department_filter(self, stmt: Select, dept_column) -> Select:
        if self.all:
            return stmt
        return stmt.where(dept_column.in_(self.department_ids)) if self.department_ids else stmt.where(dept_column.is_(None))


def my_employee(db: Session, actor: Actor) -> Employee | None:
    return db.execute(select(Employee).where(Employee.user_id == actor.user_id)).unique().scalar_one_or_none()


def get_scope(db: Session, actor: Actor) -> Scope:
    key = ("wf_scope", actor.user_id)
    if key in db.info:
        return db.info[key]
    me = my_employee(db, actor)
    managed = actor.has("workforce.view")
    all_depts = managed and actor.has("workforce.all_departments")
    depts: frozenset[uuid.UUID] = frozenset()
    reports: frozenset[uuid.UUID] = frozenset()
    if managed and not all_depts:
        depts = frozenset(db.execute(select(UserDepartment.department_id).where(UserDepartment.user_id == actor.user_id)).scalars())
        if me is not None:
            reports = frozenset(db.execute(select(Employee.id).where(Employee.manager_id == me.id)).scalars())
    scope = Scope(actor, me, all_depts, depts, reports)
    db.info[key] = scope
    return scope


def require_me(scope: Scope) -> Employee:
    if scope.me is None:
        raise PermissionDenied("Your login is not linked to an employee record. Ask HR to link it.", code="no_employee_record")
    return scope.me


def get_employee(db: Session, employee_id: uuid.UUID) -> Employee:
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise NotFound("Employee not found")
    return emp


def users_managing(db: Session, department_id: uuid.UUID, permission: str, *, exclude_user: uuid.UUID | None = None,
                   employee: Employee | None = None) -> list[uuid.UUID]:
    """Active users who hold `permission` and manage the department (or are the employee's reporting manager)."""
    from app.core.deps import build_actor
    out: list[uuid.UUID] = []
    candidates = set(db.execute(select(UserDepartment.user_id).where(UserDepartment.department_id == department_id)).scalars())
    if employee is not None and employee.manager_id:
        mgr = db.get(Employee, employee.manager_id)
        if mgr and mgr.user_id:
            candidates.add(mgr.user_id)
    for uid in candidates:
        if uid == exclude_user:
            continue
        user = db.get(User, uid)
        if user is None or not user.is_active:
            continue
        a = build_actor(db, user)
        if a.has(permission) and a.has("workforce.view"):
            out.append(uid)
    if not out:  # nobody in the department: fall back to users with access to every department
        for user in db.execute(select(User).where(User.is_active.is_(True))).scalars():
            if user.id == exclude_user:
                continue
            a = build_actor(db, user)
            if a.has(permission) and a.has("workforce.all_departments") and not a.is_super_admin:
                out.append(user.id)
    return out


# ------------------------------------------------------------------ policies
def workforce_policy(db: Session, department_id: uuid.UUID | None) -> WorkforcePolicy:
    if department_id is not None:
        row = db.execute(select(WorkforcePolicy).where(WorkforcePolicy.department_id == department_id)).scalar_one_or_none()
        if row is not None:
            return row
    row = db.execute(select(WorkforcePolicy).where(WorkforcePolicy.department_id.is_(None))).scalar_one_or_none()
    if row is None:
        row = WorkforcePolicy(department_id=None)
        db.add(row)
        db.flush()
    return row


def overtime_policy(db: Session, employee: Employee | None = None, policy_id: uuid.UUID | None = None) -> OvertimePolicy | None:
    pid = policy_id or (employee.overtime_policy_id if employee else None)
    if pid:
        row = db.get(OvertimePolicy, pid)
        if row is not None and row.is_active:
            return row
    return db.execute(select(OvertimePolicy).where(OvertimePolicy.is_default.is_(True))).scalar_one_or_none()


def employee_brief(e: Employee | None) -> dict[str, Any] | None:
    if e is None:
        return None
    return {"id": e.id, "employee_code": e.employee_code, "full_name": e.full_name, "department_id": e.department_id,
            "department_name": e.department.name if e.department else None,
            "designation": e.designation.name if e.designation else None}
