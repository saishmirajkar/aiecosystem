"""Intent Router: decides which registered agent(s) handle a message and which unconnected departments were asked about.

Deliberately rule-based and transparent: the decision is logged and shown to the user. It never routes to an
agent that is not registered, and it never pretends a department is connected.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.orchestrator.agent_registry import AgentRegistry

# Departments that exist as organisational records but have no system/agent yet.
UNCONNECTED_DEPARTMENTS: dict[str, tuple[str, list[str]]] = {
    "QA": ("Quality", ["quality", "inspection", "defect", "ncr", "non-conformance", "capa", "audit finding"]),
    "MAINT": ("Maintenance", ["maintenance", "breakdown", "downtime", "preventive", "machine health", "mtbf", "mttr"]),
    "FIN": ("Finance and Accounts", ["invoice", "payable", "receivable", "payment", "profit", "revenue", "cash flow",
                                     "balance sheet", "gst", "tax return", "budget", "expense", "p&l"]),
    "HR": ("HR and Workforce", ["payroll", "salary", "salaries", "attendance", "leave", "employee", "hiring", "recruit",
                                "headcount", "overtime", "shift roster"]),
    "LOG": ("Logistics", ["freight", "e-way bill", "eway bill", "fleet", "route plan", "vehicle tracking"]),
    "CRM": ("CRM and Sales", ["lead", "leads", "sales order", "quotation", "deal", "pipeline", "opportunit", "crm"]),
}

BRIEFING = re.compile(r"\b(what needs my attention|needs (my )?attention|briefing|what should i (look at|focus)|anything urgent|"
                      r"daily summary|status of the business|how is the business)\b", re.I)


@dataclass
class RouteDecision:
    agent_ids: list[str] = field(default_factory=list)
    unconnected: list[str] = field(default_factory=list)  # department names
    briefing: bool = False
    reason: str = ""


def _hits(text: str, keywords: list[str]) -> int:
    return sum(1 for k in keywords if re.search(r"(?<![a-z])" + re.escape(k), text))


class IntentRouter:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def route(self, message: str, *, allowed_agent_ids: set[str]) -> RouteDecision:
        text = message.lower()
        decision = RouteDecision(briefing=bool(BRIEFING.search(text)))
        scores = {a.agent_id: _hits(text, a.routing_keywords) for a in self.registry.active() if a.agent_id in allowed_agent_ids}
        decision.unconnected = [name for _, (name, kws) in UNCONNECTED_DEPARTMENTS.items() if _hits(text, kws)]

        if decision.briefing:
            decision.agent_ids = [a for a in scores]  # every connected agent contributes to a briefing
            decision.reason = "briefing across connected departments"
            return decision
        top = max(scores.values(), default=0)
        # Agents scoring at least half of the best match contribute (e.g. a stock + production question asks both).
        matched = [a for a, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s > 0 and s * 2 >= top]
        if matched:
            decision.agent_ids = matched
            decision.reason = "keyword match: " + ", ".join(matched)
        elif decision.unconnected:
            decision.reason = "only unconnected departments referenced"
        elif scores:
            # Nothing recognisable: give it to the connected agent(s); they answer within their own scope.
            decision.agent_ids = list(scores)
            decision.reason = "no specific department detected; using connected agents"
        else:
            decision.reason = "user has no access to any connected agent"
        return decision
