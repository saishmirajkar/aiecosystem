# Implementation Log

_Last updated: 2026-11-18 — initial release (Phase 1, steps 1–10)._

## Step 1 — Environment findings
* Repository was empty (no commits); built from scratch as a monorepo (`backend/`, `frontend/`, `docs/`).
* Available: Python 3.13, Node 22, PostgreSQL 16, Redis 7, Docker 29, package registries.
* No `ANTHROPIC_API_KEY` in the environment → Claude engine built and tested with a scripted
  client; the offline engine keeps the Command Center usable without a key.
* Outbound HTTPS from containers goes through an intercepting proxy in the build sandbox, so Docker
  image builds were verified from a scratch copy that adds the proxy CA (see Test results).

## Completed
### ERP foundation
- First-run setup (API wizard + CLI), single-company identity per deployment, company settings incl. logo, currency, timezone, fiscal year, formats.
- Users (create, edit, activate/deactivate, admin password set), profile, change password, password reset (single-use, 30-min tokens), lockout after 5 failures.
- Server-side sessions (hashed opaque tokens, TTL + idle timeout, revocation), HttpOnly SameSite=Strict cookie + CSRF header, Bearer support, non-erroring session probe.
- RBAC: 37 permissions, 8 seeded roles, editable roles/permissions, escalation guards, warehouse scoping.
- Departments, system settings with typed validation, audit log (UI + API), notifications & preferences, secure uploads.

### Master data
- Units, global/product-specific conversions, categories (cycle-safe), products (6 types, levels, costs, batch/expiry/serial flags, image), product suppliers, suppliers, warehouses, hierarchical locations (auto MAIN + TRANSIT).

### Inventory
- Single posting engine: deterministic row locks, append-only ledger (DB trigger), `balance_after` per line, weighted-average valuation, negative-stock policy (setting + permission), high-value control, idempotency keys.
- Opening balances, stock-in, issues (explicit location or FEFO auto-allocation), status changes (damaged/returned), reservations, transfers (draft → approval → dispatch/in-transit → receive), adjustments & cycle counts (approval before posting), batches, serials.
- 8 live alert types; dashboard overview (all figures from records); valuation by product/warehouse/category; 13 reports with filters, pagination and safe CSV.

### Purchasing foundation
- Requisitions (draft → approval → conversion to PO), purchase orders (draft → approval → sent → partially/fully received → closed; cancel rules), goods receipts (partial, damaged, rejected with reason, authorised over-receipt with reason, batches/serials, delivery documents). Only confirmed GRNs change stock.

### AI
- Tool framework (validation, server-side permission checks, savepoints, audit of every call); 15 Inventory Agent tools (12 read, 3 draft).
- Claude engine: Messages API manual tool loop, `claude-opus-5-5` default, adaptive thinking, configurable effort, prompt caching, server-side refusal fallbacks, iteration/tool budgets, typed error handling → automatic offline fallback.
- Offline engine: deterministic intents over the same tools.
- Main Agent: registry (synced to DB), rule-based intent router with unconnected-department detection and briefings, dispatcher, response consolidation with provenance, per-user persistent conversations.
- Approval manager: parameter-hash binding, TTL/expiry, single use, decision rules, execution through business services, status reflected live in chat.

### Frontend
- Shell with permission-aware navigation (no CRM), global AI drawer (⌘K), light/dark tokens, responsive layout, bundled fonts.
- Dashboard; products (list, form, detail with image, stock by location, history, suppliers); stock overview; warehouses & locations; movements with posting forms; transfers; adjustments & counts; goods receipts with documents; alerts; reports; suppliers; requisitions; purchase orders with documents; approvals; company, users, roles & permissions, departments, units/conversions/categories, system settings, audit log, profile & notification preferences; AI Command Center.

### Engineering
- Alembic migrations `0001` (schema + ledger trigger) and `0002` (deferred foreign keys), upgrade/downgrade verified, drift-tested, Docker images + compose (API, worker, beat, nginx, Postgres, Redis), CLI, OpenAPI export (`docs/api/openapi.json`, 102 paths), CI workflow, documentation set.

