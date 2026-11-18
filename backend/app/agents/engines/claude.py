"""Claude engine: a manual tool-use loop on the Anthropic Messages API.

We host the loop ourselves so every tool call passes through our ToolExecutor (validation, server-side
authorization, audit, approval gating) inside this customer's deployment.
"""
from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.agents.base_agent import AgentResponse, BaseAgent
from app.ai_tools.base import ToolContext, ToolExecutor
from app.core.config import get_settings

log = logging.getLogger("above.ai.claude")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class EngineUnavailable(Exception):
    """Raised when the model provider cannot be reached or rejects our credentials."""


class ClaudeEngine:
    name = "claude"

    def __init__(self, client: Any | None = None):
        self.settings = get_settings()
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self.settings.anthropic_api_key, max_retries=2, timeout=120.0)
        return self._client

    def _create(self, **kwargs: Any) -> Any:
        s = self.settings
        params = dict(model=s.anthropic_model, max_tokens=s.ai_max_tokens, thinking={"type": "adaptive"},
                      output_config={"effort": s.anthropic_effort}, cache_control={"type": "ephemeral"}, **kwargs)
        if s.anthropic_fallbacks_enabled:
            return self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
        return self.client.messages.create(**params)

    def run(self, agent: BaseAgent, ctx: ToolContext, message: str, history: list[dict[str, Any]],
            system_suffix: str = "") -> AgentResponse:
        s = self.settings
        tools = agent.tools_for(ctx.actor)
        executor = ToolExecutor(ctx, [t.name for t in agent.tools()])  # permission re-checked per call
        system = agent.system_prompt + (f"\n\n{system_suffix}" if system_suffix else "") + \
            f"\n\nCompany timezone: {ctx.timezone}. Current local date/time: {ctx.now().strftime('%Y-%m-%d %H:%M')}."
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": message}]
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "iterations": 0}
        text, status, tool_calls = "", "ok", 0

        try:
            for _ in range(s.ai_max_iterations):
                usage["iterations"] += 1
                response = self._create(system=system, tools=[t.to_anthropic() for t in tools], messages=messages)
                u = getattr(response, "usage", None)
                if u is not None:
                    usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
                    usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
                    usage["cache_read_input_tokens"] += getattr(u, "cache_read_input_tokens", 0) or 0

                if response.stop_reason == "refusal":
                    text, status = "I can't help with that request.", "refused"
                    break
                if response.stop_reason == "pause_turn":
                    messages.append({"role": "assistant", "content": response.content})
                    continue

                tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
                if response.stop_reason == "tool_use" and tool_uses:
                    messages.append({"role": "assistant", "content": response.content})  # appended unchanged
                    results = []
                    for tu in tool_uses:
                        if tool_calls >= s.ai_max_tool_calls:
                            results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                            "content": "Tool-call budget for this request is exhausted. Answer with what you have."})
                            continue
                        tool_calls += 1
                        r = executor.execute(tu.name, tu.input)
                        results.append({"type": "tool_result", "tool_use_id": tu.id, "content": r.for_model(),
                                        "is_error": r.status != "success"})
                    messages.append({"role": "user", "content": results})  # all results in one message
                    continue

                text = "".join(getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text").strip()
                if response.stop_reason == "max_tokens":
                    text += "\n\n_(Response truncated at the length limit.)_"
                break
            else:
                text = text or "I reached the processing limit for one request. Here is what I gathered so far."
        except anthropic.AuthenticationError as exc:
            raise EngineUnavailable("The AI provider rejected the configured API key") from exc
        except anthropic.PermissionDeniedError as exc:
            raise EngineUnavailable("The AI provider denied access for this API key") from exc
        except anthropic.RateLimitError as exc:
            raise EngineUnavailable("The AI provider is rate limiting requests") from exc
        except anthropic.APIConnectionError as exc:
            raise EngineUnavailable("The AI provider could not be reached") from exc
        except anthropic.APIStatusError as exc:
            log.warning("Anthropic API error %s (request %s)", exc.status_code, getattr(exc, "request_id", None))
            raise EngineUnavailable(f"The AI provider returned an error ({exc.status_code})") from exc

        return AgentResponse(
            agent_id=agent.agent_id, text=text or "I couldn't produce an answer.", engine=self.name,
            cards=[r.card for r in executor.results if r.card], approvals=list(ctx.approvals),
            tool_calls=[{"tool": r.tool, "status": r.status, "duration_ms": r.duration_ms, "error": r.error} for r in executor.results],
            status=status, usage=usage)
