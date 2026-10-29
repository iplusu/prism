"""Claude Code CLI adapter: `claude -p` with `--output-format stream-json`.

Verified against `claude --help` of the installed CLI (2.1.x): -p/--print,
--output-format stream-json (requires --verbose), --permission-mode,
--permission-prompts, --tools, --strict-mcp-config, --setting-sources,
--disable-slash-commands, --session-id, --append-system-prompt, --model,
--max-budget-usd, --bare.
"""

from __future__ import annotations

import json

from ..config import get_settings
from .base import Adapter, RunHandle, RunResult, RunSpec, jline

RULES = (
    "You are running non-interactively as an executor for a LaTeX manuscript workspace. "
    "Only edit files inside the current working directory, and only the files named in the task's write scope. "
    "Do not create tool configuration or instruction files. Do not try to compile, install software or access "
    "the network. When done, reply with a short summary of what you changed."
)


class ClaudeCodeAdapter(Adapter):
    name = "claude"
    label = "Claude Code"
    binary_setting = "claude_bin"
    required_flags = ("--output-format", "--permission-mode", "--tools", "--session-id", "--strict-mcp-config",
                      "--setting-sources", "--append-system-prompt", "--disable-slash-commands")

    def config_dir(self) -> str:
        s = get_settings()
        return s.claude_config_dir or str(self.tool_home() / ".claude")

    def tool_env(self) -> dict[str, str]:
        s = get_settings()
        env = {"CLAUDE_CONFIG_DIR": self.config_dir(), "DISABLE_AUTOUPDATER": "1",
               "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_TELEMETRY": "1"}
        if s.anthropic_api_key_for_tool:
            env["ANTHROPIC_API_KEY"] = s.anthropic_api_key_for_tool
        return env

    def _extra_rw(self) -> list[str]:
        return [self.config_dir()]

    def check_auth(self, exe: str) -> tuple[str, str]:
        if get_settings().anthropic_api_key_for_tool:
            return "ok", "API key configured (CLAUDE_ANTHROPIC_API_KEY)"
        try:
            r = self._run_quick([exe, "auth", "status"])
            data = json.loads(r.stdout.decode() or "{}")
        except Exception as e:
            return "unknown", f"`claude auth status` failed: {e}"
        if data.get("loggedIn"):
            return "ok", f"logged in ({data.get('authMethod', '?')}) with CLAUDE_CONFIG_DIR={self.config_dir()}"
        return "missing", f"not logged in for CLAUDE_CONFIG_DIR={self.config_dir()}"

    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        s = get_settings()
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose",
                "--permission-mode", "acceptEdits", "--permission-prompts", "none",
                "--tools", "Read,Edit,Write,Glob,Grep",
                "--strict-mcp-config", "--setting-sources", "user", "--disable-slash-commands",
                "--session-id", spec.session_id, "--append-system-prompt", RULES]
        if s.anthropic_api_key_for_tool:
            argv.append("--bare")  # skips hooks, plugins and CLAUDE.md discovery; API-key auth only
        if s.claude_model:
            argv += ["--model", s.claude_model]
        if s.claude_max_budget_usd > 0:
            argv += ["--max-budget-usd", str(s.claude_max_budget_usd)]
        return argv

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        ev = jline(line)
        if ev is None:
            return [{"type": "message", "text": line[:500], "data": {"raw": True}}] if line.strip() else []
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            handle.state["session_id"] = ev.get("session_id", "")
            return [{"type": "init", "text": f"session {ev.get('session_id', '')} model={ev.get('model', '?')}",
                     "data": {"session_id": ev.get("session_id"), "model": ev.get("model")}}]
        if t == "assistant":
            out = []
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append({"type": "message", "text": c["text"][:4000], "data": {}})
                elif c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    target = inp.get("file_path") or inp.get("path") or inp.get("pattern") or ""
                    kind = "file_change" if c.get("name") in ("Edit", "Write", "MultiEdit") else "tool"
                    out.append({"type": kind, "text": f"{c.get('name')} {target}".strip(), "data": {"tool": c.get("name")}})
            return out
        if t == "result":
            handle.state["result"] = ev
            return [{"type": "result", "text": (ev.get("result") or "")[:4000],
                     "data": {k: ev.get(k) for k in ("subtype", "is_error", "num_turns", "total_cost_usd", "duration_ms")}}]
        return []

    def finalize(self, handle: RunHandle, exit_code: int | None) -> RunResult:
        res = handle.state.get("result")
        if res is None:
            return RunResult(status="failed", exit_code=exit_code, error="no result event received")
        ok = exit_code == 0 and res.get("subtype") == "success" and not res.get("is_error")
        return RunResult(status="succeeded" if ok else "failed", exit_code=exit_code,
                         final_message=res.get("result") or "", session_id=res.get("session_id", ""),
                         error="" if ok else (res.get("result") or res.get("subtype") or "error"),
                         data={"cost_usd": res.get("total_cost_usd"), "num_turns": res.get("num_turns")})
