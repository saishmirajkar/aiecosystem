"""Approval Manager: AI (or UI) proposes → a human approves → the backend executes through business services.

An approval is bound to the exact action: `params_hash` is the SHA-256 of the canonical JSON parameters,
re-verified immediately before execution. Parameters can never be edited after creation.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import audit, get_setting, jsonable
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import AppError, InvalidState, NotFound, PermissionDenied
from app.models import ActionApproval, ApprovalStatus


def canonical(params: dict[str, Any]) -> str:
    return json.dumps(jsonable(params), sort_keys=True, separators=(",", ":"))


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(params).encode()).hexdigest()


@dataclass(frozen=True)
class ActionHandler:
    action_type: str
    permission: str
    execute: Callable[[Session, Actor, dict[str, Any]], dict[str, Any]]


_HANDLERS: dict[str, ActionHandler] = {}


def register_action(action_type: str, permission: str):
    def deco(fn: Callable[[Session, Actor, dict[str, Any]], dict[str, Any]]):
        _HANDLERS[action_type] = ActionHandler(action_type, permission, fn)
        return fn
    return deco


def handler_for(action_type: str) -> ActionHandler:
    h = _HANDLERS.get(action_type)
    if h is None:
        raise NotFound(f"Unknown action type '{action_type}'")
    return h


def approval_out(a: ActionApproval) -> dict[str, Any]:
    return {c: getattr(a, c) for c in ("id", "action_type", "params", "summary", "preview", "status", "required_permission",
                                       "requested_by", "requested_via", "agent_id", "conversation_id", "decided_by",
                                       "decided_at", "decision_note", "result", "error", "expires_at", "created_at")}


def create(db: Session, actor: Actor, action_type: str, params: dict[str, Any], summary: str, *,
           preview: dict[str, Any] | None = None, agent_id: str | None = None, conversation_id: uuid.UUID | None = None,
           via: str = "ai") -> ActionApproval:
    h = handler_for(action_type)
    actor.require(h.permission)  # you cannot even propose what you are not allowed to do
    clean = json.loads(canonical(params))
    a = ActionApproval(action_type=action_type, params=clean, params_hash=params_hash(clean), summary=summary,
                       preview=jsonable(preview) if preview else None, status=ApprovalStatus.PENDING,
                       required_permission=h.permission, requested_by=actor.user_id, requested_via=via, agent_id=agent_id,
                       conversation_id=conversation_id,
                       expires_at=utcnow() + timedelta(hours=int(get_setting(db, "ai.approval_ttl_hours"))))
    db.add(a)
    db.flush()
    audit(db, actor, "approval.create", "action_approval", a.id, {"action_type": action_type, "params": clean})
    return a


def _load_pending(db: Session, approval_id: uuid.UUID) -> ActionApproval:
    a = db.execute(select(ActionApproval).where(ActionApproval.id == approval_id).with_for_update()).scalar_one_or_none()
    if a is None:
        raise NotFound("Approval not found")
    if a.status != ApprovalStatus.PENDING:
        raise InvalidState(f"Approval is already {a.status.value}", details={"status": a.status.value})
    if a.expires_at <= utcnow():
        a.status = ApprovalStatus.EXPIRED
        db.flush()
        raise InvalidState("Approval has expired; ask the assistant to prepare it again", code="approval_expired")
    return a


def _authorize_decision(actor: Actor, a: ActionApproval) -> None:
    actor.require(a.required_permission)
    if a.requested_by != actor.user_id and not actor.has("approvals.decide"):
        raise PermissionDenied("Approving another user's request requires 'approvals.decide'",
                               details={"permission": "approvals.decide"})


def approve(db: Session, actor: Actor, approval_id: uuid.UUID, note: str | None = None) -> ActionApproval:
    a = _load_pending(db, approval_id)
    _authorize_decision(actor, a)
    if params_hash(a.params) != a.params_hash:  # tamper evidence: params must be exactly what was proposed
        a.status, a.error = ApprovalStatus.FAILED, "Parameter integrity check failed"
        audit(db, actor, "approval.integrity_failure", "action_approval", a.id)
        db.flush()
        raise InvalidState("Approval parameters failed the integrity check and were not executed")
    a.decided_by, a.decided_at, a.decision_note = actor.user_id, utcnow(), note
    h = handler_for(a.action_type)
    try:
        with db.begin_nested():
            result = h.execute(db, actor, a.params)
        a.status, a.result = ApprovalStatus.EXECUTED, jsonable(result)
        audit(db, actor, "approval.execute", "action_approval", a.id, {"action_type": a.action_type, "result": result})
    except AppError as exc:
        a.status, a.error = ApprovalStatus.FAILED, exc.message
        audit(db, actor, "approval.execute_failed", "action_approval", a.id, {"error": exc.message})
    db.flush()
    return a


def reject(db: Session, actor: Actor, approval_id: uuid.UUID, note: str | None = None) -> ActionApproval:
    a = _load_pending(db, approval_id)
    _authorize_decision(actor, a)
    a.status, a.decided_by, a.decided_at, a.decision_note = ApprovalStatus.REJECTED, actor.user_id, utcnow(), note
    audit(db, actor, "approval.reject", "action_approval", a.id, {"note": note})
    db.flush()
    return a


def expire_stale(db: Session) -> int:
    rows = db.execute(select(ActionApproval).where(ActionApproval.status == ApprovalStatus.PENDING,
                                                   ActionApproval.expires_at <= utcnow())).scalars().all()
    for a in rows:
        a.status = ApprovalStatus.EXPIRED
    return len(rows)


# ---------------------------------------------------------------- action handlers (execute via business services)
@register_action("inventory.create_stock_transfer", "inventory.transfer")
def _exec_transfer(db: Session, actor: Actor, p: dict[str, Any]) -> dict[str, Any]:
    from app.modules.inventory import schemas as s
    from app.modules.inventory.service import create_transfer
    t = create_transfer(db, actor, s.TransferCreate(
        source_warehouse_id=p["source_warehouse_id"], destination_warehouse_id=p["destination_warehouse_id"], notes=p.get("notes"),
        items=[s.TransferItemIn(product_id=i["product_id"], quantity=i["quantity"]) for i in p["items"]]), source="ai")
    return {"record_type": "stock_transfer", "record_id": str(t.id), "number": t.transfer_number, "status": t.status.value,
            "link": f"/inventory/transfers/{t.id}"}


@register_action("inventory.create_stock_adjustment", "inventory.adjust")
def _exec_adjustment(db: Session, actor: Actor, p: dict[str, Any]) -> dict[str, Any]:
    from app.modules.inventory import schemas as s
    from app.modules.inventory.service import create_adjustment
    a = create_adjustment(db, actor, s.AdjustmentCreate(
        warehouse_id=p["warehouse_id"], reason=p["reason"], submit=True,
        items=[s.AdjustmentItemIn(product_id=i["product_id"], location_id=i["location_id"], quantity_delta=i["quantity_delta"])
               for i in p["items"]]), source="ai")
    return {"record_type": "stock_adjustment", "record_id": str(a.id), "number": a.adjustment_number, "status": a.status.value,
            "link": f"/inventory/adjustments/{a.id}"}


@register_action("purchasing.create_requisition", "purchasing.requisition.create")
def _exec_requisition(db: Session, actor: Actor, p: dict[str, Any]) -> dict[str, Any]:
    from app.modules.purchasing import schemas as s
    from app.modules.purchasing.service import create_requisition
    r = create_requisition(db, actor, s.RequisitionCreate(
        justification=p.get("justification"), required_date=p.get("required_date"), submit=True,
        items=[s.RequisitionItemIn(product_id=i["product_id"], quantity=i["quantity"],
                                   preferred_supplier_id=i.get("preferred_supplier_id"),
                                   estimated_unit_cost=i.get("estimated_unit_cost")) for i in p["items"]]), source="ai")
    return {"record_type": "purchase_requisition", "record_id": str(r.id), "number": r.requisition_number,
            "status": r.status.value, "link": f"/purchasing/requisitions/{r.id}"}