## Test results (executed 2026-11-18)
| Check | Result |
|---|---|
| Backend `pytest -q` (real PostgreSQL) | **93 passed** |
| Alembic `upgrade head` → `downgrade base` → `upgrade head` | passed |
| Model ↔ migration drift check (now a test) | passed after migration 0002 |
| Frontend `npm run typecheck` (strict) | passed |
| Frontend `npm test` (Vitest) | **11 passed** |
| Frontend production build | passed (largest chunk 340 kB, charts) |
| Playwright smoke vs Vite dev stack (22 pages, AI question, draft → approve, theme, mobile overflow, no console errors/5xx) | **all checks passed** |
| Playwright smoke vs **Docker Compose stack** (nginx + production build + API container) | **all checks passed** |
| Celery worker/beat in containers; alert digest task executed | passed (7 digests created) |
| Live Claude API call | **not executed** — no API key available in this environment |
| GitHub Actions CI | workflow added; **not yet observed running** |
| Docker build with the committed Dockerfiles on an unrestricted network | not executed here; verified with an added proxy-CA layer only |

Defects found and fixed during validation: three `use_alter` foreign keys missing from the initial migration (fixed by migration `0002`, guarded by a drift test); `CORS_ORIGINS` comma format crashed settings parsing in containers; Celery missing an undeclared `packaging` dependency; nginx location blocks dropped security headers; first-run setup race; reset tokens would have been logged in production; demo loader over-consumed stock (caught by the ledger's negative-stock guard).

## Architecture decisions (summary — details in docs/)
1. Own ERP core; one deployment and database per customer (single `companies` row as identity, not tenancy).
2. Sync SQLAlchemy + explicit row locking; services never commit, routers/idempotency wrapper do.
3. Header/line ledger (`inventory_transactions` / `stock_movements`) as the source of history; balances updated only by the posting engine.
4. Weighted-average valuation per product, company-wide; `valuation_method` column reserved for FIFO.
5. AI agent loop hosted in-backend (Messages API) so tool calls inherit RBAC, audit and approvals; draft tools create approvals, never records.
6. Rule-based routing for transparency while only one agent exists.
7. Decimal values serialised as strings across the API.
8. UI kit owned in-repo (shadcn model) on Radix primitives; native `<select>` for accessibility and fewer dependencies.

## Known limitations / pending
- **Email/SMTP** not integrated: password-reset links are logged only outside production; alert emails are logged, not sent.
- **MFA** not implemented.
- **FIFO** valuation not implemented (weighted average only).
- Transfers are received in full; partial receipt at destination with discrepancy capture is pending.
- Serial-tracked products cannot be adjusted through adjustments (use stock-in/issue with serials).
- A reservation is held against a single balance row (one location/batch).
- Report JSON pagination happens in memory (max 50 000 rows per report).
- No segregation-of-duties policy (approver ≠ requester) yet; permissions decide.
- AI responses are not streamed (single response per request); conversation history passes text only (cards are not replayed to the model).
- Rate limiting falls back to per-process counters if Redis is unavailable.
- Frontend tests cover core components and flows (11 tests); broader component coverage would be valuable.

## Next development tasks
1. Run a live Claude evaluation with a customer key: the TESTING.md checklist plus prompt-injection probes; tune `ANTHROPIC_EFFORT` per route.
2. SMTP integration + email alert digests; then MFA for administrators.
3. Watch the first GitHub Actions run; add the Playwright smoke as a CI job against `docker compose`.
4. Partial transfer receipt with discrepancy capture; segregation-of-duties setting.
5. FIFO cost layers behind `valuation_method`.
6. Streaming (SSE) responses in the Command Center.
7. Next department: Procurement (full) agent, using the extension path in SYSTEM_ARCHITECTURE §7.

---

## Branch `client/company-x` — Company X production & job-work flow (2026-11-18)
Scope and decisions: `docs/clients/COMPANY_X.md`.

**Built**
- Backend module `app/modules/production`: sites, structures, orders with BOM expansion and scheduling, stage editing, challans, vendor receipts (split, direct, rework, scrap), in-house completions, costing at the last stage, positions, vendor workload, delays, summary, customers, dispatch notes.
- Migration `0003` with a guarded downgrade.
- Production AI Agent (6 tools, one approval-gated action) registered with the Main Agent. Multi-agent routing now runs every agent scoring at least half the top score.
- Frontend: Production and Sales & Dispatch menus, order tracker / timeline / stage dialogs, vendor workload, challans (printable), structure editor, plants & vendors, customers, dispatch notes, Overview production strip.
- Demo: Plant A, 4 vendors, gearbox GB-100 structures, 3 orders (completed / in flight / planned).

**Foundation fixes made on `main` and merged here**
- Quantity text in stock errors ("4E+1" → "40").
- Test database reset per session, so branches with different migrations share one test DB.

**Test results (executed 2026-11-18)**
| Check | Result |
|---|---|
| Backend `pytest` (PostgreSQL 16, Redis) | 110 passed |
| Frontend `tsc` typecheck (app + node configs) | passed |
| Frontend `vitest` | 11 passed |
| Frontend production build | passed |
| Playwright smoke against a live API + Vite with fresh demo data | all checks passed (desktop + mobile), including opening the order tracker, releasing MO-000003, issuing a challan from the UI and rendering the printable challan |
| Docker image rebuild for this branch | not executed |

**Known limitations**
- ITC-04 export, vendor bills, capacity planning and serial-level traceability are not built.
- A plant shortage is flagged on the order but does not raise a transfer request automatically.
- Demo activity is compressed into a few days, so the demo's vendor turnaround figures are short.

## Workforce & Shift Management (foundation module, branch `feature/workforce`, 2026-11-24)
Design, decisions and data model: `docs/workforce/WORKFORCE_ARCHITECTURE.md`.

**Built**
- Backend module `app/modules/workforce`:
  - people, teams, skills
  - shift templates, rotations, dated shifts with positions, generation and copy-week
  - leave with balances and approval
  - vacancy → overtime opportunity
  - atomic first-come-first-served claim
  - withdrawals and conflicts, manual assignment with reason
  - attendance with corrections and CSV import
  - overtime calculation from attendance, approval, payroll export (CSV/XLSX)
  - notifications with dedupe and delivery log; email through SMTP when configured; push/WhatsApp report "not connected"
  - dashboards and reports
  - Celery jobs: expiry, absence marking, email delivery
- Migration `0004`, with tables, the `btree_gist` no-overlap constraint and a one-active-assignment-per-position index.
- Workforce Agent (15 tools, 2 approval-gated actions), registered with the Main Agent, plus `POST /ai/workforce/chat`.
- Frontend:
  - mobile-first employee app (bottom tabs, one-tap claim)
  - manager screens: dashboard, calendar (day/week/month), leave, overtime board with live claims log, attendance, overtime records
  - HR screens: employees, setup, payroll, reports
- Demo: 17 employees in 4 teams, staggered rotations, a week of attendance, leave in every state, open/filled/expired opportunities, an open conflict, a payroll batch.

**Foundation changes made here (go to `main` with the module)**
- Sign-in accepts an employee code.
- `/auth/me` returns `employee_id`.
- Push/WhatsApp preference flags and a notification dedupe key.
- SMTP settings.
- Seeding grants a newly added module's permissions to existing system roles.
- Idempotent replay of business-decided status codes.
- `time` values in audit JSON.
- Stricter rule for adding a secondary agent.
- Frontend login keeps the session query bound (fixes a race after sign-in).

**Test results (executed 2026-11-24)**
| Check | Result |
|---|---|
| Backend `pytest` (PostgreSQL 16, Redis), whole suite | 124 passed |
| Workforce: 10 scenarios from the brief + rules, privacy, permissions, idempotency | 25 passed |
| Concurrency: 9 threads claiming a 1-position offer through the API; 9 threads with separate DB sessions claiming a 3-position offer | exactly 1 and exactly 3 winners |
| Workforce Agent | 6 passed |
| Migration 0004 upgrade / downgrade / re-upgrade, model drift check | passed |
| Frontend typecheck, `vitest` (16 tests), production build | passed |
| Playwright smoke on a live API + Vite with fresh demo data | 64 checks passed, including: employee applies for leave on a phone (employee-code login) → manager approves → opportunity published → eligible colleague notified without the absent colleague's name → claims on a phone → shift in winner's schedule → later claim refused → manager sees claims log → overtime approved → HR payroll export |
| Docker image rebuild for this branch | not executed |

**Known limitations**
- Push and WhatsApp are interfaces only (report "not connected"); email sends only with SMTP configured.
- No biometric/RFID device API yet (CSV import covers terminal exports).
- No full payroll processing (export only), by design for this phase.
- Real-time updates poll every 5–30 s; server-sent events are not implemented.
- A leave spanning two calendar years counts against the start year's balance.
- Not merged: waiting for acceptance before `feature/workforce` → `main` → `client/company-x` (migration heads 0003/0004 will need a merge revision on the client branch).

---

## Workforce merged into `client/company-x` (2026-11-25)
`feature/workforce` was merged directly into this branch at the user's request; `main` does not have it yet.
- Migration `0005` joins the two heads (`0003` production flow, `0004` workforce); it has no operations.
- Three department agents now run under the Main Agent: Inventory, Production and Workforce. The stricter
  secondary-agent rule from the workforce branch applies to all three.
- The module switcher's **ERP** module holds Inventory, Purchasing, Production and Sales & Dispatch; **Workforce** holds My work
  and Workforce. AI Command Center, Approvals and Settings are shared.
- The demo loads both scenarios: the gearbox production flow and the shop-floor workforce.
