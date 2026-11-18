"""ERP core business logic: setup, authentication, users, roles, departments, company, settings."""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from app.core.common import DEFAULT_SETTINGS, audit, diff
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.deps import Actor, build_actor
from app.core.errors import (
    AccountLocked, BusinessRuleViolation, Conflict, InvalidCredentials, NotFound, PermissionDenied, ValidationFailed,
)
from app.core.permissions import ALL_CODES
from app.core.security import (
    DUMMY_HASH, hash_password, hash_token, needs_rehash, new_token, validate_password_strength, verify_password,
)
from app.models import (
    Company, Department, PasswordResetToken, Permission, Role, SystemSetting, User, UserDepartment, UserSession,
    Warehouse, user_roles, user_warehouse_access,
)
from app.modules.erp_core import schemas as s
from app.modules.erp_core.seed import seed_reference_data

log = logging.getLogger("above.auth")


# ------------------------------------------------------------------ helpers
def _check_password(password: str, email: str) -> None:
    problem = validate_password_strength(password, email)
    if problem:
        raise ValidationFailed(problem, details={"field": "password"})


def get_company(db: Session) -> Company | None:
    return db.execute(select(Company).limit(1)).scalar_one_or_none()


def user_out(db: Session, user: User) -> dict[str, Any]:
    wh = db.execute(select(user_warehouse_access.c.warehouse_id).where(user_warehouse_access.c.user_id == user.id)).scalars().all()
    primary = next((d.department_id for d in user.departments if d.is_primary), None)
    return {
        **{k: getattr(user, k) for k in ("id", "email", "full_name", "phone", "job_title", "is_active", "last_login_at", "created_at")},
        "roles": user.roles,
        "departments": [d.department for d in user.departments],
        "primary_department_id": primary,
        "warehouse_ids": list(wh),
    }


def me_out(db: Session, user: User) -> dict[str, Any]:
    actor = build_actor(db, user)
    return {**user_out(db, user), "permissions": sorted(actor.permissions), "company": get_company(db)}


# ------------------------------------------------------------------ setup
def is_initialized(db: Session) -> bool:
    return db.execute(select(func.count()).select_from(User)).scalar_one() > 0


def initialize(db: Session, data: s.SetupIn) -> User:
    # Serialise concurrent first-run requests: only one can create the company and super admin.
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": 7_461_001})
    if is_initialized(db):
        raise Conflict("Installation is already initialised", code="setup_already_completed")
    _check_password(data.admin.password, data.admin.email)
    seed_reference_data(db)
    company = Company(**data.company.model_dump())
    db.add(company)
    admin = User(email=data.admin.email.lower(), full_name=data.admin.full_name,
                 password_hash=hash_password(data.admin.password))
    admin.roles = [db.execute(select(Role).where(Role.code == "super_admin")).scalar_one()]
    mgmt = db.execute(select(Department).where(Department.code == "MGMT")).scalar_one_or_none()
    if mgmt:
        admin.departments = [UserDepartment(department_id=mgmt.id, is_primary=True)]
    db.add(admin)
    db.flush()
    audit(db, None, "setup.initialize", "company", company.id, {"company": company.name, "admin": admin.email})
    if data.load_demo_data:
        from app.modules.erp_core.demo import load_demo_data
        load_demo_data(db, admin)
    return admin


# ------------------------------------------------------------------ authentication
def login(db: Session, email: str, password: str, ip: str | None, user_agent: str | None) -> tuple[User, str, UserSession]:
    settings = get_settings()
    user = db.execute(select(User).where(func.lower(User.email) == email.lower())).scalar_one_or_none()
    now = utcnow()
    if user is None:
        verify_password(DUMMY_HASH, password)  # equalise timing
        audit(db, None, "auth.login_failed", "user", None, {"email": email, "reason": "unknown_email"})
        db.commit()
        raise InvalidCredentials("Invalid email or password")
    if user.locked_until and user.locked_until > now:
        raise AccountLocked("Account temporarily locked after repeated failed logins. Try again later.",
                            details={"locked_until": user.locked_until.isoformat()})
    if not verify_password(user.password_hash, password):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_count = 0
        audit(db, None, "auth.login_failed", "user", user.id, {"reason": "bad_password"})
        db.commit()
        raise InvalidCredentials("Invalid email or password")
    if not user.is_active:
        raise InvalidCredentials("Account is deactivated. Contact your administrator.", code="account_inactive")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token = new_token()
    session = UserSession(user_id=user.id, token_hash=hash_token(token), created_at=now, last_seen_at=now,
                          expires_at=now + timedelta(hours=settings.session_ttl_hours),
                          ip_address=ip, user_agent=(user_agent or "")[:300])
    db.add(session)
    db.flush()
    audit(db, None, "auth.login", "user", user.id, {"session_id": str(session.id), "ip": ip})
    return user, token, session


