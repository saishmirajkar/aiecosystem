"""Wires the orchestrator. To add a department: implement its agent and register it here."""
from __future__ import annotations

from functools import lru_cache

from app.agents.inventory_agent import InventoryAgent
from app.agents.workforce_agent import WorkforceAgent
from app.core.database import SessionLocal
from app.orchestrator.agent_registry import AgentRegistry
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.intent_router import IntentRouter
from app.orchestrator.main_agent import MainAgent


@lru_cache
def get_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(InventoryAgent())
    registry.register(WorkforceAgent())
    return registry


@lru_cache
def get_main_agent() -> MainAgent:
    registry = get_registry()
    return MainAgent(registry, IntentRouter(registry), Dispatcher())


def sync_registry_to_db() -> None:
    with SessionLocal() as db:
        get_registry().sync_to_db(db)
        db.commit()
