# Prism — a LaTeX paper-writing workspace for research labs

A browser-based paper-writing tool that runs on a lab server. One screen provides:

- LaTeX project and file management (create, ZIP import, upload, rename, delete, search, section outline)
- Official conference/preprint templates (ACL, CVPR/ICCV, NeurIPS 2025, ICML 2026, arXiv): downloaded from the publishers' own URLs when a project is created, never redistributed by Prism
- Bibliography: search arXiv and add BibTeX by arXiv id or DOI (entries are taken verbatim from arXiv / doi.org, duplicates detected)
- PDF <-> source jumps (SyncTeX): "Show in PDF" (or Ctrl+Alt+J) highlights the caret's line on the page; double-clicking the PDF opens the source file at that line, also in included files
- Bibliography verification: every `.bib` entry is looked up by DOI (doi.org), arXiv id (arXiv API) or title (Crossref, then OpenAlex) and marked verified / different work / not found; "not found" is a hint (books and non-English works are often missing)
- Zotero import: a library, group or collection (API key sent per request, never stored; public groups need none), merged into the `.bib` without duplicating works already there
- arXiv preflight (Check tab) and "arXiv source (.zip)" export with the `.bbl` of your last compile; rules are taken from arXiv's help pages
- Reviewer report (AI panel, "Reviewer report…"): Codex, Claude Code and Antigravity each review the paper in parallel as separate, read-only reviewers (in their sandboxes; anything they write is discarded), optionally as a persona you type (e.g. "an ACL area chair"); the orchestrator LLM then merges the reviews into one report with agreements, disagreements and a prioritized improvement plan, and each tool's original review stays expandable. A tool that fails is shown as failed, never filled in
- AI analysis presets (AI panel): terminology consistency, missing citations; the server lets them answer only, never edit or call a tool
- Compile any `.tex` file: Compile builds the open file and names the PDF after it (a section file without `\documentclass` builds the main file instead); a switch makes it always build the main file
- Drag and drop: drop files or whole folders onto the file panel or onto a folder to upload them
- Ask AI about a selection: select text in the editor and press "Ask AI about selection"; the selected lines become the AI request's context
- Failed builds: a missing TeX package (`File 'x.sty' not found`) is installed automatically and the paper rebuilt (`tlmgr`, official TeX Live repository only, at most 5 packages per build; switches `TEX_PACKAGE_AUTO_INSTALL`, `ALLOW_TEX_PACKAGE_INSTALL`), with a one-click button as the fallback; other LaTeX errors offer "Fix with AI", which creates an ordinary reviewed task
- One-click apply: proposals in the AI chat have an Apply button (verified proposals only), and the Review page applies with a single click
- Auto compile (opt-in, off by default): compiles a few seconds after you stop editing; a failing text is tried once, not in a loop
- AI autocomplete (opt-in, off by default): greyed inline suggestion at the end of a line after a pause, Tab accepts, Esc dismisses; uses the orchestrator LLM, one request per user at a time
- Manuscript check (no AI): undefined citations/references, duplicate labels and keys, missing figures and inputs, TODO markers, each linked to its line
- LaTeX/BibTeX editor (CodeMirror 6: syntax highlighting, find/replace, jump to error location, autosave)
- Preview of the real compiled PDF (TeX Live + latexmk, bubblewrap isolation, rendered with pdf.js)
- AI chat with the paper as context. An **orchestrator LLM** on a separate inference server (e.g. a5000) analyses each request and, when needed, automatically calls the Codex / Claude Code / Antigravity CLIs
- Review of AI changes: diff, verification report, candidate PDF, apply all or part, conflict resolution
- Version history (Git), named versions, version restore (as a reviewed proposal), ZIP/PDF export
- Real-time collaborative editing by several researchers (Yjs) with roles (owner / editor / viewer)

> The design document `Local_Research_Workspace_Design.md` mentioned as an attachment could not be found in the working environment (a full filesystem search returned nothing). The implementation follows the request prompt and the later design change (orchestrator LLM moved to a separate server, `LLM_*` settings).

![workspace](docs/screenshots/03-workspace-compiled.png)

## Components

