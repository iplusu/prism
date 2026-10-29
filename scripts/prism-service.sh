#!/usr/bin/env bash
# Runs one Prism process in the foreground; used by the systemd units in deploy/systemd/.
# usage: scripts/prism-service.sh web|worker|collab|db
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
ENV_FILE="${PRISM_ENV_FILE:-$ROOT/.env}"
set -a; . "$ENV_FILE"; set +a

if [ "${PRISM_HOST:-}" = "tailscale" ]; then
  PRISM_HOST="$(tailscale ip -4 2>/dev/null | head -1)"
  [ -n "$PRISM_HOST" ] || { echo "tailscale address not available yet" >&2; exit 75; }
fi
PRISM_HOST="${PRISM_HOST:-0.0.0.0}"; PRISM_PORT="${PRISM_PORT:-8000}"
CONNECT_HOST="$PRISM_HOST"; [ "$CONNECT_HOST" = "0.0.0.0" ] && CONNECT_HOST=127.0.0.1
export COLLAB_INTERNAL_URL="${COLLAB_INTERNAL_URL:-http://127.0.0.1:1234}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://$CONNECT_HOST:$PRISM_PORT}"

case "${1:-}" in
  db)
    : "${PRISM_PG_BIN:?set PRISM_PG_BIN}" "${PRISM_PG_DATA:?set PRISM_PG_DATA}"
    exec "$PRISM_PG_BIN/postgres" -D "$PRISM_PG_DATA"
    ;;
  web)
    cd backend
    if [[ "${DATABASE_URL:-}" == postgresql* ]]; then uv run --frozen alembic upgrade head; fi
    exec uv run --frozen uvicorn app.main:app --host "$PRISM_HOST" --port "$PRISM_PORT" --proxy-headers
    ;;
  worker)
    cd backend
    exec uv run --frozen python -m app.worker ${PRISM_WORKER_KINDS:+--kinds "$PRISM_WORKER_KINDS"}
    ;;
  collab)
    cd collab
    exec node server.mjs
    ;;
  *)
    echo "usage: $0 web|worker|collab|db" >&2; exit 64
    ;;
esac
