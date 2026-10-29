#!/usr/bin/env bash
# Back up the database, project data (working trees + git history, builds,
# proposals) and the collaboration state into one timestamped directory.
# usage: scripts/backup.sh [target-dir]      (run while the services are up; pg_dump is consistent)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PRISM_ENV_FILE:-$ROOT/.env}"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
DATA="${PRISM_DATA_DIR:-$ROOT/var/data}"
COLLAB="${COLLAB_DATA_DIR:-$ROOT/var/collab}"
OUT="${1:-$ROOT/var/backups}/prism-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
DB="${DATABASE_URL:-sqlite:///$ROOT/var/prism.db}"
if [[ "$DB" == postgresql* ]]; then
  PGURL="${DB/postgresql+psycopg/postgresql}"
  pg_dump --format=custom --file "$OUT/db.dump" "$PGURL"
else
  F="${DB#sqlite:///}"; python3 -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect(sys.argv[2]); s.backup(d)" "$F" "$OUT/prism.db"
fi
# Collab state first (it is the live text when collaboration is on), then data.
[ -d "$COLLAB" ] && tar -C "$(dirname "$COLLAB")" -czf "$OUT/collab.tar.gz" "$(basename "$COLLAB")"
tar -C "$(dirname "$DATA")" --exclude='*/tasks/*/attempt-*/work' -czf "$OUT/data.tar.gz" "$(basename "$DATA")"
( cd "$OUT" && sha256sum * > SHA256SUMS )
echo "backup written to $OUT"
# Retention: PRISM_BACKUP_KEEP_DAYS (default 14) — older prism-* backups in the same directory are removed.
KEEP="${PRISM_BACKUP_KEEP_DAYS:-14}"
find "$(dirname "$OUT")" -maxdepth 1 -type d -name 'prism-*' -mtime +"$KEEP" -exec rm -rf {} + 2>/dev/null || true