def logout(db: Session, actor: Actor) -> None:
    if actor.session_id:
        sess = db.get(UserSession, actor.session_id)
        if sess and sess.revoked_at is None:
            sess.revoked_at = utcnow()
    audit(db, actor, "auth.logout", "user", actor.user_id)


def revoke_sessions(db: Session, user_id: uuid.UUID, except_session: uuid.UUID | None = None) -> None:
    q = select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
    for sess in db.execute(q).scalars():
        if sess.id != except_session:
            sess.revoked_at = utcnow()


def change_password(db: Session, actor: Actor, current: str, new: str) -> None:
    user = db.get(User, actor.user_id)
    assert user is not None
    if not verify_password(user.password_hash, current):
        raise InvalidCredentials("Current password is incorrect")
    _check_password(new, user.email)
    user.password_hash = hash_password(new)
    user.password_changed_at = utcnow()
    revoke_sessions(db, user.id, except_session=actor.session_id)
    audit(db, actor, "auth.password_changed", "user", user.id)


def request_password_reset(db: Session, email: str) -> str | None:
    """Creates a reset token and hands it to the mail backend. Returns the token (for the console backend/tests)."""
    user = db.execute(select(User).where(func.lower(User.email) == email.lower())).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    token = new_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=hash_token(token), expires_at=utcnow() + timedelta(minutes=30)))
    audit(db, None, "auth.password_reset_requested", "user", user.id)
    settings = get_settings()
    if settings.mail_backend == "console":
        if settings.environment == "production":
            # Never write usable reset tokens to production logs; an administrator can set a password instead.
            log.warning("Password reset requested for user %s but no mail backend is configured", user.id)
        else:
            log.warning("Password reset link for %s: %s/reset-password?token=%s", user.email, settings.frontend_url, token)
    return token


def confirm_password_reset(db: Session, token: str, new_password: str) -> None:
    row = db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token))
                     .with_for_update()).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise ValidationFailed("Reset link is invalid or has expired", code="invalid_reset_token")
    user = db.get(User, row.user_id)
    assert user is not None
    _check_password(new_password, user.email)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count, user.locked_until = 0, None
    row.used_at = utcnow()
    revoke_sessions(db, user.id)
    audit(db, None, "auth.password_reset", "user", user.id)


# ------------------------------------------------------------------ users
def _resolve_roles(db: Session, role_ids: list[uuid.UUID]) -> list[Role]:
    roles = list(db.execute(select(Role).where(Role.id.in_(role_ids))).scalars()) if role_ids else []
    if len(roles) != len(set(role_ids)):
        raise ValidationFailed("One or more roles do not exist")
    return roles


def _guard_role_escalation(actor: Actor, roles: list[Role]) -> None:
    if any(r.code == "super_admin" for r in roles) and not actor.is_super_admin:
        raise PermissionDenied("Only a Super Administrator can grant the Super Administrator role")


def _set_departments(db: Session, user: User, dept_ids: list[uuid.UUID], primary: uuid.UUID | None) -> None:
    depts = list(db.execute(select(Department).where(Department.id.in_(dept_ids))).scalars()) if dept_ids else []
    if len(depts) != len(set(dept_ids)):
        raise ValidationFailed("One or more departments do not exist")
    if primary and primary not in set(dept_ids):
        raise ValidationFailed("Primary department must be one of the assigned departments")
    user.departments = [UserDepartment(department_id=d, is_primary=(d == primary)) for d in dict.fromkeys(dept_ids)]


def _set_warehouses(db: Session, user_id: uuid.UUID, warehouse_ids: list[uuid.UUID]) -> None:
    if warehouse_ids:
        found = db.execute(select(func.count()).select_from(Warehouse).where(Warehouse.id.in_(warehouse_ids))).scalar_one()
        if found != len(set(warehouse_ids)):
            raise ValidationFailed("One or more warehouses do not exist")
    db.execute(delete(user_warehouse_access).where(user_warehouse_access.c.user_id == user_id))
    for wid in dict.fromkeys(warehouse_ids):
        db.execute(insert(user_warehouse_access).values(user_id=user_id, warehouse_id=wid))


