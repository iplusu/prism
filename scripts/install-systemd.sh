#!/usr/bin/env bash
# Install Prism as systemd --user services (no root needed).
#   prism-db (optional, when PRISM_PG_DATA is set), prism-web, prism-worker, prism-collab,
#   prism-backup.timer (daily backup).
# Run `loginctl enable-linger $USER` once so the services run without a login session.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PRISM_ENV_FILE:-$ROOT/.env}"
set -a; . "$ENV_FILE"; set +a
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$UNIT_DIR"
PATH_LINE="Environment=PATH=$HOME/.local/bin:${PRISM_PG_BIN:+$PRISM_PG_BIN:}/usr/local/bin:/usr/bin:/bin"

unit() { # name description after-deps exec-arg extra
  cat > "$UNIT_DIR/$1.service" <<UNIT
[Unit]
Description=$2
After=network-online.target $3
Wants=$3

[Service]
Type=simple
WorkingDirectory=$ROOT
$PATH_LINE
ExecStart=$ROOT/scripts/prism-service.sh $4
Restart=always
RestartSec=5
$5

[Install]
WantedBy=default.target
UNIT
}

DB_DEP=""
if [ -n "${PRISM_PG_DATA:-}" ]; then
  unit prism-db "Prism PostgreSQL" "" db $'KillSignal=SIGINT\nTimeoutStopSec=60'
  DB_DEP="prism-db.service"
fi
# The collab (Yjs) service only exists when COLLAB_ENABLED is true; in solo mode web must not pull it in.
COLLAB_DEP=""
case "${COLLAB_ENABLED:-false}" in
  true|True|TRUE|1|yes)
    unit prism-collab "Prism collaboration service" "" collab "KillSignal=SIGTERM"
    COLLAB_DEP="prism-collab.service" ;;
  *)
    systemctl --user disable --now prism-collab.service 2>/dev/null || true
    rm -f "$UNIT_DIR/prism-collab.service" ;;
esac
unit prism-web "Prism web/API" "$DB_DEP $COLLAB_DEP" web "TimeoutStopSec=20"
unit prism-worker "Prism worker (compile + agent tasks)" "$DB_DEP" worker $'KillMode=mixed\nTimeoutStopSec=30'

cat > "$UNIT_DIR/prism-backup.service" <<UNIT
[Unit]
Description=Prism backup

[Service]
Type=oneshot
WorkingDirectory=$ROOT
$PATH_LINE
ExecStart=$ROOT/scripts/backup.sh ${PRISM_BACKUP_DIR:-$ROOT/var/backups}
UNIT
cat > "$UNIT_DIR/prism-backup.timer" <<UNIT
[Unit]
Description=Daily Prism backup

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable ${DB_DEP:+prism-db.service} ${COLLAB_DEP} prism-web.service prism-worker.service prism-backup.timer
echo "installed units in $UNIT_DIR"
echo "start:  systemctl --user start ${DB_DEP:+prism-db} ${COLLAB_DEP:+prism-collab} prism-web prism-worker prism-backup.timer"
echo "logs:   journalctl --user -u prism-web -f"
