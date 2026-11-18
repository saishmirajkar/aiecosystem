# Testing

| Layer | Tool | Location | Runs against |
|---|---|---|---|
| Backend unit / integration / API / DB / permission / concurrency | pytest + FastAPI TestClient | `backend/tests/` | Real PostgreSQL (`above_test`), Alembic-migrated, truncated per test |
| AI layer | pytest with a scripted fake Anthropic client | `backend/tests/test_ai.py` | Same DB; offline engine + Claude loop |
| Frontend components | Vitest + Testing Library (jsdom) | `frontend/src/test/` | Mocked `fetch` |
| End-to-end | Playwright script | `frontend/e2e/smoke.mjs` | A running stack (Vite or the nginx image) |

## Backend suites (93 tests)
| File | Covers |
|---|---|
| `test_foundation.py` | Setup once-only, login/logout, lockout, error envelope, cookie + CSRF, session probe, RBAC denials, user lifecycle & session revocation, role escalation guards, configurable roles, departments, company & settings validation, audit trail, password change/reset (single-use, never logged in production), notification prefs, upload validation, health, demo data consistency (valuation report total = dashboard total) |
| `test_config.py` | CORS parsing (comma/JSON), production secret guard, AI engine selection |
| `test_master_data.py` | Units & conversions (product-specific and inverse), product CRUD & validation, serial-tracking unit rule, product suppliers, location hierarchy paths, permissions, warehouse scoping |
| `test_inventory.py` | Opening & stock-in valuation (weighted average), issues valued at average, negative-stock prevention and policy, idempotent replay / key reuse / retry after failure, **20-thread concurrent issues never oversell**, append-only ledger trigger, transfer lifecycle with in-transit stock, transfer approval permission, approval-gated adjustments, reconciliation variance + discrepancy alert, reservations, damaged status, batch FEFO allocation, serial tracking, high-value control, warehouse-scoped posting, low-stock alerts & overview, read-only denial |
| `test_purchasing.py` | PO totals and no stock change, approval permissions, unapproved PO cannot be received, partial receipts with damaged/rejected quantities, over-receipt permission + reason, idempotent GRN confirm, requisition → approval → PO conversion, cancel/close rules, batch capture on GRN, overdue PO alert |
| `test_reports.py` | Catalogue, live figures, date filters, required filters, CSV export and formula-injection guard, permission |
| `test_ai.py` | Intent routing (incl. unconnected departments and briefings), agent registry, verified cards with sources, low-stock, today's receipts with supplier/PO, unknown product not invented, honest "not connected", draft → approval → record, rejection & cross-user permission, parameter-hash tamper detection, expiry, insufficient-stock draft refusal, server-side tool permission, AI access denial, conversation isolation & persistence, execution audit rows, tool validation & unknown tools, Claude loop (tool use, permitted tools only, invalid args, refusal, iteration budget, provider outage fallback, draft-only behaviour, prompt-injection text as data) |
| `test_workers.py` | Alert digest once per day and opt-out honoured |
| `test_migrations.py` | Migrated schema has **no drift** from the ORM models; ledger immutability trigger installed |

## Running
```bash
cd backend && . .venv/bin/activate && pytest -q
```
`tests/conftest.py` points `DATABASE_URL` at `above_test` (override with the env var), forces
`AI_ENGINE=offline` and removes any `ANTHROPIC_API_KEY`, so the suite never calls a paid API.

```bash
cd frontend && npm run typecheck && npm test
```
```bash
cd frontend && BASE_URL=http://localhost:5173 E2E_EMAIL=admin@demo.example.com E2E_PASSWORD='Admin-Passw0rd!' node e2e/smoke.mjs
```
The smoke script logs in, opens all 22 pages and checks each renders without an error state,
opens a product detail, asks the Command Center a question (expects a provenance-labelled card),
prepares a requisition draft and approves it from the chat, toggles the theme, fails on any
console error or 5xx response, and checks the mobile layout for horizontal overflow. Set
`SHOTS=<dir>` to capture screenshots.

## Live Claude check (manual, costs API credits)
With `ANTHROPIC_API_KEY` set in `backend/.env`, start the API and ask in the Command Center:
"How much steel do we have?", "What needs my attention today?", "Create a purchase request for
500 kg of STL-RB-20". Expect `engine: claude` in the response and tool cards identical to the
offline engine's figures.
