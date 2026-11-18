# System Architecture — Above Studio Industrial AI Ecosystem

> YOUR ENTIRE BUSINESS. WITHIN REACH. Every department. One intelligence.

## 1. Scope of the initial release

| In scope (this release) | Explicitly out of scope |
|---|---|
| ERP Core (company, users, RBAC, departments, master data, audit) | CRM / sales / leads |
| Inventory Management System | Production, Quality, Maintenance, Finance, HR, Logistics systems |
| Procurement *foundation* (requisitions, POs, approvals, GRN) | Procurement analytics / procurement AI |
| Inventory AI Agent (fully functional) | Any other department agent |
| Main AI Agent / orchestrator foundation | Uncontrolled agent-to-agent messaging |
| Management dashboard + AI Command Center | Multi-tenant shared database |

Departments such as Production or Finance exist only as **organizational records** (for user assignment).

## 2. Deployment model — one installation per customer

Every customer receives an independent deployment (its own PostgreSQL database, Redis,
users, configuration, AI settings and integration credentials). There is **no shared
multi-tenant production database**.

The schema still carries a single `companies` row and every user belongs to it. This is a
*company-identification abstraction*, not tenancy: it lets the installation know who it
belongs to (name, currency, timezone, tax and fiscal settings) and keeps future deployment
tooling (fleet management, backups, upgrades) straightforward. The company is created once,
during installation (`POST /api/v1/setup/initialize` or `python -m app.cli bootstrap`).

## 3. Logical layers

```
┌──────────────────────────────────────────────────────────────────────────┐
│ L1  User Interface (React + TS + Vite + Tailwind + shadcn-style UI)      │
│     • Management Dashboard   • AI Command Center (also a global drawer)  │
└───────────────▲──────────────────────────────────────────────────────────┘
                │ REST /api/v1  (session cookie or bearer token)
┌───────────────┴──────────────────────────────────────────────────────────┐
│ FastAPI application                                                      │
│ ┌──────────────────────────────────────────────────────────────────────┐ │
│ │ L2  AI Orchestration (app/orchestrator)                              │ │
│ │     MainAgent → IntentRouter → AgentRegistry → Dispatcher            │ │
│ │     ContextManager · ResponseConsolidator · ApprovalManager          │ │
│ └───────────────▲──────────────────────────────────────────────────────┘ │
│ ┌───────────────┴──────────────────────────────────────────────────────┐ │
│ │ L3  Department agents (app/agents)  — InventoryAgent (active)        │ │
│ │     Each agent: own prompt, own tools, own permissions, own context  │ │
│ └───────────────▲──────────────────────────────────────────────────────┘ │
│ ┌───────────────┴──────────────────────────────────────────────────────┐ │
│ │ AI Tools (app/ai_tools) — typed, permission-checked, audited         │ │
│ └───────────────▲──────────────────────────────────────────────────────┘ │
│ ┌───────────────┴──────────────────────────────────────────────────────┐ │
│ │ L4  Business services (app/modules/*/services)                       │ │
│ │     erp_core · master data · inventory · purchasing · reporting      │ │
│ └───────────────▲──────────────────────────────────────────────────────┘ │
└─────────────────┼────────────────────────────────────────────────────────┘
┌─────────────────┴───────────────┐   ┌───────────────────────────────────┐
│ L5  PostgreSQL (source of truth)│   │ Redis: Celery broker, rate limits │
└─────────────────────────────────┘   └───────────────────────────────────┘
                                      Celery worker + beat: alert digests,
                                      scheduled checks, async jobs
```

### Key rule: the AI never touches the database directly
REST endpoints and AI tools are both thin adapters over the **same business services**. An AI
tool cannot do anything the authenticated user could not do through the REST API, because the
service layer and the permission checks are shared.

## 4. Backend module layout

```
backend/app/
  core/          config, database session, security (hashing, sessions), errors,
                 permissions catalogue, rate limiting, pagination, idempotency
  models/        SQLAlchemy 2.0 ORM models (one file per domain)
  modules/
    erp_core/    setup, auth, users, roles, departments, company, settings, audit
    master_data/ units, categories, products, suppliers, warehouses, locations
    inventory/   ledger engine, balances, receipts/issues, transfers, adjustments,
                 reservations, batches/serials, valuation, alerts, reports
    purchasing/  requisitions, purchase orders, goods receipts
  ai_tools/      tool framework + inventory tools
  agents/        base agent, engines (Claude / offline), inventory agent
  orchestrator/  main agent, registry, router, context, consolidator, approvals
  workers/       Celery app + scheduled tasks
  api/           v1 router aggregation
```

