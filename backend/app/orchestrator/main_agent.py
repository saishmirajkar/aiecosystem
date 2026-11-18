"""Main Agent: the single conversational entry point. Routes, delegates, consolidates and persists.

It never touches department data itself — only registered agents (through their tools) do.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.common import jsonable
from app.core.database import utcnow
from app.core.deps import Actor
from app.core.errors import NotFound
from app.models import AgentExecutionLog, Conversation, Message
from app.orchestrator import response_builder
from app.orchestrator.agent_registry import AgentRegistry
from app.orchestrator.context_manager import load_history
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.intent_router import IntentRouter

BRIEFING_SUFFIX = ("The user asked for a general briefing. Only the Inventory department (with the purchasing foundation) is "
                   "connected. Use get_inventory_alerts and get_pending_goods_receipts (overdue) and summarise what needs "
                   "attention, highest severity first. State that other departments are not connected yet.")
MAX_AGENTS_PER_REQUEST = 3


class MainAgent:
    def __init__(self, registry: AgentRegistry, router: IntentRouter, dispatcher: Dispatcher):
        self.registry, self.router, self.dispatcher = registry, router, dispatcher

    def get_conversation(self, db: Session, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != actor.user_id or conv.archived_at is not None:
            raise NotFound("Conversation not found")  # never reveal other users' conversations
        return conv

    def handle(self, db: Session, actor: Actor, message: str, conversation_id: uuid.UUID | None = None) -> tuple[Conversation, Message]:
        actor.require("ai.access")
        if conversation_id:
            conv = self.get_conversation(db, actor, conversation_id)
        else:
            title = " ".join(message.split())[:80]
            conv = Conversation(user_id=actor.user_id, title=title)
            db.add(conv)
            db.flush()
        history = load_history(db, conv.id)
        db.add(Message(conversation_id=conv.id, role="user", content=message))
        conv.updated_at = utcnow()
        db.commit()

        allowed = {a.agent_id for a in self.registry.active() if a.can_be_used_by(actor)}
        decision = self.router.route(message, allowed_agent_ids=allowed)
        db.add(AgentExecutionLog(conversation_id=conv.id, user_id=actor.user_id, agent_id="main", engine="router",
                                 event="routing", status="success",
                                 output_summary={"agents": decision.agent_ids, "unconnected": decision.unconnected,
                                                 "briefing": decision.briefing, "reason": decision.reason}))
        responses = []
        for agent_id in decision.agent_ids[:MAX_AGENTS_PER_REQUEST]:
            agent = self.registry.get(agent_id)
            assert agent is not None
            responses.append(self.dispatcher.run(db, actor, agent, message, history, conv.id,
                                                 BRIEFING_SUFFIX if decision.briefing else ""))
        payload: dict[str, Any] = response_builder.build(decision, responses, {a.agent_id: a.name for a in self.registry.active()})
        reply = Message(conversation_id=conv.id, role="assistant", content=payload["text"], payload=jsonable(payload),
                        status="ok" if payload["status"] != "error" else "error", agent_ids=decision.agent_ids,
                        usage=jsonable(payload.get("usage")) if payload.get("usage") else None)
        db.add(reply)
        conv.updated_at = utcnow()
        db.commit()
        return conv, reply
