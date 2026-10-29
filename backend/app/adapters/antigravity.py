"""Google Antigravity CLI adapter: `agy -p` (headless mode).

Based on the official docs (antigravity.google/docs/cli/headless and
/reference), not on an installed binary — `agy` is not installed on the
development host. The flags below are checked against the installed `agy
--help` at health-check time; a missing flag marks the adapter
`incompatible` instead of guessing.

The prompt is sent on stdin with `--input-format stream-json`
({"event":"user","message":{"content":...}}); closing stdin ends the session.
stream-json output events: init, step_update (state, step_type, tool_name,
text_delta), result (status SUCCESS|ERROR|CANCELED|..., response, error).
"""

from __future__ import annotations

import json

from ..config import get_settings
from .base import Adapter, RunHandle, RunResult, RunSpec, jline


class AntigravityAdapter(Adapter):
    name = "antigravity"
    label = "Antigravity"
    binary_setting = "agy_bin"
    required_flags = ("--output-format", "--input-format", "--print-timeout", "--mode")

    def home(self) -> str:
        s = get_settings()
        return s.agy_home or str(self.tool_home())

    def tool_env(self) -> dict[str, str]:
        s = get_settings()
        env = {"HOME": self.home()}  # config lives in ~/.gemini/antigravity-cli
        if s.gemini_api_key_for_tool:
            env["GEMINI_API_KEY"] = s.gemini_api_key_for_tool
        return env

    def _extra_rw(self) -> list[str]:
        return [self.home()]

    def check_auth(self, exe: str) -> tuple[str, str]:
        if get_settings().gemini_api_key_for_tool:
            return "ok", "API key configured (AGY_GEMINI_API_KEY)"
        return "unknown", ("no documented non-interactive auth check; sign in once interactively with "
                           f"HOME={self.home()} agy, or set AGY_GEMINI_API_KEY. Auth failures are detected at run time.")

    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        s = get_settings()
        argv = [exe, "--print", "--input-format", "stream-json", "--output-format", "stream-json",
                "--print-timeout", f"{spec.timeout_seconds}s", "--mode", "accept-edits", "--sandbox"]
        if s.agy_model:
            argv += ["--model", s.agy_model]
        return argv

    def stdin_payload(self, spec: RunSpec) -> bytes:
        return (json.dumps({"event": "user", "message": {"content": spec.prompt}}) + "\n").encode("utf-8")

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        ev = jline(line)
        if ev is None:
            return []
        t = ev.get("type") or ev.get("event")
        if t == "init":
            return [{"type": "init", "text": f"cwd={ev.get('cwd', '')} permission_mode={ev.get('permission_mode', '')}", "data": {}}]
        if t == "step_update":
            if ev.get("conversation_id"):
                handle.state["session_id"] = ev["conversation_id"]
            if ev.get("text_delta"):
                handle.state["text"] = handle.state.get("text", "") + ev["text_delta"]
                return []
            if ev.get("state") == "DONE" and ev.get("step_type") == "tool":
                return [{"type": "tool", "text": str(ev.get("tool_name", "tool")), "data": {}}]
            return []
        if t == "result":
            handle.state["result"] = ev
            return [{"type": "result", "text": str(ev.get("response") or ev.get("error") or "")[:4000],
                     "data": {"status": ev.get("status")}}]
        return []

    def finalize(self, handle: RunHandle, exit_code: int | None) -> RunResult:
        res = handle.state.get("result")
        if res is None:
            return RunResult(status="failed", exit_code=exit_code, error="no result event received")
        ok = exit_code == 0 and res.get("status") == "SUCCESS"
        return RunResult(status="succeeded" if ok else "failed", exit_code=exit_code,
                         final_message=str(res.get("response") or handle.state.get("text", "")),
                         session_id=str(res.get("conversation_id") or ""),
                         error="" if ok else str(res.get("error") or res.get("status") or "error"))