Each module contains `schemas.py` (Pydantic contracts), `service.py` (business logic, no HTTP),
and `router.py` (HTTP adapter). Services take a SQLAlchemy `Session` and an `Actor`
(authenticated user + resolved permissions) — never raw request data.

## 5. Major technology decisions

| Decision | Choice | Reasoning |
|---|---|---|
| API framework | FastAPI + Pydantic v2 | Typed contracts, automatic OpenAPI, validation at the edge |
| ORM | SQLAlchemy 2.0 **sync** + psycopg 3 | Row-level locking (`SELECT … FOR UPDATE`) and explicit transactions are simpler and safer in sync code; FastAPI runs sync endpoints in a threadpool |
| Migrations | Alembic | Versioned, reviewable schema changes; also installs DB triggers that make the stock ledger append-only |
| Auth | Server-side sessions (opaque token, SHA-256 hash stored), HttpOnly SameSite=Strict cookie, or `Authorization: Bearer` | Revocable on logout/deactivation; no JWT secrets to rotate; tokens never readable by JS |
| Passwords | Argon2id (argon2-cffi) | Current OWASP recommendation |
| Money/quantity | `NUMERIC(18,4)` / `NUMERIC(18,6)` | No floating point in business data |
| Valuation | Weighted average cost, per product, company-wide | Requested initial method; `valuation_method` column leaves room for FIFO layers |
| AI | Anthropic Messages API, manual tool loop, model configurable (default `claude-opus-5-5`) | We must authorize, audit and approval-gate every tool call inside our own backend; a self-hosted loop gives that control per customer deployment |
| AI offline mode | Deterministic intent engine over the same tools | Command Center remains useful (and testable) without an API key; clearly labelled as offline |
| Background jobs | Celery + Redis | Scheduled alert digests and long-running work |
| Frontend | React 18, TS strict, Vite, Tailwind v4, Radix-based shadcn-style components, TanStack Query, React Router, Recharts | As specified; components are owned in-repo (shadcn model) |

## 6. Request lifecycle examples

**Stock issue (REST)**: `POST /api/v1/inventory/issues` with `Idempotency-Key` → auth
dependency resolves `Actor` → permission `inventory.issue` checked → idempotency record
reserved → `InventoryLedger.post()` locks affected balance + valuation rows in deterministic
order, validates policy (negative stock, warehouse access, high-value), writes immutable
`stock_movements`, updates `inventory_balances` and `product_valuations` → audit log → commit.

**AI question**: `POST /api/v1/ai/chat` → MainAgent loads conversation context →
IntentRouter picks `inventory` → Dispatcher runs InventoryAgent with only the tools the user is
permitted to use → each tool call validated (Pydantic), authorized (again, server-side),
executed via services, logged in `agent_execution_logs` → agent returns narrative + structured
cards built **from tool output** (not from model text) → consolidated response persisted.

**AI action**: a draft tool (e.g. `create_purchase_requisition_draft`) creates an
`action_approvals` row bound to a SHA-256 hash of the canonical parameters. Nothing changes in
business tables until a user with the right permission calls `POST /approvals/{id}/approve`,
at which point the backend re-verifies the hash and permissions and executes the action
through the normal service.

## 7. Extending to new departments

1. Add business module under `app/modules/<dept>/`.
2. Implement tools in `app/ai_tools/<dept>_tools.py`.
3. Subclass `BaseAgent` (`app/agents/<dept>_agent.py`) with prompt, tool list, permissions,
   routing vocabulary.
4. Register it in `app/orchestrator/bootstrap.py`. The registry syncs it to the `agents`
   table on start-up; the router and UI pick it up automatically. No orchestrator rewrite.

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Concurrent stock updates corrupt balances | Row locks in sorted key order, single transaction per posting, tests with parallel threads |
| Client retries duplicate movements | Mandatory `Idempotency-Key` on posting endpoints + unique constraint |
| LLM hallucinated numbers | Cards rendered from tool output; prompt rules; offline engine never generates numbers |
| Prompt injection via records | Tool output wrapped and labelled as untrusted data; tools are the only capability; permissions enforced server-side |
| No API key at a customer site | Offline engine; health endpoint reports AI mode |
| Scope creep into other departments | Router answers "not connected yet" for unimplemented departments |
