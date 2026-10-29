# Verification

Everything below was actually executed on the development host (Ubuntu 26.04, no sudo, no Docker) on 2026-10-29. Items that could not be run are listed at the end. **Fake** components are test doubles and are named as such; **real** means the actual binary/service.

## Environment used

| Component | Version / source |
|---|---|
| Python | 3.12.16 (uv-managed), FastAPI 0.115.12, SQLAlchemy 2.0.40 |
| Node | 24.22.2 |
| TeX | TeX Live 2026 via TinyTeX, latexmk 4.88, pdfTeX 1.40.29, BibTeX |
| Sandbox | bubblewrap (system), unprivileged user namespaces available |
| Databases | SQLite (default test DB); **PostgreSQL 17.10** (embedded binaries, real server) |
| Browser | Firefox (system, headless) via puppeteer-core 24.43.1 |
| Codex CLI | codex-cli 0.165.0 (real, installed with npm; later signed in with ChatGPT in the service tool home) |
| Claude Code | 2.1.312 (real, installed) — tests use an isolated, empty config dir; the deployment is signed in (claude.ai) in the service tool home |
| Antigravity | agy 1.4.10 (real, official installer; signed in via OAuth in the service tool home). Needs a CPU with PCLMUL: on the first VM CPU model it crashed with SIGILL |
| Orchestrator LLM | **real**: vLLM on the a5000 server (4× RTX A5000, Qwen3.8-27B-FP8, served id `qwen38-a5000`, `max_model_len` 262144, Qwen3 reasoning parser), reached over the tailnet through the server's existing passthrough; no server settings changed |

## Commands and results

| Check | Command | Result |
|---|---|---|
| Frontend typecheck | `cd frontend && npm run typecheck` | pass, 0 errors |
| Frontend build | `cd frontend && npm run build` | pass (Vite 7.3.7) |
| Backend suite, SQLite | `cd backend && uv run pytest` | **72 passed** (6 min 30 s) |
| Backend suite, PostgreSQL 17 | `PRISM_TEST_DATABASE_URL=postgresql+psycopg://… uv run pytest` | **72 passed** (schema built by the Alembic migration) |
| Migration ↔ models | `alembic upgrade head && alembic check` on PostgreSQL 17 | "No new upgrade operations detected" |
| Browser E2E | `e2e/verify.mjs` against `scripts/serve.sh` (PostgreSQL, collab on, DEMO_MODE on) | **14/14 steps passed**; screenshots in `docs/screenshots/` |
| Backup → destroy → restore | `scripts/backup.sh`, delete project + DB, `scripts/restore.sh` (SQLite) | project text and labelled versions restored (found and fixed a WAL-file bug in restore) |
| Orchestrator LLM, real | `python -m app.cli llm-test` against the a5000 server | **7/7 steps passed**: reachability, model id served, chat, streaming, tool calling, `json_object` + `json_schema`, 50 ms timeout → `timeout`. Planning mode chosen: tools |
| Authenticated tool runs, real | on the deployed service: the same edit request ("add one sentence to the end of sections/conclusion.tex") with mode `codex`, `claude`, `antigravity`; LLM plans via tool calling, each tool runs in bwrap | **Claude Code**: proposal ready, candidate build ok, checks `preserve_labels`/`preserve_citations`/`only_write_scope` passed (238 s). **Antigravity**: same result (383 s). **Codex**: first run made no change — its own sandbox could not start inside ours (`bwrap: No permissions to create a new namespace`), reported honestly with no proposal; after the fix below: one-sentence diff, proposal ready (126 s). Proposals were not applied; the temporary project was deleted |
| Auto task, real LLM | temporary sample project on the deployed service, `mode=auto`, question about title and sections | completed in 15 s, planned via tool calling, answered directly (no external tool) with the correct title and the 4 sections; project deleted afterwards |
| Backup, PostgreSQL | `scripts/backup.sh` on the production PostgreSQL 17.11 instance (pg_dump 17.11) | custom-format `db.dump` written; `sha256sum -c SHA256SUMS` OK for all 3 files; `pg_restore --list` reads it (13 table-data entries). Restore not run |

## What the backend tests cover (`backend/tests`)

