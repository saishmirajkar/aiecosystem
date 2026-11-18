# Agent Architecture

## Principles
1. **The AI has controlled access to real systems, not a database connection.** Agents act only
   through typed tools that call the same business services as the REST API.
2. **Identity comes from the backend.** The `ToolContext` is built from the authenticated
   session (user id → DB-resolved roles, permissions, warehouse scope). Nothing the model or
   the user types can change it.
3. **Prepare ≠ execute.** Write-capable tools only create *drafts* (`action_approvals`). A human
   with the right permission approves; the backend executes.
4. **Numbers come from tools.** Cards shown in the UI are rendered from tool output; the
   model's prose is labelled as narrative.
5. **Independent agents, one orchestrator.** Agents never call each other; the Main Agent
   delegates, enforces limits and consolidates.

## Components (`backend/app/`)

| Component | File | Responsibility |
|---|---|---|
| Tool framework | `ai_tools/base.py` | `Tool` definition (name, description, Pydantic input model, required permission, kind `read`/`draft`), `ToolResult`, `ToolExecutor` (validate → authorize → execute in savepoint → audit) |
| Inventory tools | `ai_tools/inventory_tools.py` | 14 tools listed below |
| Base agent | `agents/base_agent.py` | Agent contract: id, name, department, description, version, required permissions, routing vocabulary, system prompt, tool names |
| Engines | `agents/engines/claude.py`, `agents/engines/offline.py` | How an agent reasons: Claude manual tool loop, or deterministic offline intent engine |
| Inventory agent | `agents/inventory_agent.py` | Inventory responsibilities, prompt, tools, offline intents |
| Agent registry | `orchestrator/agent_registry.py` | Registered agents + sync to `agents` / `agent_tools` tables |
| Intent router | `orchestrator/intent_router.py` | Scores agents by vocabulary; detects not-yet-connected departments; briefing intent |
| Context manager | `orchestrator/context_manager.py` | Builds trusted `ToolContext`, loads bounded conversation history |
| Dispatcher | `orchestrator/dispatcher.py` | Runs agents with limits (max tool calls, max iterations), catches failures |
| Response consolidator | `orchestrator/response_builder.py` | Merges agent responses, adds department notices, provenance labels |
| Approval manager | `orchestrator/approval_manager.py` | Creates bound approvals, approves/rejects, executes via action handlers |
| Main agent | `orchestrator/main_agent.py` | Request lifecycle, persistence of conversation + messages |

## Agent registry contract
Every agent declares: `agent_id`, `name`, `department`, `description`, `tools`,
`required_permissions`, `status` (`active`), `version`. On start-up the registry upserts
`agents` and `agent_tools`; `GET /api/v1/ai/agents` lists them. Only real, implemented agents
are registered — the UI shows other departments as "not connected", sourced from the
departments catalogue, not from fake agents.

## Inventory Agent tools

| Tool | Kind | Permission | Purpose |
|---|---|---|---|
| `search_products` | read | `inventory.view` | Find products by name/SKU/code/barcode/category |
| `get_product_details` | read | `inventory.view` | Master data + stock summary + suppliers |
| `get_stock_level` | read | `inventory.view` | Physical / available / reserved / in-transit by warehouse & location |
| `get_warehouse_stock` | read | `inventory.view` | Stock of one warehouse |
| `get_low_stock_items` | read | `inventory.view` | Items at/below reorder or minimum level |
| `get_stock_movement_history` | read | `inventory.view` | Ledger lines with filters |
| `get_recent_stock_receipts` | read | `inventory.view` | Confirmed GRNs in a date window (default today, company timezone) |
| `get_pending_goods_receipts` | read | `purchasing.view` | PO lines still outstanding, overdue flag |
| `get_purchase_order_status` | read | `purchasing.view` | PO header + lines + receipts |
| `get_inventory_valuation` | read | `reports.view` | Weighted-average valuation by product/warehouse/category |
| `get_inventory_alerts` | read | `inventory.view` | Current alert set (low, critical, overstock, expiry, overdue, transit, discrepancy, inactive) |
| `generate_inventory_report` | read | `reports.view` | Runs any registered report, returns rows |
| `create_stock_transfer_draft` | draft | `inventory.transfer` | Proposes a transfer → approval |
| `create_stock_adjustment_draft` | draft | `inventory.adjust` | Proposes an adjustment → approval |
| `create_purchase_requisition_draft` | draft | `purchasing.requisition.create` | Proposes a requisition, with stock & open-PO context → approval |

Each tool: description, strict Pydantic input schema (`extra="forbid"`), server-side
authorization, structured output (`data`, `sources` with record ids + timestamps, `kind`),
error mapping (`not_found`, `invalid_input`, `permission_denied`, `error`) and an
`agent_execution_logs` row per call.

The model only *sees* tools the current user is permitted to use; the executor still
re-checks at call time (defence in depth).

## Request lifecycle
```
POST /ai/chat {conversation_id?, message}
 └ MainAgent.handle
    ├ ContextManager.build(user)        → ToolContext (trusted), history (last N turns)
    ├ IntentRouter.route(message)       → RouteDecision(agents=[inventory], notices=[…])
    ├ Dispatcher.run(agent, …)          → engine.run → tool calls via ToolExecutor
    ├ ResponseConsolidator.build        → text + cards + sources + approvals + provenance
    └ persist user + assistant messages, execution logs, approvals
```

