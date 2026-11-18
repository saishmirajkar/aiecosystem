# Deployment Guide

Each customer gets an **independent deployment**: its own server (or VM / namespace), its own
PostgreSQL database, Redis, `.env`, API key and backups. Nothing is shared between customers.

## 1. Requirements
* Docker Engine 24+ with Compose v2.
* A DNS name and a TLS-terminating reverse proxy or load balancer in front of port `8080`
  (production mode sets `Secure` cookies, so the app must be served over HTTPS).
* Outbound HTTPS to `api.anthropic.com` if the Claude engine is enabled (optional).

## 2. Configure
```bash
cp .env.example .env
```
Edit `.env`:
| Variable | What to set |
|---|---|
| `ENVIRONMENT` | `production` (use `demo` only for local HTTP evaluation) |
| `SECRET_KEY` | ≥ 32 random characters from your secret manager — the app refuses to start in production otherwise |
| `POSTGRES_PASSWORD` | Strong, unique database password (compose refuses to start without it) |
| `CORS_ORIGINS`, `FRONTEND_URL` | The public HTTPS origin, e.g. `https://erp.customer.example` |
| `ANTHROPIC_API_KEY` | Customer's key, or empty for the offline engine |

Prefer Docker secrets / your orchestrator's secret store over plain `.env` files on shared hosts.

## 3. Start
```bash
docker compose up -d --build
```
Services: `db` (PostgreSQL 16), `redis`, `backend` (FastAPI; on start it waits for the DB,
runs `alembic upgrade head` and seeds reference data), `worker` (Celery), `beat` (Celery
scheduler), `frontend` (nginx serving the SPA and proxying `/api`).

Check:
```bash
docker compose ps
curl -s http://localhost:8080/api/v1/health
```

## 4. Initialise the installation (once)
Open the site: the first-run wizard asks for the company and the Super Administrator.
Or from the CLI:
```bash
docker compose exec -e ADMIN_PASSWORD='…' backend python -m app.cli bootstrap --company "Customer Forge Ltd" --admin-email admin@customer.example --admin-name "Plant Head"
```
Add `--demo` only for demo/evaluation installations (synthetic data, flagged in the UI).

## 5. Operations
| Task | Command |
|---|---|
| Logs | `docker compose logs -f backend worker` |
| Apply migrations manually | `docker compose run --rm backend migrate` |
| Backup DB | `docker compose exec db pg_dump -U above -Fc above > above-$(date +%F).dump` |
| Restore DB | `docker compose exec -T db pg_restore -U above -d above --clean < file.dump` |
| Backup uploads | volume `above-industrial_uploads` |
| Upgrade | `git pull && docker compose up -d --build` (migrations run on start) |
| Scale API | `API_WORKERS` env (default 2) |

## 6. Scheduled jobs (Celery beat, UTC)
* `generate_alert_notifications` — daily 01:30: one in-app alert digest per user, honouring
  notification preferences and warehouse scope.
* `expire_approvals` — hourly: marks AI action approvals past their TTL as expired.

## 7. Building behind an intercepting HTTPS proxy
If your build network re-signs TLS (corporate proxy), the image builds need the proxy CA:
pass `HTTP(S)_PROXY` build args and add the CA in an early layer (`PIP_CERT` for pip,
`NODE_EXTRA_CA_CERTS`/`npm_config_cafile` for npm). The committed Dockerfiles assume direct
internet access and contain no environment-specific certificates.

## 8. Hardening checklist
- [ ] TLS in front of nginx; HSTS enabled at the edge (the API adds HSTS in production).
- [ ] `ENVIRONMENT=production`, strong `SECRET_KEY` and DB password, `.env` readable only by root.
- [ ] Database port not published (default compose does not publish it).
- [ ] Regular `pg_dump` backups tested by restore.
- [ ] Change the Super Administrator password after handover; create named admin accounts.
- [ ] Configure SMTP before relying on self-service password reset (console backend logs nothing usable in production).
