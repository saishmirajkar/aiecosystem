"""Authentication & authorization dependencies.

`Actor` is the trusted identity used by every service and AI tool. It is built only from the
server-side session — never from client-supplied claims.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db, utcnow
from app.core.errors import AuthenticationRequired, PermissionDenied
from app.core.permissions import ALL_CODES
from app.core.security import hash_token
from app.models import User, UserSession, user_warehouse_access

CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "above-web"


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    email: str
    full_name: str
    role_codes: frozenset[str]
    permissions: frozenset[str]
    warehouse_ids: frozenset[uuid.UUID] | None = None  # None → all warehouses
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    session_id: uuid.UUID | None = field(default=None, compare=False)

    @property
    def is_super_admin(self) -> bool:
        return "super_admin" in self.role_codes

    def has(self, permission: str) -> bool:
        return self.is_super_admin or permission in self.permissions

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise PermissionDenied(f"Missing permission: {permission}", details={"permission": permission})

    def can_access_warehouse(self, warehouse_id: uuid.UUID) -> bool:
        return self.warehouse_ids is None or warehouse_id in self.warehouse_ids

    def require_warehouse(self, warehouse_id: uuid.UUID) -> None:
        if not self.can_access_warehouse(warehouse_id):
            raise PermissionDenied("You do not have access to this warehouse", details={"warehouse_id": str(warehouse_id)})


def build_actor(db: Session, user: User, *, request: Request | None = None, session_id: uuid.UUID | None = None) -> Actor:
    role_codes = frozenset(r.code for r in user.roles)
    perms = frozenset(p.code for r in user.roles for p in r.permissions)
    if "super_admin" in role_codes:
        perms = ALL_CODES
    wh_rows = db.execute(
        select(user_warehouse_access.c.warehouse_id).where(user_warehouse_access.c.user_id == user.id)
    ).scalars().all()
    return Actor(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role_codes=role_codes,
        permissions=perms,
        warehouse_ids=frozenset(wh_rows) if wh_rows else None,
        ip_address=request.client.host if request and request.client else None,
        user_agent=(request.headers.get("user-agent", "")[:300] if request else None),
        request_id=getattr(request.state, "request_id", None) if request else None,
        session_id=session_id,
    )


def _extract_token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(get_settings().session_cookie_name), True


def get_current_actor(request: Request, db: Annotated[Session, Depends(get_db)]) -> Actor:
    settings = get_settings()
    token, via_cookie = _extract_token(request)
    if not token:
        raise AuthenticationRequired("Authentication required")
    if via_cookie and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise PermissionDenied("Missing CSRF header", code="csrf_failed")

    session = db.execute(select(UserSession).where(UserSession.token_hash == hash_token(token))).scalar_one_or_none()
    now = utcnow()
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or session.last_seen_at + timedelta(minutes=settings.session_idle_minutes) <= now
    ):
        raise AuthenticationRequired("Session expired or invalid")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise AuthenticationRequired("Account is inactive")
    if (now - session.last_seen_at).total_seconds() > 60:
        session.last_seen_at = now
        db.commit()
    return build_actor(db, user, request=request, session_id=session.id)


CurrentActor = Annotated[Actor, Depends(get_current_actor)]
DB = Annotated[Session, Depends(get_db)]


def require_permission(*codes: str):
    """Dependency factory: actor must hold every listed permission."""

    def _dep(actor: CurrentActor) -> Actor:
        for code in codes:
            actor.require(code)
        return actor

    return _dep


def require_any_permission(*codes: str):
    def _dep(actor: CurrentActor) -> Actor:
        if not any(actor.has(c) for c in codes):
            raise PermissionDenied(f"Requires one of: {', '.join(codes)}")
        return actor

    return _dep
