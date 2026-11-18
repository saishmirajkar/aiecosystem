# Development Roadmap

## Phase 1 — Initial release (this repository)
| Step | Deliverable | Exit criteria |
|---|---|---|
| 1 | Environment inspection | Findings recorded in IMPLEMENTATION_LOG |
| 2 | Architecture & design docs | This `docs/` tree |
| 3 | Foundation: config, DB, migrations, auth, sessions, RBAC, company, departments, audit, API conventions | Auth + permission tests green |
| 4 | Master data: units, conversions, categories, products, suppliers, warehouses, locations | CRUD + permission tests green |
| 5 | Inventory engine: ledger, balances, opening, receipts, issues, status changes, transfers (in-transit), adjustments & reconciliation, reservations, batches/serials, valuation, alerts, reports | Business scenario + concurrency + idempotency tests green |
| 6 | Purchasing foundation: requisitions, POs, approvals, GRN with partial/over-receipt rules | Stock changes only on GRN confirm (tested) |
| 7 | Inventory AI Agent: tool framework, 15 tools, Claude + offline engines | Tool validation/permission/audit tests green |
| 8 | Main Agent: registry, router, dispatcher, context, consolidation, conversations, approvals | End-to-end chat + approval tests green |
| 9 | Frontend: shell, dashboard, inventory, purchasing, Command Center, settings | Typecheck, unit tests, production build, browser smoke test |
| 10 | Validation: full test suites, migration check, security review, Docker build | Recorded in IMPLEMENTATION_LOG |

## Phase 2 — Hardening (next)
* SMTP/email integration for password reset and alert digests.
* MFA (TOTP) for administrators.
* FIFO valuation layers (`valuation_method = fifo`).
* Partial receipt at transfer destination with discrepancy capture.
* Approval segregation-of-duties policy (approver ≠ requester) as a setting.
* Scheduled report delivery; PDF exports.
* Streaming AI responses (SSE) in the Command Center.

## Phase 3 — Additional departments (each = module + agent)
Procurement (full), Production, Quality, Maintenance, Logistics & Dispatch, Finance, HR,
CRM & Sales — added through the extension path in `SYSTEM_ARCHITECTURE.md §7`, with
multi-agent consolidation in the Main Agent.

## Dependencies and risks
* Inventory postings depend on master data; purchasing GRN depends on the inventory engine;
  AI tools depend on both. Steps are therefore strictly ordered 3 → 8.
* Claude availability is a runtime dependency only; the offline engine keeps the system usable.
