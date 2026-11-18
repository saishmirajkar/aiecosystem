# Above Studio — Industrial AI Ecosystem

> **YOUR ENTIRE BUSINESS. WITHIN REACH.** Every department. One intelligence.

The foundation of an AI-powered ERP for mechanical and manufacturing companies: an ERP core,
a complete Inventory Management System with a procurement foundation, an independent
**Inventory AI Agent**, and a central **AI Command Center** that routes requests to department
agents. Each customer receives a dedicated deployment.

This is not a chatbot that knows about inventory. It is an inventory and ERP system with its
own intelligence layer: the AI acts only through typed, permission-checked, audited tools over
the real business services, and every action it proposes needs human approval.

## What's in this release
| Area | Highlights |
|---|---|
| ERP core | First-run setup, company settings, users, server-side sessions (Argon2id), configurable RBAC (8 seeded roles, 37 permissions), departments, warehouse scoping, audit log, notifications, secure uploads |
| Master data | Products (6 types, batch/serial/expiry tracking), categories, units & conversions, suppliers, warehouses with Section → Rack → Bin locations |
| Inventory | Append-only stock ledger (DB trigger), locked & idempotent postings, opening balances, stock-in, issues with FEFO auto-allocation, damaged/returned status, reservations, in-transit transfers with approval, approval-gated adjustments & cycle counts, weighted-average valuation, 8 live alert types, 13 reports with CSV |
| Purchasing | Requisitions → approval → conversion, purchase orders with approval workflow, goods receipts with partial, damaged, rejected and authorised over-receipt — the only path from PO to stock |
| AI | Inventory Agent with 15 tools; Claude engine (manual tool loop, adaptive thinking, refusal fallbacks) or deterministic offline engine; Main Agent with registry, router, dispatcher, consolidation; approvals bound to a parameter hash; per-user conversations |
| UI | Dashboard, inventory, purchasing, AI Command Center (page + global drawer), settings — light & dark themes, responsive |

> **Branch `client/company-x`** adds Company X's production and job-work flow: plants and job-work
> vendors as stock locations, BOM + route per product, production orders with a live "where are
> the parts" tracker and timeline, split lots, vendor-to-vendor shipments, rework/scrap, printable
> job-work challans, costing that includes vendor rates, customers and dispatch, and a Production
> AI Agent. See [`docs/clients/COMPANY_X.md`](docs/clients/COMPANY_X.md).

CRM and the other department systems are intentionally **not** included; the architecture is
ready for them (see `docs/architecture/SYSTEM_ARCHITECTURE.md` §7).

## Quick start (Docker)
```bash
cp .env.example .env    # set SECRET_KEY, POSTGRES_PASSWORD; ENVIRONMENT=demo for local http
docker compose up -d --build
```
Open http://localhost:8080 and complete the first-run wizard (tick *Load synthetic demo data*
for an evaluation instance). Full guide: [`docs/deployment/DEPLOYMENT.md`](docs/deployment/DEPLOYMENT.md).

## Local development
Prerequisites: Python 3.12+, Node 22, PostgreSQL 16, Redis 7.

**Database**
```bash
sudo -u postgres psql -c "CREATE ROLE above LOGIN PASSWORD 'above' CREATEDB;"
```
```bash
sudo -u postgres createdb -O above above
```
```bash
sudo -u postgres createdb -O above above_test
```

**Backend** (http://localhost:8000, API docs at `/api/docs`)
```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt
```
```bash
alembic upgrade head
```
```bash
ADMIN_PASSWORD='Admin-Passw0rd!' python -m app.cli bootstrap --company "Demo Precision Components" --admin-email admin@demo.example.com --admin-name "Asha Kulkarni" --demo
```
```bash
uvicorn app.main:app --reload
```
Optional background worker and scheduler:
```bash
celery -A app.workers.celery_app worker --loglevel=INFO
```
```bash
celery -A app.workers.celery_app beat --loglevel=INFO
```

**Frontend** (http://localhost:5173, proxies `/api` to :8000)
```bash
cd frontend && npm install && npm run dev
```

Demo users (demo installations only) share the password `Demo-Passw0rd!`:
`ops@`, `inventory@`, `warehouse@`, `purchase@`, `staff@`, `viewer@demo.example.com`.

**Enable Claude:** set `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_MODEL`, default
`claude-opus-5-5`) in `backend/.env` and restart. Without a key the Command Center runs the
offline engine, which answers recognised questions from live data and says when it cannot.

## Tests
```bash
cd backend && . .venv/bin/activate && pytest -q
```
```bash
cd frontend && npm run typecheck && npm test && npm run build
```
End-to-end browser smoke test (stack running, demo data loaded; needs Playwright — `npm i -g playwright` and `NODE_PATH=$(npm root -g)`, or a local install):
```bash
cd frontend && BASE_URL=http://localhost:5173 node e2e/smoke.mjs
```
The backend suite runs against a real PostgreSQL database (`above_test`), migrated with Alembic,
including multi-threaded concurrency tests. See [`docs/testing/TESTING.md`](docs/testing/TESTING.md).

## Repository layout
```
backend/    FastAPI app (app/core, models, modules/{erp_core,master_data,inventory,purchasing},
            ai_tools, agents, orchestrator, workers), Alembic migrations, pytest suite
frontend/   React + TypeScript + Vite + Tailwind; features/*, components/ui, e2e smoke test
docs/       architecture, database (ER diagram), api (+ openapi.json), agents, security,
            deployment, testing, roadmap, implementation log
docker-compose.yml · .env.example
```

## Documentation
* [System architecture](docs/architecture/SYSTEM_ARCHITECTURE.md)
* [Database schema & ER diagram](docs/database/DATABASE_SCHEMA.md)
* [API specification](docs/api/API_SPECIFICATION.md) · live docs at `/api/docs`
* [Agent architecture](docs/agents/AGENT_ARCHITECTURE.md)
* [Security architecture](docs/security/SECURITY_ARCHITECTURE.md)
* [Deployment](docs/deployment/DEPLOYMENT.md) · [Testing](docs/testing/TESTING.md)
* [Roadmap](docs/DEVELOPMENT_ROADMAP.md) · [Implementation log](docs/IMPLEMENTATION_LOG.md)