def create_user(db: Session, actor: Actor, data: s.UserCreate) -> User:
    if db.execute(select(User).where(func.lower(User.email) == data.email.lower())).scalar_one_or_none():
        raise Conflict("A user with this email already exists")
    _check_password(data.password, data.email)
    roles = _resolve_roles(db, data.role_ids)
    _guard_role_escalation(actor, roles)
    user = User(email=data.email.lower(), full_name=data.full_name, phone=data.phone, job_title=data.job_title,
                password_hash=hash_password(data.password), created_by=actor.user_id, updated_by=actor.user_id)
    user.roles = roles
    _set_departments(db, user, data.department_ids, data.primary_department_id)
    db.add(user)
    db.flush()
    _set_warehouses(db, user.id, data.warehouse_ids)
    audit(db, actor, "user.create", "user", user.id, {"email": user.email, "roles": [r.code for r in roles]})
    return user


def update_user(db: Session, actor: Actor, user_id: uuid.UUID, data: s.UserUpdate) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    fields = data.model_dump(exclude_unset=True, include={"full_name", "phone", "job_title"})
    changes = diff(user, fields)
    if data.role_ids is not None:
        if user.id == actor.user_id:
            raise PermissionDenied("You cannot change your own roles")
        roles = _resolve_roles(db, data.role_ids)
        _guard_role_escalation(actor, roles)
        if any(r.code == "super_admin" for r in user.roles) and not actor.is_super_admin:
            raise PermissionDenied("Only a Super Administrator can modify another Super Administrator")
        changes["roles"] = {"from": [r.code for r in user.roles], "to": [r.code for r in roles]}
        user.roles = roles
    if data.department_ids is not None:
        _set_departments(db, user, data.department_ids, data.primary_department_id)
        changes["departments"] = [str(d) for d in data.department_ids]
    if data.warehouse_ids is not None:
        _set_warehouses(db, user.id, data.warehouse_ids)
        changes["warehouse_ids"] = [str(w) for w in data.warehouse_ids]
    user.updated_by = actor.user_id
    audit(db, actor, "user.update", "user", user.id, changes)
    return user


def set_user_active(db: Session, actor: Actor, user_id: uuid.UUID, active: bool) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if user.id == actor.user_id and not active:
        raise BusinessRuleViolation("You cannot deactivate your own account")
    if any(r.code == "super_admin" for r in user.roles) and not actor.is_super_admin:
        raise PermissionDenied("Only a Super Administrator can change another Super Administrator")
    user.is_active = active
    user.updated_by = actor.user_id
    if not active:
        revoke_sessions(db, user.id)
    audit(db, actor, "user.activate" if active else "user.deactivate", "user", user.id)
    return user


def admin_set_password(db: Session, actor: Actor, user_id: uuid.UUID, new_password: str) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if any(r.code == "super_admin" for r in user.roles) and not actor.is_super_admin:
        raise PermissionDenied("Only a Super Administrator can reset another Super Administrator's password")
    _check_password(new_password, user.email)
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count, user.locked_until = 0, None
    revoke_sessions(db, user.id)
    audit(db, actor, "user.password_set_by_admin", "user", user.id)


# ------------------------------------------------------------------ roles
def role_out(db: Session, role: Role) -> dict[str, Any]:
    count = db.execute(select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role.id)).scalar_one()
    perms = sorted(ALL_CODES) if role.code == "super_admin" else sorted(p.code for p in role.permissions)
    return {"id": role.id, "code": role.code, "name": role.name, "description": role.description,
            "is_system": role.is_system, "permissions": perms, "user_count": count}


def _resolve_permissions(db: Session, codes: list[str]) -> list[Permission]:
    unknown = set(codes) - ALL_CODES
    if unknown:
        raise ValidationFailed("Unknown permissions", details={"unknown": sorted(unknown)})
    return list(db.execute(select(Permission).where(Permission.code.in_(codes))).scalars()) if codes else []


def create_role(db: Session, actor: Actor, data: s.RoleCreate) -> Role:
    if db.execute(select(Role).where(Role.code == data.code)).scalar_one_or_none():
        raise Conflict("Role code already exists")
    role = Role(code=data.code, name=data.name, description=data.description, is_system=False)
    role.permissions = _resolve_permissions(db, data.permissions)
    db.add(role)
    db.flush()
    audit(db, actor, "role.create", "role", role.id, {"code": role.code, "permissions": data.permissions})
    return role


