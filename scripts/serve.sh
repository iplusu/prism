#!/usr/bin/env bash
# Run Prism on a server WITHOUT Docker: built frontend served by the backend on
# $PRISM_HOST:$PRISM_PORT (default 0.0.0.0:8000), one worker, collab service.
# Put a TLS reverse proxy in front (see deploy/nginx.conf) for anything but a trusted LAN.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] || { echo "create .env from .env.example first"; exit 1; }
set -a; . ./.env; set +a
: "${PRISM_SECRET_KEY:?set PRISM_SECRET_KEY in .env}"
# PRISM_HOST=tailscale binds only to this node's Tailscale address (tailnet-only access).
if [ "${PRISM_HOST:-}" = "tailscale" ]; then
  PRISM_HOST="$(tailscale ip -4 | head -1)" || { echo "tailscale is not running"; exit 1; }
  export PRISM_HOST
fi
PRISM_HOST="${PRISM_HOST:-0.0.0.0}"; PRISM_PORT="${PRISM_PORT:-8000}"
CONNECT_HOST="$PRISM_HOST"; [ "$CONNECT_HOST" = "0.0.0.0" ] && CONNECT_HOST=127.0.0.1
export COLLAB_INTERNAL_URL="${COLLAB_INTERNAL_URL:-http://127.0.0.1:1234}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://$CONNECT_HOST:$PRISM_PORT}"
[ -d frontend/dist ] || (cd frontend && npm ci && npm run build)
if [[ "${DATABASE_URL:-}" == postgresql* ]]; then (cd backend && uv run alembic upgrade head); fi
mkdir -p var/log
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
(cd backend && exec uv run uvicorn app.main:app --host "$PRISM_HOST" --port "$PRISM_PORT" --proxy-headers) >>var/log/web.log 2>&1 & pids+=($!)
(cd backend && exec uv run python -m app.worker) >>var/log/worker.log 2>&1 & pids+=($!)
if [ "${COLLAB_ENABLED:-false}" = "true" ]; then (cd collab && exec node server.mjs) >>var/log/collab.log 2>&1 & pids+=($!); fi
echo "Prism: http://$PRISM_HOST:$PRISM_PORT  (logs in var/log/)"
wait -n
