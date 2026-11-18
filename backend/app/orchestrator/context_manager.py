"""Context Manager: builds the trusted tool context and bounded conversation history.

The identity in the context comes from the authenticated session (Actor). The model never supplies it.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai_tools.base import ToolContext
from app.core.config import get_settings
from app.core.deps import Actor
from app.models import Message
from app.modules.erp_core.service import get_company


def build_context(db: Session, actor: Actor, agent_id: str, engine: str, conversation_id: uuid.UUID | None) -> ToolContext:
    company = get_company(db)
    return ToolContext(db=db, actor=actor, agent_id=agent_id, engine=engine, conversation_id=conversation_id,
                       timezone=company.timezone if company else "UTC")


def load_history(db: Session, conversation_id: uuid.UUID | None) -> list[dict[str, Any]]:
    """Last N turns as plain text, alternating user/assistant (well-formed for the Messages API)."""
    if conversation_id is None:
        return []
    turns = get_settings().ai_history_turns
    rows = db.execute(select(Message).where(Message.conversation_id == conversation_id, Message.status == "ok")
                      .order_by(Message.created_at.desc()).limit(turns * 2)).scalars().all()
    history: list[dict[str, Any]] = []
    for m in reversed(rows):
        if m.role not in ("user", "assistant") or not m.content.strip():
            continue
        if history and history[-1]["role"] == m.role:
            history[-1]["content"] += "\n\n" + m.content
        else:
            history.append({"role": m.role, "content": m.content})
    while history and history[0]["role"] != "user":
        history.pop(0)
    if history and history[-1]["role"] == "user":
        history.pop()  # the new message is appended by the engine
    return history
