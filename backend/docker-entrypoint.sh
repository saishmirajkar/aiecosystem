#!/bin/sh
# Container roles: api (default) | worker | beat | migrate | <any command>
set -e

wait_for_db() {
  python -m app.wait_for_db
}

case "$1" in
  api)
    wait_for_db
    alembic upgrade head
    python -m app.cli seed
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${API_WORKERS:-2}" --proxy-headers --forwarded-allow-ips="*"
    ;;
  worker)
    wait_for_db
    exec celery -A app.workers.celery_app worker --loglevel=INFO --concurrency "${WORKER_CONCURRENCY:-2}"
    ;;
  beat)
    wait_for_db
    exec celery -A app.workers.celery_app beat --loglevel=INFO --schedule /tmp/celerybeat-schedule
    ;;
  migrate)
    wait_for_db
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
