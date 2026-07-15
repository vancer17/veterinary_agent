#!/usr/bin/env sh
set -eu

: "${DATABASE_URL:=postgresql://vet_agent:vet_agent@postgres:5432/vet_agent}"
: "${AUTO_MIGRATE:=true}"
: "${AUTO_SEED:=true}"
: "${APP_HOST:=0.0.0.0}"
: "${APP_PORT:=8000}"
: "${UVICORN_RELOAD:=true}"

export DATABASE_URL

python - <<'PY'
import os
import time

import psycopg

url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
deadline = time.time() + int(os.getenv("DB_WAIT_SECONDS", "60"))
last_error = None
while time.time() < deadline:
    try:
        with psycopg.connect(url, connect_timeout=3) as conn:
            conn.close()
        raise SystemExit(0)
    except Exception as exc:
        last_error = exc
        time.sleep(1)
raise SystemExit(f"database is not ready: {last_error}")
PY

if [ "$AUTO_MIGRATE" = "true" ]; then
  alembic upgrade head
fi

if [ "$AUTO_SEED" = "true" ]; then
  if [ "${SEED_WITH_EMBEDDINGS:-false}" = "true" ]; then
    python scripts/seed_database.py --with-embeddings
  else
    python scripts/seed_database.py
  fi
fi

args="main:app --host ${APP_HOST} --port ${APP_PORT}"
if [ "$UVICORN_RELOAD" = "true" ]; then
  args="$args --reload"
fi

exec uvicorn $args