| Component | Technology | Location |
|---|---|---|
| Web/API | Python 3.12, FastAPI, SQLAlchemy, Alembic | `backend/app` |
| Worker | Same codebase, `python -m app.worker`; compiles, proposal verify/apply and AI tasks run in separate threads, so a long AI task never holds up a compile | `backend/app/worker.py` |
| DB / job queue | PostgreSQL 17 (`FOR UPDATE SKIP LOCKED` queue). SQLite supported for development | `backend/migrations` |
| Compilation | TeX Live + latexmk, bubblewrap sandbox | `backend/app/compile` |
| Orchestrator LLM | OpenAI-compatible API client (provider adapter design) | `backend/app/llm` |
| Tool adapters | `codex exec`, `claude -p`, `agy -p` | `backend/app/adapters` |
| Collaboration | Node + Yjs (y-protocols), persistent storage | `collab/server.mjs` |
| Frontend | React 18 + TypeScript + Vite, CodeMirror 6, pdf.js | `frontend/` |
| Deployment | Docker Compose + nginx | `docker-compose.yml`, `deploy/` |

Choices that differ from the suggested stack (a PostgreSQL queue instead of Redis, bubblewrap inside the worker container instead of a container per build) and the reasons for them are in [docs/architecture.md](docs/architecture.md).

## Quick start (development)

Requires `uv`, Node 20+ (npm), `git`, `bubblewrap`, and TeX Live (`latexmk`). No sudo needed (this repository was developed and verified on a server without sudo; installation notes in [docs/deployment.md](docs/deployment.md)).

```bash
make install                 # backend (uv sync), frontend (npm ci), collab (npm ci)
cp .env.example .env         # for development, delete the DATABASE_URL line to use SQLite (var/prism.db)
#   fill in PRISM_ADMIN_USERNAME / PRISM_ADMIN_PASSWORD to create an admin on first start
#   with COLLAB_ENABLED=true, dev.sh also starts the collaboration service
make dev                     # API :8000, worker, collab :1234, Vite :5173
```

Browser: **http://localhost:5173** (Vite proxies `/api` and `/collab`)

## Running on a server

### Docker Compose (recommended for production)

```bash
cp .env.example .env         # set PRISM_SECRET_KEY, PRISM_ADMIN_*, COLLAB_SHARED_SECRET, LLM_*
export POSTGRES_PASSWORD=...  # or add it to .env
docker compose build
docker compose up -d
```

Browser: **http://<server>:8080** (change with `PRISM_HTTP_PORT`). When exposing it beyond the lab, put a TLS reverse proxy in front and set `PRISM_COOKIE_SECURE=true`. Service layout and network isolation: [docs/deployment.md](docs/deployment.md).

> This development host has no Docker, so the Compose setup has **not been verified by running it** (only its YAML structure was checked). The Docker-free method below has been verified for real.

### Without Docker

```bash
cd frontend && npm ci && npm run build && cd ..
./scripts/serve.sh           # needs .env. web (0.0.0.0:8000) + worker + collab
```

Browser: **http://<server>:8000**. The collaboration WebSocket is proxied over the same port (`/collab`), so port 1234 does not need to be exposed.

### Access over Tailscale (tailnet only)

The web process proxies the collaboration WebSocket at `/collab` on the same port (leave `COLLAB_PUBLIC_URL` empty), so a single port is enough. Nothing is exposed on public or LAN interfaces in either setup below.

**HTTPS without a port (recommended; used by the current deployment)**: Prism listens only on localhost and Tailscale terminates TLS with a tailnet certificate.

1. In the Tailscale admin console, enable MagicDNS and HTTPS certificates (DNS page).
2. Let your user manage Tailscale serve, once (needs sudo): `sudo tailscale set --operator=$USER`
3. In `.env`: `PRISM_HOST=127.0.0.1`, `PRISM_COOKIE_SECURE=true`; restart `prism-web prism-collab prism-worker`.
4. `tailscale serve --bg --https 443 http://127.0.0.1:8000` (persists across reboots; check with `tailscale serve status`, remove with `tailscale serve --https=443 off`).

- Address: `https://<machine>.<tailnet>.ts.net/`
- With secure cookies, plain-HTTP addresses cannot log in, so do not also expose an HTTP port.

**Plain HTTP on a port (no sudo)**: with `PRISM_HOST=tailscale`, `serve.sh` and the systemd services bind only to this node's Tailscale IPv4 address. Address: `http://<machine>.<tailnet>.ts.net:8000` (keep `PRISM_COOKIE_SECURE=false`). Ports below 1024 such as 80 need root, so the port stays in the URL.

### Always-on systemd services (no root needed)

