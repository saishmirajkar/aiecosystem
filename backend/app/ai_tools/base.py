"""Typed, permission-aware AI tool framework.

A tool can only be executed through `ToolExecutor.execute`, which:
  1. looks the tool up in the agent's allow-list (unknown tools are refused),
  2. validates the model-supplied arguments with a strict Pydantic model,
  3. re-checks the *authenticated user's* permission (never the model's claims),
  4. runs the handler inside a SAVEPOINT so a failing tool cannot leave partial writes,
  5. writes an `agent_execution_logs` row for every call — success, denial or failure.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from app.core.common import jsonable
from app.core.deps import Actor
from app.core.errors import AppError, NotFound, PermissionDenied
from app.models import AgentExecutionLog

log = logging.getLogger("above.ai.tools")

Provenance = Literal["verified", "calculated", "recommendation", "draft", "completed"]
MAX_MODEL_PAYLOAD_CHARS = 24000


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ToolError(AppError):
    """Raised by handlers for expected, explainable failures (e.g. ambiguous product)."""
    status_code, code = 422, "tool_error"


@dataclass
class ToolContext:
    db: Session
    actor: Actor
    agent_id: str
    engine: str
    conversation_id: uuid.UUID | None = None
    timezone: str = "UTC"
    approvals: list[dict[str, Any]] = field(default_factory=list)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)


@dataclass
class ToolOutput:
    data: dict[str, Any]
    summary: str
    card: dict[str, Any] | None = None


@dataclass
class ToolResult:
    tool: str
    status: Literal["success", "denied", "invalid", "not_found", "error"]
    input: dict[str, Any]
    data: dict[str, Any] | None = None
    summary: str = ""
    card: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: int = 0

    def for_model(self) -> str:
        """Serialised result handed back to the LLM — labelled as data, never as instructions."""
        if self.status != "success":
            return json.dumps({"status": self.status, "error": self.error, **({"details": self.data} if self.data else {})})
        payload = json.dumps({"status": "success", "summary": self.summary, "data": jsonable(self.data)}, default=str)
        if len(payload) > MAX_MODEL_PAYLOAD_CHARS:
            payload = json.dumps({"status": "success", "summary": self.summary, "truncated": True,
                                  "data_preview": payload[:MAX_MODEL_PAYLOAD_CHARS]})
        return payload


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[ToolInput]
    permission: str
    kind: Literal["read", "draft"]
    handler: Callable[[ToolContext, Any], ToolOutput]

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        schema["additionalProperties"] = False
        return schema

    def to_anthropic(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.json_schema()}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())


TOOLS = ToolRegistry()


def tool(name: str, description: str, input_model: type[ToolInput], permission: str, kind: Literal["read", "draft"] = "read"):
    def deco(fn: Callable[[ToolContext, Any], ToolOutput]) -> Callable[[ToolContext, Any], ToolOutput]:
        TOOLS.register(Tool(name, description, input_model, permission, kind, fn))
        return fn
    return deco


class ToolExecutor:
    def __init__(self, ctx: ToolContext, allowed: list[str]):
        self.ctx = ctx
        self.allowed = set(allowed)
        self.results: list[ToolResult] = []

    def execute(self, name: str, raw_input: Any) -> ToolResult:
        started = time.monotonic()
        raw = raw_input if isinstance(raw_input, dict) else {}
        tool_def = TOOLS.get(name) if name in self.allowed else None
        if tool_def is None:
            result = ToolResult(name, "invalid", raw, error=f"Tool '{name}' is not available to this agent")
        elif not self.ctx.actor.has(tool_def.permission):
            result = ToolResult(name, "denied", raw, error=f"The current user lacks permission '{tool_def.permission}'")
        else:
            try:
                args = tool_def.input_model.model_validate(raw)
            except ValidationError as exc:
                errors = [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
                result = ToolResult(name, "invalid", raw, error="Invalid tool arguments", data={"errors": errors})
            else:
                try:
                    with self.ctx.db.begin_nested():
                        out = tool_def.handler(self.ctx, args)
                    result = ToolResult(name, "success", raw, data=out.data, summary=out.summary, card=out.card)
                except PermissionDenied as exc:
                    result = ToolResult(name, "denied", raw, error=exc.message)
                except NotFound as exc:
                    result = ToolResult(name, "not_found", raw, error=exc.message, data=exc.details)
                except AppError as exc:
                    result = ToolResult(name, "invalid", raw, error=exc.message, data=exc.details)
                except Exception:  # pragma: no cover - defensive
                    log.exception("Tool %s failed", name)
                    result = ToolResult(name, "error", raw, error="The tool failed unexpectedly; the failure was logged.")
        result.duration_ms = int((time.monotonic() - started) * 1000)
        self.ctx.db.add(AgentExecutionLog(
            conversation_id=self.ctx.conversation_id, user_id=self.ctx.actor.user_id, agent_id=self.ctx.agent_id,
            engine=self.ctx.engine, event="tool_call", tool_name=name, tool_input=jsonable(raw), status=result.status,
            output_summary={"summary": result.summary[:500]} if result.status == "success" else None,
            error=result.error, duration_ms=result.duration_ms))
        self.ctx.db.flush()
        self.results.append(result)
        return result
