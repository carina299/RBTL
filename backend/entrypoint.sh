#!/usr/bin/env sh
# Container entrypoint, used by every deployment mode (local / VPS / Render) that
# runs the backend via Docker. Applies pending Alembic migrations, then starts the
# app — so a fresh Postgres is never left with the wrong schema.
set -e

# /data is a mounted volume/disk (empty on first boot) — make sure the paths the
# app writes to actually exist before it starts.
mkdir -p "${RELAY_UPLOAD_DIR:-/data/uploads}"
mkdir -p "$(dirname "${RELAY_DB:-/data/relay.db}")"

# Render (and most PaaS providers) inject a managed Postgres connection as
# DATABASE_URL, scheme "postgres://" — SQLAlchemy's psycopg3 driver needs
# "postgresql+psycopg://". docker-compose / a VPS set RELAY_DATABASE_URL
# directly instead, so this only kicks in when that's unset.
if [ -z "$RELAY_DATABASE_URL" ] && [ -n "$DATABASE_URL" ]; then
  RELAY_DATABASE_URL=$(echo "$DATABASE_URL" | sed -E 's#^postgres(ql)?://#postgresql+psycopg://#')
  export RELAY_DATABASE_URL
  echo "[entrypoint] derived RELAY_DATABASE_URL from DATABASE_URL"
fi

echo "[entrypoint] running migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn on 0.0.0.0:${PORT:-3011}"
exec uvicorn app:app --host 0.0.0.0 --port "${PORT:-3011}"