```bash
loginctl enable-linger $USER          # keep services running without a login session (once)
./scripts/install-systemd.sh          # prism-db (optional) / collab / web / worker + daily 03:30 backup timer
systemctl --user start prism-db prism-collab prism-web prism-worker prism-backup.timer
systemctl --user status 'prism-*'     # status
journalctl --user -u prism-web -f     # logs
```

If `PRISM_PG_DATA`/`PRISM_PG_BIN` are set, PostgreSQL is managed as the `prism-db` service as well. After changing settings, run `systemctl --user restart prism-web prism-worker`.

Admin commands:

```bash
cd backend
uv run python -m app.cli create-user alice --admin   # password from prompt/stdin
uv run python -m app.cli doctor                      # TeX, sandbox, LLM settings, collab, tool status
uv run python -m app.cli llm-test                    # orchestrator LLM connection test (JSON output)
uv run python -m app.cli tools                       # tool installation/authentication status
```

## Connecting the orchestrator LLM (separate inference server such as a5000)

No model is installed on the web server. Only the backend calls the inference API; browsers never reach the LLM server directly. The existing LLM server's runtime configuration is left unchanged.

```ini
LLM_PROVIDER=openai          # OpenAI-compatible /v1/chat/completions (vLLM, SGLang, llama.cpp server, LM Studio …)
LLM_BASE_URL=http://<address reachable from the web server>:<port>/v1
LLM_MODEL=<exact model id listed by GET /v1/models>
LLM_API_KEY=<if required>
LLM_CONTEXT_CHARS=24000      # adjust to the context window of the model actually being served
```

Port, model id, API format and context length are never guessed from the server name (for the deployment, the endpoint and model id were taken from the running server on a5000 and its `GET /v1/models` list). For an API that is not OpenAI-compatible, implement `LLMProvider` in `backend/app/llm/provider.py` and register it in `PROVIDERS`.

**Settings → Orchestrator LLM → Test connection** (or `app.cli llm-test`) actually checks:

1. Whether the API is reachable from the web backend (distinguishing DNS failure / connection refused / TLS / authentication errors)
2. The model list the server offers and whether `LLM_MODEL` matches it (context length is shown if the server reports it)
3. Normal and streaming responses
4. Support for tool calling and for JSON output (`json_object` / `json_schema`)
5. Timeout handling (a 50 ms budget on a long generation request → a clean `timeout` error)

The result is stored, and with `LLM_PLANNING_MODE=auto` the planning method (tool calling → JSON mode → JSON in the prompt + server-side validation) is chosen according to the **observed capabilities**.

## Installing and authenticating the external CLIs

Each tool uses a dedicated service home directory (default `<PRISM_DATA_DIR>/tool-homes/<tool>`). Settings and credentials from users' personal home directories are not used.

