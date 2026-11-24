"""Employees, teams (eligibility groups), skills, designations, work locations, holidays and portal access."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.common import audit, diff
from app.core.deps import Actor
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import hash_password, validate_password_strength
from app.models import (
    Department, Designation, Employee, EmployeeSkill, Holiday, OvertimePolicy, Role, ShiftPattern, Skill, Team, User,
    UserDepartment, WorkLocation,
)
from app.modules.workforce import schemas as s
from app.modules.workforce.core import Scope, employee_brief, get_employee

REF_MODELS = {"locations": WorkLocation, "designations": Designation, "skills": Skill}


# ------------------------------------------------------------------ reference data
def ref_out(obj: Any) -> dict[str, Any]:
    out = {"id": obj.id, "code": obj.code, "name": obj.name, "is_active": getattr(obj, "is_active", True)}
    for extra in ("address", "department_id", "kind"):
        if hasattr(obj, extra):
            out[extra] = getattr(obj, extra)
    return out


def create_ref(db: Session, actor: Actor, kind: str, data: s.RefIn) -> Any:
    model = REF_MODELS[kind]
    if db.execute(select(model).where(func.lower(model.code) == data.code.lower())).first():
        raise Conflict(f"Code '{data.code}' already exists")
    obj = model(**data.model_dump())
    db.add(obj)
    db.flush()
    audit(db, actor, f"workforce.{kind}.create", kind, obj.id, data.model_dump())
    return obj


def update_ref(db: Session, actor: Actor, kind: str, obj_id: uuid.UUID, data: s.RefUpdate) -> Any:
    obj = db.get(REF_MODELS[kind], obj_id)
    if obj is None:
        raise NotFound("Record not found")
    changes = diff(obj, data.model_dump(exclude_unset=True))
    audit(db, actor, f"workforce.{kind}.update", kind, obj.id, changes)
    return obj


def team_out(t: Team, db: Session) -> dict[str, Any]:
    members = db.execute(select(Employee).join(Employee.teams).where(Team.id == t.id).order_by(Employee.first_name)).unique().scalars().all()
    return {"id": t.id, "code": t.code, "name": t.name, "department_id": t.department_id, "department_name": t.department.name,
            "description": t.description, "is_active": t.is_active, "member_count": len(members),
            "members": [employee_brief(m) for m in members]}


def _employees(db: Session, ids: list[uuid.UUID]) -> list[Employee]:
    rows = db.execute(select(Employee).where(Employee.id.in_(ids))).unique().scalars().all() if ids else []
    if len(rows) != len(set(ids)):
        raise ValidationFailed("One or more employees were not found")
    return list(rows)


def create_team(db: Session, actor: Actor, data: s.TeamIn) -> Team:
    if db.execute(select(Team).where(func.lower(Team.code) == data.code.lower())).first():
        raise Conflict(f"Team code '{data.code}' already exists")
    if db.get(Department, data.department_id) is None:
        raise ValidationFailed("Department not found")
    team = Team(code=data.code, name=data.name, department_id=data.department_id, description=data.description)
    db.add(team)
    db.flush()
    for e in _employees(db, data.member_ids):
        e.teams.append(team)
    audit(db, actor, "workforce.team.create", "team", team.id, data.model_dump())
    return team


def update_team(db: Session, actor: Actor, team_id: uuid.UUID, data: s.TeamUpdate) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise NotFound("Team not found")
    payload = data.model_dump(exclude_unset=True)
    members = payload.pop("member_ids", None)
    changes = diff(team, payload)
    if members is not None:
        current = db.execute(select(Employee).join(Employee.teams).where(Team.id == team.id)).unique().scalars().all()
        for e in current:
            if e.id not in members:
                e.teams.remove(team)
        for e in _employees(db, members):
            if team not in e.teams:
                e.teams.append(team)
        changes["member_ids"] = members
    audit(db, actor, "workforce.team.update", "team", team.id, changes)
    return team


def create_holiday(db: Session, actor: Actor, data: s.HolidayIn) -> Holiday:
    if db.execute(select(Holiday).where(Holiday.holiday_date == data.holiday_date,
                                        Holiday.work_location_id.is_(data.work_location_id) if data.work_location_id is None
                                        else Holiday.work_location_id == data.work_location_id)).first():
        raise Conflict("A holiday already exists on that date for this location")
    h = Holiday(**data.model_dump())
    db.add(h)
    db.flush()
    audit(db, actor, "workforce.holiday.create", "holiday", h.id, data.model_dump())
    return h


def delete_holiday(db: Session, actor: Actor, holiday_id: uuid.UUID) -> None:
    h = db.get(Holiday, holiday_id)
    if h is None:
        raise NotFound("Holiday not found")
    audit(db, actor, "workforce.holiday.delete", "holiday", h.id, {"date": h.holiday_date, "name": h.name})
    db.delete(h)


# ------------------------------------------------------------------ employees
def employee_out(db: Session, e: Employee, scope: Scope, *, detail: bool = False) -> dict[str, Any]:
    show_rate = scope.actor.has("workforce.payroll")
    out = {
        **employee_brief(e), "first_name": e.first_name, "last_name": e.last_name, "email": e.email, "phone": e.phone,
        "status": e.status.value, "employment_type": e.employment_type.value, "date_of_joining": e.date_of_joining,
        "work_location_id": e.work_location_id, "work_location_name": e.location.name if e.location else None,
        "designation_id": e.designation_id, "manager_id": e.manager_id,
        "manager_name": e.manager.full_name if e.manager_id and e.manager else None,
        "overtime_eligible": e.overtime_eligible, "has_login": e.user_id is not None,
        "teams": [{"id": t.id, "name": t.name} for t in e.teams],
        "skills": [{"skill_id": k.skill_id, "code": k.skill.code, "name": k.skill.name, "level": k.level, "valid_until": k.valid_until}
                   for k in e.skills],
        "photo_attachment_id": e.photo_attachment_id,
    }
    if detail:
        out.update({
            "date_of_leaving": e.date_of_leaving, "shift_pattern_id": e.shift_pattern_id, "pattern_anchor_date": e.pattern_anchor_date,
            "weekly_hour_limit": e.weekly_hour_limit, "overtime_policy_id": e.overtime_policy_id,
            "emergency_contact_name": e.emergency_contact_name, "emergency_contact_phone": e.emergency_contact_phone,
            "address": e.address, "notes": e.notes if scope.manages(e) else None,
            "login_email": e.user.email if e.user_id and e.user and not e.user.email.endswith(".invalid") else None,
            "shift_pattern_name": db.get(ShiftPattern, e.shift_pattern_id).name if e.shift_pattern_id else None,
        })
    if show_rate:
        out["base_hourly_rate"] = e.base_hourly_rate
    return out


def list_employees(db: Session, scope: Scope, *, q: str | None = None, department_id: uuid.UUID | None = None,
                   team_id: uuid.UUID | None = None, status: str | None = None, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    stmt = scope.employee_filter(select(Employee))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(Employee.first_name + " " + func.coalesce(Employee.last_name, "")).like(like),
                              func.lower(Employee.employee_code).like(like)))
    if department_id:
        stmt = stmt.where(Employee.department_id == department_id)
    if team_id:
        stmt = stmt.where(Employee.teams.any(Team.id == team_id))
    if status:
        stmt = stmt.where(Employee.status.in_(status.split(",")))
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Employee.first_name, Employee.employee_code).limit(page_size).offset((page - 1) * page_size)).unique().scalars().all()
    return {"items": [employee_out(db, e, scope) for e in rows], "total": total, "page": page, "page_size": page_size}


def _validate_refs(db: Session, data: dict[str, Any], employee_id: uuid.UUID | None = None) -> None:
    for key, model, label in (("department_id", Department, "Department"), ("designation_id", Designation, "Designation"),
                              ("work_location_id", WorkLocation, "Work location"), ("shift_pattern_id", ShiftPattern, "Shift pattern"),
                              ("overtime_policy_id", OvertimePolicy, "Overtime policy"), ("manager_id", Employee, "Manager")):
        if data.get(key) is not None and db.get(model, data[key]) is None:
            raise ValidationFailed(f"{label} not found", details={"field": key})
    if employee_id and data.get("manager_id") == employee_id:
        raise ValidationFailed("An employee cannot report to themselves", details={"field": "manager_id"})
    if data.get("shift_pattern_id") and not data.get("pattern_anchor_date"):
        raise ValidationFailed("Set the date the pattern starts from (day 1 of the cycle)", details={"field": "pattern_anchor_date"})


def _set_skills_teams(db: Session, e: Employee, skills: list[dict] | None, team_ids: list[uuid.UUID] | None) -> None:
    if skills is not None:
        wanted = {k["skill_id"]: k for k in skills}
        for sid in wanted:
            if db.get(Skill, sid) is None:
                raise ValidationFailed("Skill not found", details={"skill_id": str(sid)})
        e.skills = [EmployeeSkill(skill_id=sid, level=k.get("level", 1), valid_until=k.get("valid_until")) for sid, k in wanted.items()]
    if team_ids is not None:
        teams = db.execute(select(Team).where(Team.id.in_(team_ids))).unique().scalars().all() if team_ids else []
        if len(teams) != len(set(team_ids)):
            raise ValidationFailed("One or more teams were not found")
        e.teams = list(teams)


def create_employee(db: Session, actor: Actor, data: s.EmployeeCreate) -> Employee:
    actor.require("workforce.employees.manage")
    if db.execute(select(Employee).where(func.lower(Employee.employee_code) == data.employee_code.lower())).first():
        raise Conflict(f"Employee code '{data.employee_code}' is already used")
    payload = data.model_dump(exclude={"skills", "team_ids"})
    _validate_refs(db, payload)
    if payload.get("base_hourly_rate") is not None:
        actor.require("workforce.payroll")
    e = Employee(**payload, created_by=actor.user_id, updated_by=actor.user_id)
    db.add(e)
    db.flush()
    _set_skills_teams(db, e, [k.model_dump() for k in data.skills], data.team_ids)
    audit(db, actor, "workforce.employee.create", "employee", e.id,
          {k: v for k, v in data.model_dump().items() if k not in ("base_hourly_rate",)})
    return e


def update_employee(db: Session, actor: Actor, employee_id: uuid.UUID, data: s.EmployeeUpdate) -> Employee:
    actor.require("workforce.employees.manage")
    e = get_employee(db, employee_id)
    payload = data.model_dump(exclude_unset=True)
    skills = payload.pop("skills", None)
    teams = payload.pop("team_ids", None)
    if "base_hourly_rate" in payload:
        actor.require("workforce.payroll")
    merged = {"shift_pattern_id": e.shift_pattern_id, "pattern_anchor_date": e.pattern_anchor_date, **payload}
    _validate_refs(db, merged, e.id)
    changes = diff(e, payload)
    _set_skills_teams(db, e, skills, teams)
    e.updated_by = actor.user_id
    if "base_hourly_rate" in changes:
        changes["base_hourly_rate"] = "changed"  # amounts are kept out of the audit diff
    audit(db, actor, "workforce.employee.update", "employee", e.id, {**changes, **({"skills": "updated"} if skills is not None else {}),
                                                                     **({"team_ids": teams} if teams is not None else {})})
    return e


def grant_portal_access(db: Session, actor: Actor, employee_id: uuid.UUID, data: s.PortalAccessIn) -> Employee:
    """Create a login with the Employee role. Staff without email sign in with their employee code."""
    actor.require("workforce.employees.manage")
    e = get_employee(db, employee_id)
    if e.user_id:
        raise Conflict("This employee already has a login")
    email = e.email or f"{e.employee_code.lower()}@employees.invalid"  # .invalid is reserved and never deliverable
    problem = validate_password_strength(data.password, email)
    if problem:
        raise ValidationFailed(problem, details={"field": "password"})
    if db.execute(select(User).where(func.lower(User.email) == email.lower())).first():
        raise Conflict("A login with this employee's email already exists. Link it from the Users page or change the email.")
    role = db.execute(select(Role).where(Role.code == "employee")).scalar_one_or_none()
    if role is None:
        raise ValidationFailed("The Employee role is missing; run reference-data seeding")
    user = User(email=email, full_name=e.full_name, password_hash=hash_password(data.password), phone=e.phone,
                job_title=e.designation.name if e.designation else None, created_by=actor.user_id)
    user.roles = [role]
    db.add(user)
    db.flush()
    db.add(UserDepartment(user_id=user.id, department_id=e.department_id, is_primary=True))
    e.user_id = user.id
    audit(db, actor, "workforce.employee.portal_access", "employee", e.id, {"user_id": user.id, "sign_in": "email" if e.email else "employee_code"})
    return e


def link_user(db: Session, actor: Actor, employee_id: uuid.UUID, user_id: uuid.UUID) -> Employee:
    """Link an existing login (e.g. a manager created on the Users page) to an employee record."""
    actor.require("workforce.employees.manage")
    e = get_employee(db, employee_id)
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise ValidationFailed("User not found or inactive")
    other = db.execute(select(Employee).where(Employee.user_id == user_id)).unique().scalar_one_or_none()
    if other is not None and other.id != e.id:
        raise Conflict(f"That login is already linked to {other.full_name} ({other.employee_code})")
    if e.user_id and e.user_id != user_id:
        raise Conflict("This employee is already linked to a different login")
    e.user_id = user_id
    audit(db, actor, "workforce.employee.link_user", "employee", e.id, {"user_id": user_id})
    return e
