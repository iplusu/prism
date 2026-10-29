#!/usr/bin/env bash
# Restore a backup made by backup.sh. STOP all Prism services first.
# usage: scripts/restore.sh <backup-dir>
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${1:?backup directory}"
ENV_FILE="${PRISM_ENV_FILE:-$ROOT/.env}"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
( cd "$SRC" && sha256sum -c SHA256SUMS )
DATA="${PRISM_DATA_DIR:-$ROOT/var/data}"
COLLAB="${COLLAB_DATA_DIR:-$ROOT/var/collab}"
DB="${DATABASE_URL:-sqlite:///$ROOT/var/prism.db}"
for d in "$DATA" "$COLLAB"; do [ -e "$d" ] && mv "$d" "$d.before-restore-$(date +%s)"; done
mkdir -p "$(dirname "$DATA")" "$(dirname "$COLLAB")"
tar -C "$(dirname "$DATA")" -xzf "$SRC/data.tar.gz"
[ -f "$SRC/collab.tar.gz" ] && tar -C "$(dirname "$COLLAB")" -xzf "$SRC/collab.tar.gz"
if [[ "$DB" == postgresql* ]]; then
  pg_restore --clean --if-exists --no-owner --dbname "${DB/postgresql+psycopg/postgresql}" "$SRC/db.dump"
else
  F="${DB#sqlite:///}"; TS=$(date +%s)
  # Move the WAL/SHM side files too: leftovers would be replayed over the restored database.
  for x in "$F" "$F-wal" "$F-shm"; do [ -e "$x" ] && mv "$x" "$x.before-restore-$TS"; done
  cp "$SRC/prism.db" "$F"
fi
echo "restored from $SRC — start the services again"