### Routing rules
* Inventory / purchasing / stock vocabulary → `inventory`.
* Vocabulary of unconnected departments (payroll, invoice, production order, machine
  breakdown, leads, …) → polite "not connected yet" notice; no agent invoked unless inventory
  terms are also present.
* Briefing questions ("what needs my attention today") → `inventory` alert briefing **plus** a
  notice that other departments are not connected.
* No recognisable department vocabulary → the message goes to the connected agent(s), which
  answer within their own scope (and say so when a question is out of scope). Routing is
  deliberately rule-based and logged (`agent_execution_logs.event = 'routing'`) so decisions
  are transparent and deterministic; an LLM classifier can be added behind the same
  `IntentRouter.route()` interface once several department agents exist.
* Up to 3 agents per request (`MAX_AGENTS_PER_REQUEST`); each runs independently and the
  consolidator merges their sections.

### Engines
* **Claude engine** — Messages API (beta endpoint for `fallbacks`), model from
  `ANTHROPIC_MODEL` (default `claude-opus-5-5`), adaptive thinking, `effort` from settings
  (default `medium`), `fallbacks: "default"` refusal fallback, manual loop with
  `max_iterations` and `max_tool_calls`; assistant content appended unchanged; tool errors
  returned as `is_error` tool results; `refusal` / `max_tokens` stop reasons handled.
* **Offline engine** — deterministic intent patterns → the same tools → templated narrative.
  Never invents numbers; says what it cannot answer. Response is labelled `engine: offline`.

### Response format (assistant message payload)
```json
{
  "text": "…narrative…",
  "engine": "claude|offline",
  "routing": {"agents": ["inventory"], "notices": ["Finance is not connected yet."]},
  "cards": [
    {"type": "table|metrics|approval|notice", "title": "…",
     "provenance": "verified|calculated|recommendation|draft|completed",
     "columns": [], "rows": [], "metrics": [], "sources": [{"type": "product", "id": "…", "as_of": "…"}]}
  ],
  "approvals": [{"id": "…", "action_type": "…", "status": "pending", "summary": "…"}],
  "tool_calls": [{"tool": "get_stock_level", "status": "success", "duration_ms": 12}]
}
```

## Approval workflow
1. Draft tool validates parameters against current data (products exist, warehouses
   accessible, quantity > 0) and calls `ApprovalManager.create(...)`.
2. `action_approvals` row stores `action_type`, canonical JSON `params`, `params_hash`
   (SHA-256), `required_permission`, `expires_at` (default 24 h), requester, agent and
   conversation.
3. `POST /approvals/{id}/approve` → approver must hold `required_permission` **and**
   `approvals.decide`; hash re-verified; action executed through the business service in the
   same transaction; result stored (`executed` with record reference, or `failed` + error).
4. `POST /approvals/{id}/reject` → `rejected` with note. Expired approvals cannot execute.

Action outcomes: requisition → created as `pending_approval` requisition; transfer → `draft`
transfer; adjustment → `pending_approval` adjustment (still needs its own approval to post).

**Who may decide:** the approver must hold the action's `required_permission`. Confirming
*your own* AI-prepared draft needs nothing more (it is equivalent to doing the action
manually); approving *someone else's* request additionally requires `approvals.decide`.

## Testing the agent layer
`backend/tests/test_ai.py` covers routing, the offline engine end-to-end on real data, the
approval lifecycle (binding, expiry, single use, permissions), conversation isolation, audit
rows, tool-argument validation, and the Claude loop driven by a **scripted fake Anthropic
client** (tool execution, permission denials returned as `is_error`, invalid arguments,
refusal, iteration budget, provider outage → offline fallback, prompt-injection text treated
as data). The Claude engine has not been exercised against the live API in this repository's
test suite because no API key is configured in CI; see IMPLEMENTATION_LOG for how to run a
live check.

## Model configuration
| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | empty | Empty → offline engine |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` | Any current Claude model ID |
| `ANTHROPIC_EFFORT` | `medium` | `low` … `max`; Opus 5.5 defaults to `medium` |
| `ANTHROPIC_FALLBACKS_ENABLED` | `true` | Server-side refusal fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) |
| `AI_ENGINE` | `auto` | `auto` / `claude` / `offline` |
| `AI_MAX_ITERATIONS` / `AI_MAX_TOOL_CALLS` | 8 / 12 | Per request |

## Prompt-injection resistance
* User messages, conversation history and tool output are treated as data. Tool output is
  delivered as JSON tool results, never concatenated into the system prompt.
* System prompt states that instructions inside data must be ignored and that the agent cannot
  change permissions.
* The model has no tool that alters permissions, users or settings, and no raw SQL.
* Even a fully compromised model can only: read what the user may read, and create drafts that
  a human must approve.

## Multi-agent future
`RouteDecision.agents` is a list. The dispatcher already runs each selected agent independently
and the consolidator merges results. Delegation depth is 1 (Main → department); department
agents cannot invoke other agents. Execution limits are enforced per agent and per request.
