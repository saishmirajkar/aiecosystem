"""Dispatcher: runs each selected agent with its own context, engine and limits; isolates failures."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.agents.base_agent import AgentResponse, BaseAgent
from app.agents.engines.claude import ClaudeEngine, EngineUnavailable
from app.agents.engines.offline import OfflineEngine
from app.core.config import get_settings
from app.core.deps import Actor
from app.models import AgentExecutionLog
from app.orchestrator.context_manager import build_context

log = logging.getLogger("above.ai.dispatch")


class Dispatcher:
    def __init__(self, claude: ClaudeEngine | None = None, offline: OfflineEngine | None = None):
        self._claude = claude
        self.offline = offline or OfflineEngine()

    @property
    def claude(self) -> ClaudeEngine:
        if self._claude is None:
            self._claude = ClaudeEngine()
        return self._claude

    def run(self, db: Session, actor: Actor, agent: BaseAgent, message: str, history: list[dict[str, Any]],
            conversation_id: uuid.UUID | None, system_suffix: str = "") -> AgentResponse:
        mode = get_settings().ai_mode
        engine = self.claude if mode == "claude" else self.offline
        started = time.monotonic()
        notices: list[str] = []
        try:
            ctx = build_context(db, actor, agent.agent_id, engine.name, conversation_id)
            response = engine.run(agent, ctx, message, history, system_suffix)
        except EngineUnavailable as exc:
            log.warning("Claude unavailable for %s: %s — falling back to offline engine", agent.agent_id, exc)
            notices.append(f"{exc}. Answered with the offline engine instead.")
            db.rollback()  # drop any partial work (e.g. drafts) from the failed attempt before retrying offline
            ctx = build_context(db, actor, agent.agent_id, self.offline.name, conversation_id)
            response = self.offline.run(agent, ctx, message, history, system_suffix)
        except Exception:
            log.exception("Agent %s failed", agent.agent_id)
            db.rollback()  # the user message was committed before dispatch; discard only partial agent work
            response = AgentResponse(agent.agent_id, f"The {agent.name} hit an unexpected error and could not answer. "
                                     "The failure has been logged.", engine.name, status="error")
        response.notices.extend(notices)
        db.add(AgentExecutionLog(conversation_id=conversation_id, user_id=actor.user_id, agent_id=agent.agent_id,
                                 engine=response.engine, event="agent_run", status=response.status,
                                 output_summary={"tool_calls": len(response.tool_calls), "approvals": len(response.approvals),
                                                 "usage": response.usage},
                                 duration_ms=int((time.monotonic() - started) * 1000)))
        return response
