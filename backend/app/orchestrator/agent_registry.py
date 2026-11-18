"""Agent Registry: the set of real, implemented department agents. Synced to the `agents`/`agent_tools` tables."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base_agent import BaseAgent
from app.models import Agent, AgentTool


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"Agent {agent.agent_id} already registered")
        missing = [n for n in agent.tool_names if n not in {t.name for t in agent.tools()}]
        if missing:
            raise ValueError(f"Agent {agent.agent_id} references unknown tools: {missing}")
        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    def active(self) -> list[BaseAgent]:
        return [a for a in self._agents.values() if a.status == "active"]

    def all(self) -> list[BaseAgent]:
        return list(self._agents.values())

    def describe(self, agent: BaseAgent) -> dict:
        return {"id": agent.agent_id, "name": agent.name, "department": agent.department, "description": agent.description,
                "version": agent.version, "status": agent.status, "required_permissions": agent.required_permissions,
                "tools": [{"name": t.name, "description": t.description, "kind": t.kind, "permission": t.permission,
                           "input_schema": t.json_schema()} for t in agent.tools()],
                "suggested_questions": agent.suggested_questions}

    def sync_to_db(self, db: Session) -> None:
        for agent in self._agents.values():
            row = db.get(Agent, agent.agent_id)
            if row is None:
                row = Agent(id=agent.agent_id)
                db.add(row)
            row.name, row.department_code, row.description = agent.name, agent.department, agent.description
            row.version, row.status, row.required_permissions = agent.version, agent.status, agent.required_permissions
            existing = {t.name: t for t in db.execute(select(AgentTool).where(AgentTool.agent_id == agent.agent_id)).scalars()}
            for t in agent.tools():
                at = existing.pop(t.name, None) or AgentTool(agent_id=agent.agent_id, name=t.name)
                at.description, at.kind, at.required_permission, at.input_schema, at.is_enabled = \
                    t.description, t.kind, t.permission, t.json_schema(), True
                db.add(at)
            for stale in existing.values():
                stale.is_enabled = False
        db.flush()
