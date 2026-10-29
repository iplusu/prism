"""Codex CLI adapter: `codex exec --json`.

Verified against `codex exec --help` of codex-cli 0.165: --json (JSONL
events), -s/--sandbox, -C/--cd, --skip-git-repo-check, --ephemeral,
--ignore-rules, -c key=value, -m, -o/--output-last-message, prompt `-` = stdin.
Events: thread.started, turn.started, item.started/updated/completed
(agent_message, command_execution, file_change, reasoning, ...),
turn.completed, turn.failed, error.

Inside Prism's bwrap sandbox, Codex's own sandbox cannot start (it would need a
nested user namespace: "bwrap: No permissions to create a new namespace"), so
every shell command and file write fails. There the run uses
`--sandbox danger-full-access` and relies on the outer sandbox (only the work
copy, run dir and CODEX_HOME are writable; host home and other projects are not
visible). Without the outer sandbox, Codex's own `workspace-write` is kept.
"""

from __future__ import annotations

from ..config import get_settings
from .base import Adapter, RunHandle, RunResult, RunSpec, jline


class CodexAdapter(Adapter):
    name = "codex"
    label = "Codex"
    binary_setting = "codex_bin"
    help_args = ("exec", "--help")
    required_flags = ("--json", "--sandbox", "--cd", "--skip-git-repo-check", "--ignore-rules",
                      "--output-last-message", "--ephemeral")

    def codex_home(self) -> str:
        s = get_settings()
        return s.codex_home or str(self.tool_home() / ".codex")

    def tool_env(self) -> dict[str, str]:
        s = get_settings()
        env = {"CODEX_HOME": self.codex_home()}
        if s.openai_api_key_for_tool:
            env["OPENAI_API_KEY"] = s.openai_api_key_for_tool
        return env

    def _extra_rw(self) -> list[str]:
        import os

        os.makedirs(self.codex_home(), exist_ok=True)
        return [self.codex_home()]

    def check_auth(self, exe: str) -> tuple[str, str]:
        if get_settings().openai_api_key_for_tool:
            return "ok", "API key configured (CODEX_OPENAI_API_KEY)"
        import os

        os.makedirs(self.codex_home(), exist_ok=True)
        try:
            r = self._run_quick([exe, "login", "status"])
        except Exception as e:
            return "unknown", f"`codex login status` failed: {e}"
        text = (r.stdout + r.stderr).decode(errors="replace").strip()
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("WARNING")]
        msg = next((ln for ln in lines if "logged" in ln.lower()), lines[0] if lines else "")
        if r.returncode == 0 and "not logged in" not in text.lower():
            return "ok", msg[:160] or "logged in"
        return "missing", (msg[:160] or "not logged in") + f" (CODEX_HOME={self.codex_home()})"

    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        s = get_settings()
        argv = [exe, "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-rules",
                "--sandbox", "danger-full-access" if spec.isolated else "workspace-write", "--cd", str(spec.workdir),
                "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0",
                "--output-last-message", str(spec.run_dir / "last_message.txt")]
        if s.codex_model:
            argv += ["-m", s.codex_model]
        argv.append("-")  # read the prompt from stdin
        return argv

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        ev = jline(line)
        if ev is None:
            return []
        t = ev.get("type", "")
        if t == "thread.started":
            handle.state["session_id"] = ev.get("thread_id", "")
            return [{"type": "init", "text": f"thread {ev.get('thread_id', '')}", "data": {"session_id": ev.get("thread_id")}}]
        if t in ("item.completed", "item.started"):
            item = ev.get("item") or {}
            it = item.get("type") or item.get("item_type")
            if t == "item.completed" and it == "agent_message":
                handle.state["last_message"] = item.get("text", "")
                return [{"type": "message", "text": item.get("text", "")[:4000], "data": {}}]
            if it == "file_change" and t == "item.completed":
                paths = [c.get("path", "") for c in item.get("changes") or []]
                return [{"type": "file_change", "text": ", ".join(paths)[:500], "data": {"status": item.get("status")}}]
            if it == "command_execution":
                return [{"type": "tool", "text": f"{'run' if t == 'item.started' else 'done'}: {str(item.get('command', ''))[:200]}",
                         "data": {"exit_code": item.get("exit_code")}}]
            if it == "error":
                return [{"type": "error", "text": item.get("message", "")[:1000], "data": {}}]
            return []
        if t == "turn.completed":
            handle.state["completed"] = True
            handle.state["usage"] = ev.get("usage")
            return [{"type": "result", "text": "turn completed", "data": {"usage": ev.get("usage")}}]
        if t == "turn.failed":
            err = (ev.get("error") or {}).get("message", "turn failed")
            handle.state["failed"] = err
            return [{"type": "error", "text": err[:1000], "data": {}}]
        if t == "error":
            handle.state["failed"] = ev.get("message", "error")
            return [{"type": "error", "text": str(ev.get("message", ""))[:1000], "data": {}}]
        return []

    def finalize(self, handle: RunHandle, exit_code: int | None) -> RunResult:
        msg = handle.state.get("last_message", "")
        p = handle.spec.run_dir / "last_message.txt"
        if p.exists():
            msg = p.read_text(errors="replace") or msg
        ok = exit_code == 0 and handle.state.get("completed") and not handle.state.get("failed")
        return RunResult(status="succeeded" if ok else "failed", exit_code=exit_code, final_message=msg,
                         error="" if ok else str(handle.state.get("failed") or f"exit code {exit_code}"),
                         data={"usage": handle.state.get("usage")})
