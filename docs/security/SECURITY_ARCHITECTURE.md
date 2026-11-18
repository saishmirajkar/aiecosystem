# Security Architecture

## Authentication
* Passwords hashed with **Argon2id** (argon2-cffi defaults). Minimum length 10, must not equal
  the email. Hashes are rehashed on login if parameters change.
* Login creates a **server-side session**: 256-bit random token; only its SHA-256 hash is
  stored (`user_sessions`). Sessions expire (`SESSION_TTL_HOURS`, default 12) with idle
  timeout (`SESSION_IDLE_MINUTES`, default 120) and are revoked on logout, password change,
  password reset and user deactivation.
* Token transport: `HttpOnly`, `SameSite=Strict`, `Secure` (in production) cookie
  `above_session` for the SPA, or `Authorization: Bearer <token>` for API clients.
* Brute force: per-IP + per-email rate limiting on `/auth/login`; account lockout for 15
  minutes after 5 consecutive failures.
* Password reset: single-use token (hash stored, 30-minute expiry). Delivery is via the
  configured mail backend (console backend in development). The request endpoint always
  returns 202 to avoid account enumeration. Administrators can also set a temporary password.

## CSRF
Cookie-authenticated, state-changing requests (`POST/PUT/PATCH/DELETE`) must send
`X-Requested-With: above-web` (the SPA does). Browsers cannot add custom headers cross-site
without CORS preflight, and CORS only allows configured origins. Combined with
`SameSite=Strict` this blocks CSRF. Bearer-token requests are exempt (not ambient).

## Authorization (RBAC)
* Permissions are catalogue rows (`permissions.code`, e.g. `inventory.issue`) seeded from
  `app/core/permissions.py`. Roles map to permissions and are editable via the API
  (`roles.manage`). System roles cannot be deleted; `super_admin` always holds every
  permission.
* Every endpoint declares `Depends(require_permission("…"))`. The frontend hides controls
  the user cannot use, but **the backend is the enforcement point**.
* Optional warehouse scoping (`user_warehouse_access`): when present, reads and postings are
  restricted to those warehouses (enforced in services, used by AI tools too).
* A user cannot change their own roles or deactivate themselves.

## Sensitive actions & approvals
| Action | Control |
|---|---|
| Stock adjustments / reconciliations | Draft → submit → approve (`inventory.adjust.approve`) → post |
| Purchase orders | Submit → approve (`purchasing.po.approve`) before sending/receiving |
| Purchase requisitions | Approve (`purchasing.requisition.approve`) before conversion to PO |
| High-value movements | Issues/transfers above `inventory.high_value_threshold` require `inventory.high_value` |
| Over-receipt | `purchasing.over_receipt` + mandatory reason |
| Negative stock | Disabled by default; if enabled requires `inventory.negative_stock` |
| AI-proposed actions | `action_approvals` bound to params hash; approver needs target permission + `approvals.decide` |

## AI security
* The authenticated user id is resolved from the session by the backend and passed into a
  `ToolContext`; permissions are loaded from the database. Requests never carry role,
  company or permission claims that are trusted.
* Tool inputs are validated with strict Pydantic models (`extra="forbid"`, bounded lengths
  and ranges). No tool accepts SQL, filters as code, or arbitrary column names.
* Every tool call (including denied and invalid calls) is written to `agent_execution_logs`.
* Conversations are isolated per user: every conversation query filters by `user_id`;
  another user's conversation id returns 404.
* AI chat is rate limited per user. The model, effort and limits are configured server-side.
* `ANTHROPIC_API_KEY` lives only in backend environment variables; the frontend never sees
  it.

## Input & data protection
* SQL injection: SQLAlchemy parameter binding only; sort fields are whitelisted per endpoint.
* Input sanitisation: Pydantic types with max lengths; text rendered by React (escaped).
* File uploads: allow-list of extensions + content types (images, PDF, CSV), size limit
  (`MAX_UPLOAD_MB`), magic-byte sniffing for images/PDF, random storage names outside the
  web root, SHA-256 recorded.
* Security headers: `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`,
  `Content-Security-Policy` (API), HSTS when `ENVIRONMENT=production`.
* CORS: explicit origin list from `CORS_ORIGINS`, credentials allowed only for those origins.
* Error responses never include stack traces; unexpected errors log a request id.

## Audit
`audit_logs` records actor, action, entity, before/after changes, IP, user agent and request
id for: logins (success/failure), user/role/permission changes, settings changes, master
data changes, all postings, approvals and AI action executions.

## Secrets management
* All secrets via environment variables (`.env` not committed; `.env.example` has
  placeholders only). In production use Docker secrets or the host's secret manager.
* `SECRET_KEY` is required in production; the application refuses to start with the
  placeholder value when `ENVIRONMENT=production`.

## Known gaps (tracked in IMPLEMENTATION_LOG.md)
* Email delivery for password reset uses the console backend until an SMTP integration is
  configured.
* MFA is not yet implemented.