def update_role(db: Session, actor: Actor, role_id: uuid.UUID, data: s.RoleUpdate) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found")
    if role.code == "super_admin" and data.permissions is not None:
        raise BusinessRuleViolation("Super Administrator always holds every permission")
    changes = diff(role, data.model_dump(exclude_unset=True, include={"name", "description"}))
    if data.permissions is not None:
        changes["permissions"] = {"from": sorted(p.code for p in role.permissions), "to": sorted(data.permissions)}
        role.permissions = _resolve_permissions(db, data.permissions)
    audit(db, actor, "role.update", "role", role.id, changes)
    return role


def delete_role(db: Session, actor: Actor, role_id: uuid.UUID) -> None:
    role = db.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found")
    if role.is_system:
        raise BusinessRuleViolation("System roles cannot be deleted")
    in_use = db.execute(select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role.id)).scalar_one()
    if in_use:
        raise BusinessRuleViolation("Role is assigned to users; reassign them first")
    audit(db, actor, "role.delete", "role", role.id, {"code": role.code})
    db.delete(role)


# ------------------------------------------------------------------ departments
def department_out(db: Session, d: Department) -> dict[str, Any]:
    count = db.execute(select(func.count()).select_from(UserDepartment).where(UserDepartment.department_id == d.id)).scalar_one()
    return {**{k: getattr(d, k) for k in ("id", "code", "name", "description", "head_user_id", "is_active")}, "member_count": count}


def create_department(db: Session, actor: Actor, data: s.DepartmentCreate) -> Department:
    if db.execute(select(Department).where(Department.code == data.code)).scalar_one_or_none():
        raise Conflict("Department code already exists")
    d = Department(**data.model_dump())
    db.add(d)
    db.flush()
    audit(db, actor, "department.create", "department", d.id, data.model_dump())
    return d


def update_department(db: Session, actor: Actor, dept_id: uuid.UUID, data: s.DepartmentUpdate) -> Department:
    d = db.get(Department, dept_id)
    if d is None:
        raise NotFound("Department not found")
    changes = diff(d, data.model_dump(exclude_unset=True))
    audit(db, actor, "department.update", "department", d.id, changes)
    return d


# ------------------------------------------------------------------ company & settings
def update_company(db: Session, actor: Actor, data: s.CompanyUpdate) -> Company:
    company = get_company(db)
    if company is None:
        raise NotFound("Company is not configured")
    changes = diff(company, data.model_dump(exclude_unset=True))
    audit(db, actor, "company.update", "company", company.id, changes)
    return company


def list_settings(db: Session) -> list[dict[str, Any]]:
    rows = {r.key: r for r in db.execute(select(SystemSetting)).scalars()}
    out = []
    for key, (default, desc) in DEFAULT_SETTINGS.items():
        r = rows.get(key)
        out.append({"key": key, "value": r.value if r else default, "description": desc, "updated_at": r.updated_at if r else None})
    return out


def _coerce_setting(key: str, value: Any) -> Any:
    default = DEFAULT_SETTINGS[key][0]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValidationFailed(f"{key} must be true or false")
        return value
    if isinstance(default, int):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 3650:
            raise ValidationFailed(f"{key} must be an integer between 0 and 3650")
        return value
    if key == "inventory.high_value_threshold":
        try:
            d = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValidationFailed(f"{key} must be a number") from exc
        if d < 0:
            raise ValidationFailed(f"{key} must be non-negative")
        return format(d, "f")
    if key == "inventory.valuation_method":
        if value != "weighted_average":
            raise ValidationFailed("Only 'weighted_average' valuation is available in this release")
        return value
    return value


def update_settings(db: Session, actor: Actor, values: dict[str, Any]) -> None:
    unknown = set(values) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ValidationFailed("Unknown settings", details={"unknown": sorted(unknown)})
    changes = {}
    for key, raw in values.items():
        value = _coerce_setting(key, raw)
        row = db.get(SystemSetting, key)
        if row is None:
            row = SystemSetting(key=key, value=value, description=DEFAULT_SETTINGS[key][1])
            db.add(row)
            changes[key] = {"from": DEFAULT_SETTINGS[key][0], "to": value}
        elif row.value != value:
            changes[key] = {"from": row.value, "to": value}
            row.value = value
        row.updated_by = actor.user_id
    audit(db, actor, "settings.update", "system_settings", None, changes)
