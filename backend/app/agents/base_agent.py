"""Department agent contract. Agents are independent: own prompt, tools, permissions, routing vocabulary."""
from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from app.ai_tools.base import TOOLS, Tool
from app.core.deps import Actor


@dataclass
class AgentResponse:
    agent_id: str
    text: str
    engine: str
    cards: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok | error | refused
    usage: dict[str, Any] | None = None
    notices: list[str] = field(default_factory=list)


class BaseAgent(ABC):
    agent_id: str
    name: str
    department: str
    description: str
    version: str = "1.0.0"
    status: str = "active"
    required_permissions: list[str] = []
    tool_names: list[str] = []
    routing_keywords: list[str] = []
    system_prompt: str = ""
    suggested_questions: list[str] = []

    def tools(self) -> list[Tool]:
        return [t for name in self.tool_names if (t := TOOLS.get(name)) is not None]

    def tools_for(self, actor: Actor) -> list[Tool]:
        """Only tools the authenticated user may use are offered to the model (executor re-checks anyway)."""
        return [t for t in self.tools() if actor.has(t.permission)]

    def can_be_used_by(self, actor: Actor) -> bool:
        return all(actor.has(p) for p in self.required_permissions)

    def offline_intents(self) -> list[Any]:
        return []
