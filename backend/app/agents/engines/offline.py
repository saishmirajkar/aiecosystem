"""Offline engine: deterministic intent matching over the same tools. Used when no LLM is configured
(or the provider is unavailable). It never generates numbers — all figures come from tool output."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.agents.base_agent import AgentResponse, BaseAgent
from app.ai_tools.base import ToolContext, ToolExecutor

Plan = list[tuple[str, dict[str, Any]]]


@dataclass
class OfflineIntent:
    name: str
    pattern: re.Pattern[str]
    plan: Callable[[re.Match[str], str], Plan | str]
    intro: str = ""


class OfflineEngine:
    name = "offline"

    def run(self, agent: BaseAgent, ctx: ToolContext, message: str, history: list[dict[str, Any]],
            system_suffix: str = "") -> AgentResponse:
        tools = agent.tools_for(ctx.actor)
        executor = ToolExecutor(ctx, [t.name for t in agent.tools()])  # permission re-checked per call
        text_msg = " ".join(message.split())
        for intent in agent.offline_intents():
            m = intent.pattern.search(text_msg)
            if not m:
                continue
            plan = intent.plan(m, text_msg)
            if isinstance(plan, str):
                return AgentResponse(agent.agent_id, plan, self.name)
            parts = [intent.intro] if intent.intro else []
            for tool_name, args in plan:
                r = executor.execute(tool_name, args)
                if r.status == "success":
                    parts.append(r.summary)
                elif r.status == "denied":
                    parts.append(f"You don't have permission for that ({r.error}).")
                else:
                    detail = r.error or "The request could not be completed."
                    cands = (r.data or {}).get("candidates")
                    if cands:
                        detail += " Candidates: " + ", ".join(f"{c['name']} ({c['sku']})" for c in cands) + "."
                    parts.append(detail)
            return AgentResponse(
                agent_id=agent.agent_id, text="\n\n".join(p for p in parts if p), engine=self.name,
                cards=[r.card for r in executor.results if r.card], approvals=list(ctx.approvals),
                tool_calls=[{"tool": r.tool, "status": r.status, "duration_ms": r.duration_ms, "error": r.error}
                            for r in executor.results])
        examples = "\n".join(f"• {q}" for q in agent.suggested_questions[:7])
        return AgentResponse(agent.agent_id, "I'm running in offline mode (no language model configured), so I can only "
                             "answer recognised inventory questions. Try one of these:\n" + examples, self.name)
