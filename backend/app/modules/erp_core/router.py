"""ERP core HTTP endpoints."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.common import Page, apply_sort, audit, paginate
from app.core.config import get_settings
from app.core.database import get_db, utcnow
from app.core.deps import DB, Actor, CurrentActor, require_permission
from app.core.errors import NotFound, PermissionDenied
from app.core.rate_limit import limiter
from app.models import (
    Attachment, AuditLog, Department, Notification, NotificationPreference, Permission, Role, User,
)
from app.modules.erp_core import schemas as s
from app.modules.erp_core import service as svc
from app.modules.erp_core.attachments import save_upload, storage_dir

router = APIRouter()
ALERT_TYPES = ["low_stock", "critical_stock", "overstock", "expiring_stock", "pending_receipt",
               "unconfirmed_transfer", "stock_discrepancy", "inactive_stock", "approval_request"]


# ------------------------------------------------------------------ setup
@router.get("/setup/status", tags=["setup"])
def setup_status(db: DB) -> dict:
    return {"initialized": svc.is_initialized(db)}


@router.post("/setup/initialize", tags=["setup"], status_code=201)
def setup_initialize(data: s.SetupIn, request: Request, db: DB) -> dict:
    limiter.hit(f"setup:{request.client.host if request.client else 'na'}", 5)
    admin = svc.initialize(db, data)
    db.commit()
    return {"initialized": True, "admin_email": admin.email}


# ------------------------------------------------------------------ auth
def _set_cookie(response: Response, token: str, max_age: int) -> None:
    st = get_settings()
    response.set_cookie(st.session_cookie_name, token, max_age=max_age, httponly=True, secure=st.cookie_secure,
                        samesite="strict", path="/api")


@router.post("/auth/login", response_model=s.LoginOut, tags=["auth"])
def login(data: s.LoginIn, request: Request, response: Response, db: DB) -> dict:
    ip = request.client.host if request.client else "unknown"
    st = get_settings()
    limiter.hit(f"login-ip:{ip}", st.login_rate_limit_per_minute)
    limiter.hit(f"login-email:{data.email.lower()}", st.login_rate_limit_per_minute)
    user, token, session = svc.login(db, data.email, data.password, ip, request.headers.get("user-agent"))
    db.commit()
    _set_cookie(response, token, st.session_ttl_hours * 3600)
    return {"user": svc.me_out(db, user), "token": token, "expires_at": session.expires_at}


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(actor: CurrentActor, response: Response, db: DB) -> Response:
    svc.logout(db, actor)
    db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/api")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=s.MeOut, tags=["auth"])
def me(actor: CurrentActor, db: DB) -> dict:
    return svc.me_out(db, db.get(User, actor.user_id))  # type: ignore[arg-type]


@router.patch("/auth/me", response_model=s.MeOut, tags=["auth"])
def update_me(data: s.ProfileUpdate, actor: CurrentActor, db: DB) -> dict:
    user = db.get(User, actor.user_id)
    assert user is not None
    changes = svc.diff(user, data.model_dump(exclude_unset=True))
    audit(db, actor, "user.profile_update", "user", user.id, changes)
    db.commit()
    return svc.me_out(db, user)


@router.post("/auth/change-password", status_code=204, tags=["auth"])
def change_password(data: s.ChangePasswordIn, actor: CurrentActor, db: DB) -> Response:
    svc.change_password(db, actor, data.current_password, data.new_password)
    db.commit()
    return Response(status_code=204)


@router.post("/auth/password-reset/request", status_code=202, tags=["auth"])
def password_reset_request(data: s.ResetRequestIn, request: Request, db: DB) -> dict:
    limiter.hit(f"reset:{request.client.host if request.client else 'na'}", 5)
    svc.request_password_reset(db, data.email)
    db.commit()
    return {"message": "If the account exists, a reset link has been sent."}


@router.post("/auth/password-reset/confirm", status_code=204, tags=["auth"])
def password_reset_confirm(data: s.ResetConfirmIn, db: DB) -> Response:
    svc.confirm_password_reset(db, data.token, data.new_password)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ company
@router.get("/company", response_model=s.CompanyOut, tags=["company"])
def get_company(db: DB, _: Actor = Depends(require_permission("company.view"))):
    company = svc.get_company(db)
    if company is None:
        raise NotFound("Company is not configured")
    return company


@router.patch("/company", response_model=s.CompanyOut, tags=["company"])
def update_company(data: s.CompanyUpdate, db: DB, actor: Actor = Depends(require_permission("company.manage"))):
    company = svc.update_company(db, actor, data)
    db.commit()
    return company


@router.post("/company/logo", response_model=s.CompanyOut, tags=["company"])
def upload_logo(db: DB, file: UploadFile = File(...), actor: Actor = Depends(require_permission("company.manage"))):
    company = svc.get_company(db)
    if company is None:
        raise NotFound("Company is not configured")
    att = save_upload(db, actor, file, entity_type="company", entity_id=str(company.id), images_only=True)
    company.logo_attachment_id = att.id
    audit(db, actor, "company.logo_update", "company", company.id, {"attachment_id": att.id})
    db.commit()
    return company


# ------------------------------------------------------------------ users
@router.get("/users", response_model=Page[s.UserOut], tags=["users"])
def list_users(db: DB, _: Actor = Depends(require_permission("users.view")),
               q: str | None = None, is_active: bool | None = None, role_id: uuid.UUID | None = None,
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200), sort: str | None = None):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if role_id:
        stmt = stmt.where(User.roles.any(Role.id == role_id))
    stmt = apply_sort(stmt, sort, {"full_name": User.full_name, "email": User.email, "created_at": User.created_at,
                                   "last_login_at": User.last_login_at}, User.full_name)
    return paginate(db, stmt, page, page_size, lambda u: svc.user_out(db, u))


@router.post("/users", response_model=s.UserOut, status_code=201, tags=["users"])
def create_user(data: s.UserCreate, db: DB, actor: Actor = Depends(require_permission("users.manage"))):
    user = svc.create_user(db, actor, data)
    db.commit()
    return svc.user_out(db, user)


@router.get("/users/{user_id}", response_model=s.UserOut, tags=["users"])
def get_user(user_id: uuid.UUID, db: DB, _: Actor = Depends(require_permission("users.view"))):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return svc.user_out(db, user)


@router.patch("/users/{user_id}", response_model=s.UserOut, tags=["users"])
def update_user(user_id: uuid.UUID, data: s.UserUpdate, db: DB, actor: Actor = Depends(require_permission("users.manage"))):
    user = svc.update_user(db, actor, user_id, data)
    db.commit()
    return svc.user_out(db, user)


@router.post("/users/{user_id}/activate", response_model=s.UserOut, tags=["users"])
def activate_user(user_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("users.manage"))):
    user = svc.set_user_active(db, actor, user_id, True)
    db.commit()
    return svc.user_out(db, user)


@router.post("/users/{user_id}/deactivate", response_model=s.UserOut, tags=["users"])
def deactivate_user(user_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("users.manage"))):
    user = svc.set_user_active(db, actor, user_id, False)
    db.commit()
    return svc.user_out(db, user)


@router.post("/users/{user_id}/set-password", status_code=204, tags=["users"])
def set_password(user_id: uuid.UUID, data: s.SetPasswordIn, db: DB, actor: Actor = Depends(require_permission("users.manage"))):
    svc.admin_set_password(db, actor, user_id, data.new_password)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ roles & permissions
@router.get("/permissions", response_model=list[s.PermissionOut], tags=["roles"])
def list_permissions(db: DB, _: Actor = Depends(require_permission("roles.view"))):
    return db.execute(select(Permission).order_by(Permission.module, Permission.code)).scalars().all()


@router.get("/roles", response_model=list[s.RoleOut], tags=["roles"])
def list_roles(db: DB, _: Actor = Depends(require_permission("roles.view"))):
    roles = db.execute(select(Role).order_by(Role.is_system.desc(), Role.name)).scalars().all()
    return [svc.role_out(db, r) for r in roles]


@router.post("/roles", response_model=s.RoleOut, status_code=201, tags=["roles"])
def create_role(data: s.RoleCreate, db: DB, actor: Actor = Depends(require_permission("roles.manage"))):
    role = svc.create_role(db, actor, data)
    db.commit()
    return svc.role_out(db, role)


@router.get("/roles/{role_id}", response_model=s.RoleOut, tags=["roles"])
def get_role(role_id: uuid.UUID, db: DB, _: Actor = Depends(require_permission("roles.view"))):
    role = db.get(Role, role_id)
    if role is None:
        raise NotFound("Role not found")
    return svc.role_out(db, role)


@router.patch("/roles/{role_id}", response_model=s.RoleOut, tags=["roles"])
def update_role(role_id: uuid.UUID, data: s.RoleUpdate, db: DB, actor: Actor = Depends(require_permission("roles.manage"))):
    role = svc.update_role(db, actor, role_id, data)
    db.commit()
    return svc.role_out(db, role)


@router.delete("/roles/{role_id}", status_code=204, tags=["roles"])
def delete_role(role_id: uuid.UUID, db: DB, actor: Actor = Depends(require_permission("roles.manage"))):
    svc.delete_role(db, actor, role_id)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ departments
@router.get("/departments", response_model=list[s.DepartmentOut], tags=["departments"])
def list_departments(db: DB, _: Actor = Depends(require_permission("departments.view")), include_inactive: bool = False):
    stmt = select(Department).order_by(Department.name)
    if not include_inactive:
        stmt = stmt.where(Department.is_active.is_(True))
    return [svc.department_out(db, d) for d in db.execute(stmt).scalars()]


@router.post("/departments", response_model=s.DepartmentOut, status_code=201, tags=["departments"])
def create_department(data: s.DepartmentCreate, db: DB, actor: Actor = Depends(require_permission("departments.manage"))):
    d = svc.create_department(db, actor, data)
    db.commit()
    return svc.department_out(db, d)


@router.patch("/departments/{dept_id}", response_model=s.DepartmentOut, tags=["departments"])
def update_department(dept_id: uuid.UUID, data: s.DepartmentUpdate, db: DB,
                      actor: Actor = Depends(require_permission("departments.manage"))):
    d = svc.update_department(db, actor, dept_id, data)
    db.commit()
    return svc.department_out(db, d)


# ------------------------------------------------------------------ settings & audit
@router.get("/settings", response_model=list[s.SettingOut], tags=["settings"])
def get_settings_list(db: DB, _: Actor = Depends(require_permission("settings.view"))):
    return svc.list_settings(db)


@router.patch("/settings", response_model=list[s.SettingOut], tags=["settings"])
def patch_settings(data: s.SettingsUpdate, db: DB, actor: Actor = Depends(require_permission("settings.manage"))):
    svc.update_settings(db, actor, data.values)
    db.commit()
    return svc.list_settings(db)


@router.get("/audit-logs", response_model=Page[s.AuditLogOut], tags=["audit"])
def list_audit_logs(db: DB, _: Actor = Depends(require_permission("audit.view")),
                    entity_type: str | None = None, entity_id: str | None = None, action: str | None = None,
                    actor_user_id: uuid.UUID | None = None,
                    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)):
    stmt = select(AuditLog, User.full_name).outerjoin(User, User.id == AuditLog.actor_user_id)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"{action}%"))
    if actor_user_id:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    from sqlalchemy import func as f
    total = db.execute(select(f.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    items = [{**{c: getattr(a, c) for c in ("id", "actor_user_id", "action", "entity_type", "entity_id", "changes",
                                            "ip_address", "created_at")}, "actor_name": name} for a, name in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ------------------------------------------------------------------ notifications
@router.get("/notifications/preferences", response_model=list[s.NotificationPrefOut], tags=["notifications"])
def get_prefs(actor: CurrentActor, db: DB):
    rows = {p.alert_type: p for p in db.execute(select(NotificationPreference).where(NotificationPreference.user_id == actor.user_id)).scalars()}
    return [rows.get(t) or {"alert_type": t, "in_app": True, "email": False} for t in ALERT_TYPES]


@router.put("/notifications/preferences", response_model=list[s.NotificationPrefOut], tags=["notifications"])
def put_prefs(data: list[s.NotificationPrefIn], actor: CurrentActor, db: DB):
    for item in data:
        if item.alert_type not in ALERT_TYPES:
            from app.core.errors import ValidationFailed
            raise ValidationFailed(f"Unknown alert type '{item.alert_type}'", details={"allowed": ALERT_TYPES})
        row = db.get(NotificationPreference, (actor.user_id, item.alert_type))
        if row is None:
            db.add(NotificationPreference(user_id=actor.user_id, **item.model_dump()))
        else:
            row.in_app, row.email = item.in_app, item.email
    audit(db, actor, "notifications.preferences_update", "user", actor.user_id, {"preferences": [i.model_dump() for i in data]})
    db.commit()
    return get_prefs(actor, db)


@router.get("/notifications", response_model=Page[s.NotificationOut], tags=["notifications"])
def list_notifications(actor: CurrentActor, db: DB, unread_only: bool = False,
                       page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    stmt = select(Notification).where(Notification.user_id == actor.user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return paginate(db, stmt.order_by(Notification.created_at.desc()), page, page_size)


@router.post("/notifications/read-all", status_code=204, tags=["notifications"])
def read_all(actor: CurrentActor, db: DB):
    db.execute(update(Notification).where(Notification.user_id == actor.user_id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return Response(status_code=204)


@router.post("/notifications/{notification_id}/read", status_code=204, tags=["notifications"])
def read_one(notification_id: uuid.UUID, actor: CurrentActor, db: DB):
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != actor.user_id:
        raise NotFound("Notification not found")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ attachments
ENTITY_VIEW_PERMS = {"product": "inventory.view", "goods_receipt": "purchasing.view", "purchase_order": "purchasing.view",
                     "company": None, "stock_transfer": "inventory.view", "stock_adjustment": "inventory.view"}
ENTITY_UPLOAD_PERMS = {"product": "products.edit", "goods_receipt": "inventory.receive", "purchase_order": "purchasing.po.create",
                       "stock_transfer": "inventory.transfer", "stock_adjustment": "inventory.adjust"}


@router.post("/attachments", response_model=s.AttachmentOut, status_code=201, tags=["attachments"])
def upload_attachment(actor: CurrentActor, db: DB, file: UploadFile = File(...),
                      entity_type: Annotated[str, Form(max_length=60)] = "", entity_id: Annotated[str, Form(max_length=64)] = ""):
    if entity_type not in ENTITY_UPLOAD_PERMS:
        from app.core.errors import ValidationFailed
        raise ValidationFailed("Unsupported entity_type", details={"allowed": sorted(ENTITY_UPLOAD_PERMS)})
    actor.require(ENTITY_UPLOAD_PERMS[entity_type])
    att = save_upload(db, actor, file, entity_type=entity_type, entity_id=entity_id or None)
    db.commit()
    return att


@router.get("/attachments", response_model=list[s.AttachmentOut], tags=["attachments"])
def list_attachments(actor: CurrentActor, db: DB, entity_type: str, entity_id: str):
    perm = ENTITY_VIEW_PERMS.get(entity_type, "settings.manage")
    if perm:
        actor.require(perm)
    return db.execute(select(Attachment).where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
                      .order_by(Attachment.created_at)).scalars().all()


@router.get("/attachments/{attachment_id}", tags=["attachments"])
def download_attachment(attachment_id: uuid.UUID, actor: CurrentActor, db: Session = Depends(get_db)):
    att = db.get(Attachment, attachment_id)
    if att is None:
        raise NotFound("Attachment not found")
    perm = ENTITY_VIEW_PERMS.get(att.entity_type or "", "settings.manage")
    if perm and not actor.has(perm):
        raise PermissionDenied("You cannot access this attachment")
    path = storage_dir() / att.storage_name
    if not path.is_file():
        raise NotFound("File is missing from storage")
    disposition = "inline" if att.content_type.startswith("image/") or att.content_type == "application/pdf" else "attachment"
    return FileResponse(path, media_type=att.content_type, filename=att.filename, content_disposition_type=disposition)
