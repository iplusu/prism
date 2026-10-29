# Orchestrator LLM and agent adapters

## Roles — do not mix them up

* The **orchestrator LLM** (served on a separate machine, e.g. the lab's a5000) analyses each request, chooses context, decides to answer, edit, or delegate, writes the structured plan, and explains the verified result.
* **Codex, Claude Code and Antigravity** are external executors that the server *invokes* with their own vendor models. Prism does not replace those tools' internal models with the orchestrator model.

## Orchestrator LLM

`backend/app/llm/provider.py`

* `LLMProvider` interface: `list_models()`, `chat(messages, tools=, tool_choice=, response_format=, …)`, `chat_stream(…)`.
* `OpenAICompatibleProvider` implements `/models` and `/chat/completions` (incl. SSE streaming). Errors are classified as `not_configured | dns | connect | timeout | tls | auth | http | protocol | unsupported`, with secrets redacted.
* A different wire format is added by implementing `LLMProvider` and registering it in `PROVIDERS` (selected by `LLM_PROVIDER`).

Connection test (`backend/app/llm/diagnostics.py`, UI: *Settings → Orchestrator LLM → Test connection*): reachability, model list + `LLM_MODEL` match (+ context length if the server reports it), plain completion, streaming, tool calling, JSON output (`json_object`, `json_schema`), timeout handling. The observed capabilities are stored per (base URL, model) and drive planning when `LLM_PLANNING_MODE=auto`:

| capability seen | planning mode |
|---|---|
| tool calling works | `tools`: tools `answer`, `edit`, `delegate` |
| JSON response format works | `json`: `response_format` + schema in prompt |
| neither | `json-prompt`: schema in prompt, server-side validation |

Every plan is validated with pydantic (`extra="forbid"`). Invalid output gets one repair round with the validation error; a second failure fails the task with the error shown to the user (the raw output is kept in the task events). `edit` plans are additionally checked: each `find` string must occur exactly once.

## Task contract

```json
{
  "task_id": "…", "project_id": "…", "base_revision": "<git commit>",
  "goal": "…", "task_kind": "edit_text|fix_build|bibliography|restructure|figures_tables|analysis|other",
  "executor": "codex|claude|antigravity|llm|demo",
  "read_scope": ["**"], "write_scope": ["sections/method.tex"],
  "constraints": ["…"],
  "acceptance_checks": ["compiles", "only_write_scope", "no_new_undefined_references", "no_new_undefined_citations",
                        "preserve_citations", "preserve_labels", "preserve_numbers", "preserve_equations"],
  "timeout_seconds": 600, "max_attempts": 2, "task_deadline_seconds": 1500
}
```

The model proposes `goal`, `task_kind`, scopes, constraints, checks and optionally an executor. The server fills ids/revision, decides the executor (policy below) and sets the budgets from the project policy, clamped to `AGENT_RUN_TIMEOUT_SECONDS`, `AGENT_TASK_TIMEOUT_SECONDS`, `AGENT_MAX_ATTEMPTS` (defaults 600 s / 1500 s / 2).

## Executor selection (Auto)

`backend/app/agent/policy.py`. Inputs: the project's allowed executors, per-task-kind routing (optional), the preference order (`AGENT_DEFAULT_ORDER`, default `codex,claude,antigravity` — a configurable preference, **not** a quality ranking), live health (installed / authenticated / flags present), tools already tried in this task, and recent failures (`AGENT_RECENT_FAILURE_MINUTES`; such tools are tried last). An explicit user choice wins but must still be allowed and healthy. On failure the next attempt in Auto mode uses the next tool; auth/unavailable failures with an explicit tool are not retried.

## Adapter interface

`backend/app/adapters/base.py`: `health()`, `capabilities()`, `start(spec)`, `stream_events(handle)`, `cancel(handle)`, `collect_result(handle)`.

Per run: a private work copy (`tasks/<id>/attempt-N/work`, only files in read ∪ write scope, never instruction/hook files, no `.git`), a run directory for logs (`tool.log`, redacted), a session id, a time limit. Processes are started with an argv list and the prompt on **stdin** — never via a shell. With `TOOL_SANDBOX=bwrap` (default) the tool sees: read-only `/usr`, its install directory (whole symlink chain), the work copy and its own tool home (read-write), DNS/TLS files in `/etc`, a private `/tmp`; no host home, no other projects, no Prism database or secrets. Network is allowed (tools call their vendor APIs). The exit code **and** the tool's structured result are both required for success. After the run, the server diffs the work copy against what it handed out; any change outside `write_scope`, to a protected file, or a new symlink rejects the whole attempt.

Environment for every tool: `HOME=<tool home>`, `PRISM_AGENT_DEPTH=1`, `CI=1`, `NO_COLOR=1`, plus only that tool's credential variable.

`health()` runs `<tool> --version`, the relevant `--help` and checks that **every flag the adapter uses** is present (`incompatible` otherwise), then checks authentication where the CLI offers a non-interactive check.

### Codex — `codex exec`

Verified against codex-cli 0.165.0 (`codex exec --help`).

```
codex exec --json --skip-git-repo-check --ephemeral --ignore-rules
           --sandbox danger-full-access --cd <work>     # workspace-write when TOOL_SANDBOX=none
           -c approval_policy="never" -c project_doc_max_bytes=0
           --output-last-message <run>/last_message.txt [-m CODEX_MODEL] -
```

* Sandbox mode: inside Prism's bwrap sandbox Codex's own sandbox cannot start (it needs a nested user namespace; every command and file write failed with "bwrap: No permissions to create a new namespace" in a real run), so Codex runs with `danger-full-access` and the outer sandbox is the boundary: only the work copy, the run directory and `CODEX_HOME` are writable, the host home and other projects are not mounted. Without the outer sandbox (`TOOL_SANDBOX=none`, which requires `ALLOW_UNSANDBOXED_TOOLS=true`) Codex keeps its own `workspace-write` sandbox.
* Prompt on stdin (`-`). `project_doc_max_bytes=0` disables AGENTS.md loading; `--ignore-rules` skips exec-policy files.
* Events (JSONL): `thread.started` (session id), `item.started/completed` with `agent_message`, `command_execution`, `file_change`, `error`, `turn.completed` (usage), `turn.failed`, `error`.
* Success: exit 0, `turn.completed`, no `turn.failed`/`error`.
* Auth: `codex login status` with `CODEX_HOME=<tool home>/.codex` (or `CODEX_OPENAI_API_KEY`).

### Claude Code — `claude -p`

Verified against Claude Code 2.1.312 (`claude --help`).

```
claude -p --output-format stream-json --verbose
       --permission-mode acceptEdits --permission-prompts none
       --tools Read,Edit,Write,Glob,Grep
       --strict-mcp-config --setting-sources user --disable-slash-commands
       --session-id <uuid> --append-system-prompt <rules>
       [--bare] [--model CLAUDE_MODEL] [--max-budget-usd N]
```

* Prompt on stdin. No shell tool is offered. `--strict-mcp-config` without a config means no MCP servers (no path back to Prism). `--setting-sources user` reads only the service's own config dir (`CLAUDE_CONFIG_DIR`), never the project's `.claude/`. With an API key, `--bare` also skips hooks, plugins and CLAUDE.md discovery.
* Events (NDJSON): `system/init` (session id, model), `assistant` (text and `tool_use` blocks), `result` (`subtype`, `is_error`, `result`, `total_cost_usd`, `num_turns`).
* Success: exit 0 and `result.subtype == "success"` and not `is_error`.
* Auth: `claude auth status` (JSON `loggedIn`) with `CLAUDE_CONFIG_DIR`, or `CLAUDE_ANTHROPIC_API_KEY`.

### Antigravity — `agy -p`

Based on the official docs (<https://antigravity.google/docs/cli/headless>, <https://antigravity.google/docs/cli/reference>) and checked against an installed agy 1.4.10, including a real edit task run through Prism. Health checks the installed `--help` for every flag used and reports `incompatible` instead of guessing.

```
agy --print= --input-format stream-json --output-format stream-json
    --print-timeout <N>s --mode accept-edits --sandbox [--model AGY_MODEL]
# --print takes the prompt as its value; it is passed empty and the prompt goes on stdin
stdin: {"event":"user","message":{"content":"<prompt>"}}\n   (closing stdin ends the session)
```

* Events: `init`, `step_update` (`conversation_id`, `state`, `step_type`, `tool_name`, `text_delta`), `result` (`status`, `response`, `error`, `usage`). Each event carries its payload under a key named after it, e.g. `{"event":"result","result":{...}}`.
* Success: exit 0 and `result.status == "SUCCESS"`. File writes in the workspace are auto-allowed in headless mode; shell commands are soft-denied unless pre-approved.
* Auth: there is no `agy auth status`; an interactive login writes `<tool home>/.gemini/antigravity-cli/antigravity-oauth-token`, so its presence (or `AGY_GEMINI_API_KEY`) means signed in, otherwise `needs_auth`. An `authentication required` error at run time also marks the run `needs_auth`. Credentials: interactive login once with `HOME=<tool home>`, or `AGY_GEMINI_API_KEY`.

### Demo executor (`DEMO_MODE=true` only)

A real child process going through the same pipeline (sandbox, stdin, events, cancel, timeout) that makes a fixed, labelled edit without any AI. Everything it produces is tagged `DEMO`.

## Status values

`available` · `unavailable` (not installed) · `needs_auth` · `incompatible` (installed version lacks a required flag) · `error`. A run result is `succeeded`, `failed`, `timeout`, `cancelled`, `needs_auth`, `unavailable` or `rejected` (scope violation).

## Cancellation and limits

Cancel sets a flag; the worker checks it about once per second, sends SIGTERM to the process group, then SIGKILL after 3 s. Because the tool runs in its own PID namespace with `--die-with-parent`, grandchildren die with it (verified by test). Per-run timeout = `min(run limit, remaining task budget)`; the task deadline also covers planning and verification.
