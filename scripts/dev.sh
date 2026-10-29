#!/usr/bin/env bash
# Development: backend (reload), worker, collab service and Vite dev server.
# Open http://localhost:5173  (API on :8000, collab on :1234, proxied by Vite).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -f .env ] && { set -a; . ./.env; set +a; }
export PRISM_DATA_DIR="${PRISM_DATA_DIR:-$ROOT/var/data}"
export COLLAB_DATA_DIR="${COLLAB_DATA_DIR:-$ROOT/var/collab}"
export COLLAB_INTERNAL_URL="${COLLAB_INTERNAL_URL:-http://127.0.0.1:1234}"
export BACKEND_INTERNAL_URL="${BACKEND_INTERNAL_URL:-http://127.0.0.1:8000}"
export COLLAB_HOST="${COLLAB_HOST:-127.0.0.1}"
if [ "${COLLAB_ENABLED:-false}" = "true" ] && [ -z "${COLLAB_SHARED_SECRET:-}" ]; then
  export COLLAB_SHARED_SECRET="dev-$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
mkdir -p var/log
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
(cd backend && exec uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload) & pids+=($!)
(cd backend && exec uv run python -m app.worker) & pids+=($!)
if [ "${COLLAB_ENABLED:-false}" = "true" ]; then (cd collab && exec node server.mjs) & pids+=($!); fi
(cd frontend && exec npm run dev -- --host 127.0.0.1) & pids+=($!)
echo "Prism dev: http://localhost:5173"
wait -n
