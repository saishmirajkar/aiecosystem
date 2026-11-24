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
from app.core.errors import NotFound, PermissionDenied
from app.models import AgentExecutionLog, Conversation, Message
from app.orchestrator import response_builder
from app.orchestrator.agent_registry import AgentRegistry
from app.orchestrator.context_manager import load_history
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.intent_router import IntentRouter

BRIEFING_SUFFIX = ("The user asked for a general briefing. Summarise what needs attention in your own department, highest "
                   "severity first (inventory: alerts and overdue deliveries; workforce: staffing gaps, absences, vacant shifts and "
                   "pending leave). Other agents cover their own departments; finance, quality, maintenance and sales are not "
                   "connected yet.")
MAX_AGENTS_PER_REQUEST = 3


class MainAgent:
    def __init__(self, registry: AgentRegistry, router: IntentRouter, dispatcher: Dispatcher):
        self.registry, self.router, self.dispatcher = registry, router, dispatcher

    def get_conversation(self, db: Session, actor: Actor, conversation_id: uuid.UUID) -> Conversation:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.user_id != actor.user_id or conv.archived_at is not None:
            raise NotFound("Conversation not found")  # never reveal other users' conversations
        return conv

    def handle(self, db: Session, actor: Actor, message: str, conversation_id: uuid.UUID | None = None,
               agent_id: str | None = None) -> tuple[Conversation, Message]:
        """Route a message to department agents. `agent_id` addresses one agent directly (e.g. /ai/workforce/chat)."""
        actor.require("ai.access")
        if agent_id is not None:
            target = self.registry.get(agent_id)
            if target is None or target.status != "active":
                raise NotFound(f"Agent '{agent_id}' is not available")
            if not target.can_be_used_by(actor):
                raise PermissionDenied(f"You don't have access to the {target.name}")
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
        decision = self.router.route(message, allowed_agent_ids={agent_id} if agent_id else allowed)
        if agent_id and not decision.agent_ids:
            decision.agent_ids, decision.reason = [agent_id], f"addressed directly to {agent_id}"
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