| Tool | Install | Authentication (either one) |
|---|---|---|
| Codex | `npm install -g @openai/codex` | `CODEX_HOME=<data>/tool-homes/codex/.codex codex login` or `CODEX_OPENAI_API_KEY` |
| Claude Code | `npm install -g @anthropic-ai/claude-code` (or the official install script) | `CLAUDE_CONFIG_DIR=<data>/tool-homes/claude/.claude claude auth login`, or `CLAUDE_ANTHROPIC_API_KEY` (then run with `--bare`) |
| Antigravity | Per the [official documentation](https://antigravity.google/docs) | Log in interactively once with `HOME=<data>/tool-homes/antigravity agy`, or `AGY_GEMINI_API_KEY` |

Check status under **Settings → Tools** (installed / version / authentication / `--help` flag compatibility). Details: [docs/agent-adapters.md](docs/agent-adapters.md).

## Real service vs. DEMO mode

| | Real service | `DEMO_MODE=true` |
|---|---|---|
| Orchestration | Configured LLM | Rule-based planning when no LLM is configured (questions are answered with an explicit "cannot answer") |
| Executors | Codex / Claude Code / Antigravity | Plus a `demo` executor: a real subprocess, but a fixed edit with no AI that inserts one labelled comment line |
| Labelling | — | Banner at the top of every screen, `DEMO` tag on tasks and proposals |

Compilation, verification, diff, apply and collaborative editing all work for real in DEMO mode too. Do not enable it on a production instance.

## Where data is stored, and backups

- DB: PostgreSQL (users, projects, build/task/proposal metadata, job queue, events)
- `PRISM_DATA_DIR/projects/<id>/repo`: current manuscript working tree + `.git` history
- `PRISM_DATA_DIR/builds/<id>/output.pdf`: build PDFs (the latest 10 per project + the last successful one are kept)
- `PRISM_DATA_DIR/proposals/<id>/{base,candidate}`: files before/after a proposal
- `PRISM_DATA_DIR/tasks/<id>/attempt-N/{work,run}`: tool working copies and logs
- `PRISM_DATA_DIR/tool-homes/`: per-tool login data (secret — handle backups with care)
- `COLLAB_DATA_DIR/<id>.ystate`: collaborative document state

```bash
./scripts/backup.sh [target dir]     # pg_dump (or SQLite backup) + data + collab, with SHA256SUMS
./scripts/restore.sh <backup dir>    # run with the services stopped
```

## Implementation status

Implemented and verified (evidence in [docs/verification.md](docs/verification.md)):

- Project home, workspace, change review and settings screens (light/dark, tabbed layout on narrow screens, resizable/collapsible panels)
- Safe ZIP import, per-project permissions, CSRF protection
- Isolated real LaTeX compilation, last successful PDF kept on failure, stale indicator
- Orchestrator LLM client, 5-step connection test, schema validation and re-prompting, direct answers / direct edit proposals / delegation to external tools
- Three tool adapters, automatic selection policy, limits of 2 attempts, 10 minutes per run and 25 minutes overall, cancel/retry, allowed-scope checks, recursive delegation blocked
- Proposal verification (compile + citation/label/number/equation report), conditional apply, 3-way merge followed by re-verification, conflict resolution, partial (hunk) apply followed by re-verification
- Real-time collaborative editing (two clients editing at once, AI changes propagated, read-only viewers, reconnect, state restored after restart)
- Saving, comparing and restoring versions, ZIP/PDF export, backup and restore scripts
- Official templates (all five download, import and compile for real in the sandbox), arXiv search, BibTeX import by arXiv id / DOI (arXiv BibTeX and doi.org checked against the live services), manuscript check, AI autocomplete (real a5000 answers in ~0.6 s with thinking disabled; browser-checked)

Remaining work / not verified:

- **Orchestrator LLM**: connected to the a5000 server (vLLM, Qwen3.8-27B-FP8, served id `qwen38-a5000`). All 7 connection-test steps pass and a real Auto question was answered correctly from the project. The real model also planned and delegated edit tasks to each external tool (below). Not yet run with the real model: an edit proposal written by the LLM itself, and Auto mode choosing among the tools.
- **External tools**: Codex 0.165.0 and Claude Code 2.1.312 are signed in separately in the service tool homes; Antigravity (agy) 1.4.10 is signed in separately for Prism (its token differs from the personal one). A real edit task with each of the three, inside the bwrap sandbox, produced a proposal; Antigravity was re-run with its own login (86 s, one file changed, proposal ready). Antigravity needs a CPU with PCLMUL (a generic QEMU CPU model is not enough).
- **Auto mode with the real LLM**: observed end to end — the model edited directly for a small request, and delegated a file-restructuring request, for which Auto picked Codex (and Claude Code when it was first in the project's order); the verification caught a real problem (Claude Code cannot delete files, so equations ended up duplicated). Planning time varies from under a minute to over ten (shared a5000; `LLM_TIMEOUT_SECONDS=600`), and one run failed on that timeout. **Not observed with real tools: the fallback to the next tool after a failure** (covered by fake-CLI tests only).
- **arXiv search** parsing is tested against a response with the real structure; a live search was blocked by arXiv rate limiting (HTTP 429) when tested.
- **Docker Compose**: no Docker on this host, so image builds and startup were not verified.
- Backup script: the `pg_dump` backup path was run on the production PostgreSQL instance; the `pg_restore` path of `restore.sh` has not been run. The SQLite backup/restore path was verified.
- The memory cap for tool processes relies on the container (`mem_limit`) (Node-based CLIs are incompatible with `RLIMIT_AS`).
- AI chat history is per user (task history is shared within the project).

## Documentation

- [docs/architecture.md](docs/architecture.md) — components, data flow, security boundaries, reasons for design choices
- [docs/deployment.md](docs/deployment.md) — installation, Compose, network, TLS, operations
- [docs/agent-adapters.md](docs/agent-adapters.md) — orchestrator LLM, task contract, per-tool flags, events and authentication
- [docs/verification.md](docs/verification.md) — checks actually performed and their results
