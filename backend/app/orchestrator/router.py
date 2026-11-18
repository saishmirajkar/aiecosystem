"""AI Command Center and approval endpoints."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy import func, select

from app.core.common import InputSchema, Page, jsonable, paginate
from app.core.config import get_settings
from app.core.database import utcnow
from app.core.deps import DB, Actor, require_permission
from app.core.errors import NotFound
from app.core.rate_limit import limiter
from app.models import ActionApproval, ApprovalStatus, Conversation, Message, User
from app.orchestrator import approval_manager
from app.orchestrator.bootstrap import get_main_agent, get_registry

router = APIRouter()
AI = require_permission("ai.access")


class ChatIn(InputSchema):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None


class TitleIn(InputSchema):
    title: str = Field(min_length=1, max_length=200)


class DecisionIn(InputSchema):
    note: str | None = Field(None, max_length=1000)


def message_out(m: Message) -> dict[str, Any]:
    return {"id": str(m.id), "conversation_id": str(m.conversation_id), "role": m.role, "content": m.content,
            "payload": m.payload, "status": m.status, "created_at": m.created_at.isoformat()}


def refresh_approvals(db, payload: dict | None) -> dict | None:
    """Show the *current* state of approvals referenced by a stored message."""
    if not payload or not payload.get("approvals"):
        return payload
    ids = [uuid.UUID(a["id"]) for a in payload["approvals"]]
    current = {str(a.id): approval_manager.approval_out(a) for a in db.execute(select(ActionApproval).where(ActionApproval.id.in_(ids))).scalars()}
    payload = dict(payload)
    payload["approvals"] = [jsonable(current.get(a["id"], a)) for a in payload["approvals"]]
    return payload


@router.post("/ai/chat", tags=["ai"])
def chat(data: ChatIn, db: DB, actor: Actor = Depends(AI)) -> dict:
    limiter.hit(f"ai:{actor.user_id}", get_settings().ai_rate_limit_per_minute)
    conv, reply = get_main_agent().handle(db, actor, data.message, data.conversation_id)
    return {"conversation": {"id": str(conv.id), "title": conv.title}, "message": message_out(reply)}


@router.get("/ai/conversations", tags=["ai"])
def list_conversations(db: DB, actor: Actor = Depends(AI), q: str | None = None,
                       page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)) -> dict:
    stmt = select(Conversation).where(Conversation.user_id == actor.user_id, Conversation.archived_at.is_(None))
    if q:
        stmt = stmt.where(Conversation.title.ilike(f"%{q}%"))
    result = paginate(db, stmt.order_by(Conversation.updated_at.desc()), page, page_size)
    result["items"] = [{"id": str(c.id), "title": c.title, "created_at": c.created_at.isoformat(),
                        "updated_at": c.updated_at.isoformat()} for c in result["items"]]
    return result


@router.get("/ai/conversations/{conversation_id}", tags=["ai"])
def get_conversation(conversation_id: uuid.UUID, db: DB, actor: Actor = Depends(AI)) -> dict:
    conv = get_main_agent().get_conversation(db, actor, conversation_id)
    msgs = db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)).scalars().all()
    return {"id": str(conv.id), "title": conv.title, "created_at": conv.created_at.isoformat(),
            "messages": [{**message_out(m), "payload": refresh_approvals(db, m.payload)} for m in msgs]}


@router.patch("/ai/conversations/{conversation_id}", tags=["ai"])
def rename_conversation(conversation_id: uuid.UUID, data: TitleIn, db: DB, actor: Actor = Depends(AI)) -> dict:
    conv = get_main_agent().get_conversation(db, actor, conversation_id)
    conv.title = data.title
    db.commit()
    return {"id": str(conv.id), "title": conv.title}


@router.delete("/ai/conversations/{conversation_id}", status_code=204, tags=["ai"])
def archive_conversation(conversation_id: uuid.UUID, db: DB, actor: Actor = Depends(AI)) -> Response:
    conv = get_main_agent().get_conversation(db, actor, conversation_id)
    conv.archived_at = utcnow()
    db.commit()
    return Response(status_code=204)


@router.get("/ai/agents", tags=["ai"])
def list_agents(actor: Actor = Depends(AI)) -> dict:
    reg = get_registry()
    from app.orchestrator.intent_router import UNCONNECTED_DEPARTMENTS
    return {"agents": [{**reg.describe(a), "available_to_you": a.can_be_used_by(actor),
                        "tools": [t for t in reg.describe(a)["tools"] if actor.has(t["permission"])]} for a in reg.all()],
            "not_connected": [name for name, _ in UNCONNECTED_DEPARTMENTS.values()],
            "engine": get_settings().ai_mode,
            "model": get_settings().anthropic_model if get_settings().ai_mode == "claude" else None}


@router.get("/ai/agents/{agent_id}", tags=["ai"])
def get_agent(agent_id: str, actor: Actor = Depends(AI)) -> dict:
    agent = get_registry().get(agent_id)
    if agent is None:
        raise NotFound("Agent not found")
    return get_registry().describe(agent)


@router.get("/ai/suggestions", tags=["ai"])
def suggestions(actor: Actor = Depends(AI)) -> list[str]:
    return [q for a in get_registry().active() if a.can_be_used_by(actor) for q in a.suggested_questions]


# ---------------------------------------------------------------- approvals
@router.get("/approvals", tags=["approvals"])
def list_approvals(db: DB, actor: Actor = Depends(require_permission("approvals.view")), status: ApprovalStatus | None = None,
                   mine: bool = False, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100)) -> dict:
    approval_manager.expire_stale(db)
    db.commit()
    stmt = select(ActionApproval)
    if status:
        stmt = stmt.where(ActionApproval.status == status)
    if mine or not actor.has("approvals.decide"):
        stmt = stmt.where(ActionApproval.requested_by == actor.user_id)
    result = paginate(db, stmt.order_by(ActionApproval.created_at.desc()), page, page_size)
    names = dict(db.execute(select(User.id, User.full_name)).all())
    result["items"] = [jsonable({**approval_manager.approval_out(a), "requested_by_name": names.get(a.requested_by),
                                 "decided_by_name": names.get(a.decided_by),
                                 "can_decide": a.status == ApprovalStatus.PENDING and actor.has(a.required_permission)
                                 and (a.requested_by == actor.user_id or actor.has("approvals.decide"))})
                       for a in result["items"]]
    result["pending_count"] = db.execute(select(func.count()).select_from(ActionApproval).where(
        ActionApproval.status == ApprovalStatus.PENDING)).scalar_one()
    return result


@router.post("/approvals/{approval_id}/approve", tags=["approvals"])
def approve(approval_id: uuid.UUID, db: DB, data: DecisionIn | None = None, actor: Actor = Depends(AI)) -> dict:
    a = approval_manager.approve(db, actor, approval_id, data.note if data else None)
    db.commit()
    return jsonable(approval_manager.approval_out(a))


@router.post("/approvals/{approval_id}/reject", tags=["approvals"])
def reject(approval_id: uuid.UUID, db: DB, data: DecisionIn | None = None, actor: Actor = Depends(AI)) -> dict:
    a = approval_manager.reject(db, actor, approval_id, data.note if data else None)
    db.commit()
    return jsonable(approval_manager.approval_out(a))
