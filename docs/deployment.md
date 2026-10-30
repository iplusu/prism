# Deployment

## Option A — Docker Compose (recommended for the lab server)

```bash
git clone <this repo> /srv/prism-src && cd /srv/prism-src
cp .env.example .env
#  set: PRISM_SECRET_KEY, PRISM_ADMIN_USERNAME/PASSWORD, COLLAB_SHARED_SECRET,
#       LLM_BASE_URL, LLM_MODEL (+ LLM_API_KEY), PRISM_COOKIE_SECURE=true behind TLS
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)" >> .env
docker compose build
docker compose up -d
docker compose logs -f web
```

Open `http://<server>:8080` (change with `PRISM_HTTP_PORT`). The `web` container runs `alembic upgrade head` on start.

> Not executed on the development host (no Docker available there). The application and its isolation were verified without containers (Option B); treat the first Compose start as an acceptance test and check `docker compose exec agent-worker python -m app.cli doctor`.

Services and boundaries:

| Service | Networks | Notes |
|---|---|---|
| `db` | internal | PostgreSQL 17, volume `pgdata` |
| `web` | internal, egress | API + frontend. No TeX, no CLIs, no Docker socket, read-only root, `cap_drop: ALL`. Egress only so *Test connection* can reach the LLM server. |
| `compile-worker` | internal (no internet) | `--kinds compile,verify_proposal,apply_proposal` |
| `agent-worker` | internal, egress | `--kinds agent`; reaches the LLM server and tool vendor APIs; tool logins in the data volume |
| `collab` | internal | Yjs service, volume `collabdata`, read-only root |
| `proxy` | internal, public | nginx; `/collab/` → collab (WebSocket), SSE unbuffered, `/internal/` blocked |

The workers need unprivileged user namespaces for bubblewrap, hence `security_opt: seccomp=unconfined, apparmor=unconfined` **on the workers only**. If your Docker host forbids that, either provide a seccomp profile that allows `unshare`/`clone` with namespace flags, or set `COMPILE_SANDBOX=none` + `ALLOW_UNSANDBOXED_COMPILE=true` — builds will then be labelled `isolation: none` in the UI and the container is the only boundary.

The LLM server (e.g. a5000) must be reachable from `web` and `agent-worker`. Check from inside: `docker compose exec web python -m app.cli llm-test`.

### Tool logins in Compose

```bash
docker compose exec -it agent-worker sh -c 'CODEX_HOME=/srv/prism/data/tool-homes/codex/.codex codex login --device-auth'
docker compose exec -it agent-worker sh -c 'CLAUDE_CONFIG_DIR=/srv/prism/data/tool-homes/claude/.claude claude auth login'
```

Or set `CODEX_OPENAI_API_KEY`, `CLAUDE_ANTHROPIC_API_KEY`, `AGY_GEMINI_API_KEY` in `.env`. Antigravity is not bundled in the image; install it per the vendor docs (add a `RUN` step to `deploy/backend.Dockerfile`).

## Option B — without Docker (verified on the development host)

Requirements (all installable without root):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh                                   # uv (+ Python 3.12 via uv)
curl -sL https://nodejs.org/dist/v24.22.2/node-v24.22.2-linux-x64.tar.xz | tar xJ -C ~/.local/opt   # Node 24
curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh                           # TeX Live (TinyTeX)
~/.TinyTeX/bin/x86_64-linux/tlmgr install latexmk biber cleveref caption booktabs natbib biblatex synctex   # synctex: PDF <-> source jumps
# extra packages the official templates (ACL, CVPR, NeurIPS, ICML, arXiv) need; TinyTeX does not ship them:
~/.TinyTeX/bin/x86_64-linux/tlmgr install environ fancyhdr lineno microtype silence enumitem eso-pic units upquote \
    forloop lipsum mwe sttools currfile doi mathtools grfext todonotes courier
# bubblewrap and git: from the OS (apt install bubblewrap git) — usually already present
```

Ubuntu ≥ 24.04 restricts unprivileged user namespaces through AppArmor; the distribution's `bwrap` package ships a profile that allows it. Check with `bwrap --unshare-all --ro-bind / / true && echo ok`.

```bash
make install
cp .env.example .env    # DATABASE_URL, PRISM_SECRET_KEY, TEX_BIN_DIR=~/.TinyTeX/bin/x86_64-linux, ...
cd frontend && npm run build && cd ..
./scripts/serve.sh      # web :8000 + worker + collab :1234, logs in var/log/
```

For a permanent install use the systemd user units: `loginctl enable-linger $USER`, then `scripts/install-systemd.sh` (units `prism-db` (optional), `prism-collab`, `prism-web`, `prism-worker`, `prism-backup.timer`; each runs `scripts/prism-service.sh <role>`). The web process proxies `/collab/*` to the collab service, so only one port is needed. Ways to expose that port:

* **Tailscale, HTTPS without a port (used by the current deployment)**: enable MagicDNS and HTTPS certificates for the tailnet, run `sudo tailscale set --operator=$USER` once, set `PRISM_HOST=127.0.0.1` and `PRISM_COOKIE_SECURE=true` in `.env`, restart `prism-web prism-collab prism-worker`, then `tailscale serve --bg --https 443 http://127.0.0.1:8000`. The serve config persists across reboots (`tailscale serve status`; remove with `tailscale serve --https=443 off`). Address: `https://<machine>.<tailnet>.ts.net/`, reachable from the tailnet only. Secure cookies mean plain-HTTP addresses cannot log in, so do not expose an HTTP port as well.
* **Tailscale, plain HTTP (no sudo)**: `PRISM_HOST=tailscale` binds the web process to the node's Tailscale IPv4 address only; address `http://<machine>.<tailnet>.ts.net:8000`, `PRISM_COOKIE_SECURE=false`. Unprivileged users cannot bind ports below 1024, so the port stays in the URL.
* **nginx/Caddy with TLS**: use `deploy/nginx.conf` as the template (replace the upstreams with `127.0.0.1:8000` and `127.0.0.1:1234`) and set `PRISM_COOKIE_SECURE=true`.

## PostgreSQL

`DATABASE_URL=postgresql+psycopg://user:pass@host:5432/prism`, then `make migrate` (`alembic upgrade head`). The full backend test suite can be run against a scratch database: `make test-pg PG=postgresql+psycopg://user@host/prism_test` (it drops and recreates all tables there).

## Operations

* **Health**: `GET /api/health`, collab `GET :1234/health`, `python -m app.cli doctor`.
* **Workers**: several workers can run against PostgreSQL. Jobs whose worker stops heart-beating for 2 minutes are marked failed (not silently re-run) and the task shows the error.
* **Backups**: `scripts/backup.sh` (pg_dump custom format or SQLite online backup, `data.tar.gz` without task work copies, `collab.tar.gz`, `SHA256SUMS`). Restore with `scripts/restore.sh <dir>` while services are stopped; it keeps the previous data as `*.before-restore-<ts>`. `tool-homes/` contains tool credentials — protect backups accordingly. Verified: the pg_dump backup on the production PostgreSQL instance (checksums OK, `pg_restore --list` reads the dump) and SQLite backup → restore; the `pg_restore` branch of `restore.sh` has not been run yet.
* **Upgrades**: rebuild images / `git pull && make install && make build`, then `make migrate`, restart.
* **Limits** (env): `AGENT_RUN_TIMEOUT_SECONDS=600`, `AGENT_TASK_TIMEOUT_SECONDS=1500`, `AGENT_MAX_ATTEMPTS=2`, `COMPILE_*`, `PRISM_MAX_*`. Projects can lower (not raise) agent limits in *Settings → Project*.
