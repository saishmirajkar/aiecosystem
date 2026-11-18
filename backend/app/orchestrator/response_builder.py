"""Response Consolidator: merges agent responses into one structured assistant message with provenance."""
from __future__ import annotations

from typing import Any

from app.agents.base_agent import AgentResponse
from app.orchestrator.intent_router import RouteDecision


def connected_departments_text(names: list[str]) -> str:
    return ", ".join(names) if names else "none"


def build(decision: RouteDecision, responses: list[AgentResponse], agent_names: dict[str, str]) -> dict[str, Any]:
    notices: list[str] = []
    connected = [agent_names[r.agent_id] for r in responses]
    if decision.unconnected:
        notices.append(f"{', '.join(decision.unconnected)} {'is' if len(decision.unconnected) == 1 else 'are'} not connected yet. "
                       f"Connected today: {connected_departments_text(list(agent_names.values()))}.")
    if decision.briefing:
        notices.append(f"This briefing covers {', '.join(agent_names[r.agent_id] for r in responses) or 'connected agents'} — "
                       "quality, maintenance, finance, HR and sales are not connected yet.")
    for r in responses:
        notices.extend(r.notices)

    if not responses:
        if decision.unconnected:
            text = (f"I can't answer that yet: {', '.join(decision.unconnected)} "
                    f"{'is' if len(decision.unconnected) == 1 else 'are'} not connected to the Command Center. "
                    f"Today I can help with: {connected_departments_text(list(agent_names.values()))}.")
        else:
            text = "You don't have access to any connected agent. Ask an administrator for AI Command Center access."
        return {"text": text, "engine": None, "routing": {"agents": [], "reason": decision.reason, "unconnected": decision.unconnected},
                "cards": [], "approvals": [], "tool_calls": [], "notices": notices, "status": "ok"}

    if len(responses) == 1:
        text = responses[0].text
    else:  # future multi-agent consolidation: sections per department
        text = "\n\n".join(f"**{agent_names[r.agent_id]}**\n{r.text}" for r in responses)
    status = "error" if all(r.status == "error" for r in responses) else ("refused" if any(r.status == "refused" for r in responses) else "ok")
    return {
        "text": text,
        "engine": responses[0].engine,
        "routing": {"agents": [{"id": r.agent_id, "name": agent_names[r.agent_id]} for r in responses],
                    "reason": decision.reason, "unconnected": decision.unconnected},
        "cards": [c for r in responses for c in r.cards],
        "approvals": [a for r in responses for a in r.approvals],
        "tool_calls": [{**t, "agent": r.agent_id} for r in responses for t in r.tool_calls],
        "notices": notices,
        "usage": {r.agent_id: r.usage for r in responses if r.usage},
        "status": status,
        "provenance_legend": {"verified": "Business records read from the database",
                              "calculated": "Computed from business records",
                              "recommendation": "AI-generated suggestion", "draft": "Proposed action awaiting approval",
                              "completed": "Action executed after approval"},
    }
