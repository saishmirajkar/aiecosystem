"""Shared service utilities: audit, document numbers, pagination, sorting, settings access, schema base."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from typing_extensions import Annotated

from app.core.errors import ValidationFailed
from app.models import AuditLog, DocumentSequence, SystemSetting

if False:  # pragma: no cover - typing only
    from app.core.deps import Actor

T = TypeVar("T")

# Decimals are serialised as strings (no float loss); inputs accept numbers or strings.
DecimalStr = Annotated[Decimal, PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json")]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=200)


def paginate(db: Session, stmt: Select, page: int, page_size: int, mapper=None) -> dict[str, Any]:
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.limit(page_size).offset((page - 1) * page_size)).unique().scalars().all()
    items = [mapper(r) for r in rows] if mapper else rows
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def apply_sort(stmt: Select, sort: str | None, allowed: dict[str, Any], default: Any) -> Select:
    if not sort:
        return stmt.order_by(default)
    desc = sort.startswith("-")
    key = sort.lstrip("-")
    if key not in allowed:
        raise ValidationFailed(f"Unsupported sort field '{key}'", details={"allowed": sorted(allowed)})
    col = allowed[key]
    return stmt.order_by(col.desc() if desc else col.asc())


def _jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


jsonable = _jsonable


def audit(db: Session, actor: "Actor | None", action: str, entity_type: str | None = None,
          entity_id: Any = None, changes: dict | None = None) -> None:
    db.add(AuditLog(
        actor_user_id=actor.user_id if actor else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        changes=_jsonable(changes) if changes else None,
        ip_address=actor.ip_address if actor else None,
        user_agent=actor.user_agent if actor else None,
        request_id=actor.request_id if actor else None,
    ))


def diff(obj: Any, updates: dict[str, Any]) -> dict[str, Any]:
    """Apply updates to an ORM object and return {field: {from, to}} for changed fields."""
    changes: dict[str, Any] = {}
    for key, new in updates.items():
        old = getattr(obj, key)
        if old != new:
            changes[key] = {"from": old, "to": new}
            setattr(obj, key, new)
    return changes


SEQUENCES = {
    "inventory_transaction": "IT",
    "stock_transfer": "TR",
    "stock_adjustment": "ADJ",
    "purchase_requisition": "PR",
    "purchase_order": "PO",
    "goods_receipt": "GRN",
    "leave_request": "LV",
    "overtime_opportunity": "OT",
    "payroll_export": "PAY",
}


def next_number(db: Session, name: str) -> str:
    """Gap-tolerant document numbering under a row lock (inside the caller's transaction)."""
    seq = db.execute(select(DocumentSequence).where(DocumentSequence.name == name).with_for_update()).scalar_one_or_none()
    if seq is None:
        db.execute(
            pg_insert(DocumentSequence)
            .values(name=name, prefix=SEQUENCES.get(name, name[:3].upper()), next_value=1, padding=6)
            .on_conflict_do_nothing()
        )
        seq = db.execute(select(DocumentSequence).where(DocumentSequence.name == name).with_for_update()).scalar_one()
    value = seq.next_value
    seq.next_value = value + 1
    return f"{seq.prefix}-{str(value).zfill(seq.padding)}"


# ---------------------------------------------------------------- system settings
DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "inventory.allow_negative_stock": (False, "Allow postings that make stock negative (requires inventory.negative_stock)"),
    "inventory.high_value_threshold": ("500000", "Issues/transfers valued above this require inventory.high_value"),
    "inventory.valuation_method": ("weighted_average", "Inventory valuation method"),
    "inventory.transfer_requires_approval": (True, "Stock transfers must be approved before dispatch"),
    "inventory.expiry_alert_days": (30, "Days before expiry to raise expiring-stock alerts"),
    "inventory.transfer_overdue_days": (3, "Days in transit before a transfer is flagged unconfirmed"),
    "inventory.inactive_days": (90, "Days without movement before stock is flagged inactive"),
    "purchasing.po_requires_approval": (True, "Purchase orders require approval before sending"),
    "ai.approval_ttl_hours": (24, "Hours before an AI-proposed action expires"),
}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(SystemSetting, key)
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS[key][0]