| Requirement | Tests | Real / fake |
|---|---|---|
| ZIP import + path validation: `../`, absolute, backslash, NUL, symlink entries, declared-size bomb, forged-header bomb (streaming counter), entry count, foreign `.git`, folder stripping, API rejects traversal | `test_security_paths.py` | real |
| Edit save, re-login and reload | `test_projects_files::test_edit_save_and_reconnect` | real |
| Stale edit rejected (409) | `test_projects_files::test_stale_save_is_rejected` | real |
| File create/rename/upload/delete, outline, bib keys, search | `test_projects_files::test_file_ops` | real |
| Project isolation between users; viewer read-only; CSRF header | `test_project_isolation_between_users`, `test_csrf_header_required`, `test_unauthenticated`, `test_proposal_access_is_project_scoped` | real |
| Real LaTeX → PDF (BibTeX resolved, text checked with pypdf), in bwrap | `test_compile::test_real_build_produces_pdf` | real TeX |
| Failed build keeps last PDF; stale flag; error file:line | `test_compile::test_failed_build_keeps_last_success_and_marks_stale` | real TeX |
| Shell escape blocked; host files unreadable; project `latexmkrc` not executed; refuse unsandboxed by default | `test_compile` (4 tests) | real TeX + bwrap |
| LLM connection test: all 7 steps, wrong model id, no tool calling → JSON mode, connection refused, DNS failure, not configured, 401, read timeout, streaming, key never in errors | `test_llm.py` | **fake** OpenAI-compatible server |
| Invalid LLM output: rejected twice → task fails with message; repaired on 2nd try; shell `command` field rejected | `test_agent.py` | fake LLM |
| LLM answers directly; LLM edit → verified proposal → apply | `test_agent.py` | fake LLM, real TeX |
| Auto delegation, contract fields and server budgets | `test_agent::test_auto_delegation_to_tool_then_apply` | fake LLM + **fake CLI** in bwrap |
| Out-of-scope / protected-file changes rejected; second tool tried; max 2 attempts | `test_agent::test_out_of_scope_changes_are_rejected` | fake CLI |
| Tool not installed → unavailable; auth failure → needs_auth, no retry | `test_agent` | fake CLI / missing binary |
| Timeout kills the process tree (incl. grandchild) | `test_agent::test_tool_timeout_kills_process_tree` | fake CLI in bwrap |
| Cancel running task, processes gone, retry | `test_agent::test_cancel_running_task` | fake CLI in bwrap |
| Auto without LLM refused; explicit tool without LLM uses server contract | `test_agent` | — |
| Apply on unchanged manuscript; no apply before verification / twice | `test_proposals.py` | real TeX |
| Stale base + edit elsewhere → 3-way merge → re-verified → applied (both edits kept) | `test_stale_base_with_concurrent_edit_elsewhere_merges_and_reverifies` | real |
| Stale base + conflicting edit → `conflict`, live text untouched; resolution with markers refused; resolved → verified → applied | `test_conflicting_edit_is_never_overwritten` | real |
| Delete-vs-edit conflict resolved by whole-file choice; no-op resolution refused | `test_delete_vs_edit_conflict_resolved_by_choice` | real |
| Selective hunk apply → new proposal, recompiled, applied subset only | `test_selective_hunks_create_reverified_proposal` | real |
| Candidate that fails to compile is reported as failed check | `test_failed_candidate_build_is_reported` | real TeX |
| **Collaboration**: 2 real y-websocket clients edit concurrently and converge; AI proposal applied while both connected (both see it, their edits kept); conflicting live edit → conflict; viewer edits dropped; offline edit + reconnect; collab restart → state restored; write-through to disk; forged/foreign/missing tokens → 401; internal API without secret → 403 | `test_collab_integration.py` | real backend (uvicorn), real collab service, real Yjs clients |
| **Real CLIs**: Codex and Claude Code installed, versions read, all used flags present, real binary launched in bwrap with our argv/stdin, unauthenticated run reported as `needs_auth` (Codex got a real HTTP 401 from the OpenAI API, Claude Code returned "Not logged in"); Antigravity absent → `unavailable` | `test_real_cli.py` | **real** |

## Browser E2E (`e2e/verify.mjs`)

Two separate browser sessions (admin and a second editor) against the running stack:

1. login page, admin login → 2. project list → 3. workspace with editor → 4. **Compile → real PDF rendered by pdf.js** (canvas pixels checked) → 5. second user opens the project → 6. **simultaneous typing in both browsers converges** (both editors and the server contain both edits) → 7. **stale badge** after editing → 8. AI task with the DEMO executor via the AI panel → 9. **review screen**: diff, verification (checks, builds, limitations), candidate PDF, **Apply** → the second user's open editor receives the applied change and keeps his own edit → 10. settings: LLM connection test shows "not configured" honestly → 11. tools status (`needs_auth` / `unavailable` / `available`) → 12. dark mode + 420 px narrow layout with tabs → 13. no uncaught page errors (Firefox BiDi instrumentation messages about cross-origin worker objects are filtered as automation noise).

Screenshots: `docs/screenshots/01-login.png` … `11-narrow-dark.png`.

## Bugs found by these checks and fixed

* ZIP import: a single top-level `../` entry was treated as a strippable folder prefix → traversal. Fixed (validate full name first).
* Sandbox: binaries reached through a chain of symlinks (npm global bin → package) were not visible inside bwrap; the earlier lenient test hid it. Fixed (bind every hop) and the test now requires `needs_auth` and no `execvp` error.
* Collab: WebSocket messages arriving while a document was loading were dropped (clients never synced). Fixed (queue until ready).
* LLM timeout probe could pass/fail depending on server speed. Now a long generation under a 50 ms budget; a server that answers faster is reported as "not exercised".
* LLM connection test against the real reasoning model: chat and streaming probes had a 16-token budget that the model's hidden reasoning used up, so they reported an empty reply. Probes now allow 512 tokens and report `finish_reason`/usage when the reply is empty.
* Codex inside the bwrap sandbox: `--sandbox workspace-write` makes Codex start its own bubblewrap, which cannot create a nested namespace, so every command and file write failed. Inside Prism's sandbox Codex now runs with `danger-full-access` and the outer sandbox is the boundary; `workspace-write` is kept when there is no outer sandbox.
* Antigravity: showed `available` before sign-in (unknown auth was mapped to available); `--print` needs a value; event payloads are nested under the event name. Adapter fixed against agy 1.4.10; sign-in is detected from the OAuth token file.
* SQLite timestamps lacked a timezone; restore left stale WAL files; conflicts on binary/added/deleted files were not resolvable. Fixed.

## Not verified here

* **Real orchestrator LLM, beyond Q&A**: connection and a direct answer were verified with the real model (above); an LLM-written edit proposal and delegation to an external tool have not been run with it yet (the latter also needs authenticated tools). The automated suite still uses the fake server.
* **Auto mode choosing an external tool** with the real LLM and authenticated tools, and the second-attempt fallback with real tools (both verified only with the fake CLI).
* **Docker Compose** build/start (no Docker on this host).
* `pg_restore` branch of `scripts/restore.sh` (never run against a database; the SQLite restore branch was verified).
