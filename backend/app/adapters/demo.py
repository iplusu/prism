"""DEMO_MODE executor. Runs a real child process through the same pipeline as
the real CLIs (sandbox, stdin, JSONL events, cancel, timeout) but performs a
fixed, clearly labelled edit instead of calling any AI service. Only
registered when DEMO_MODE=true; every result it produces is marked demo."""

from __future__ import annotations

import sys

from .base import Adapter, RunHandle, RunResult, RunSpec, jline

DEMO_SCRIPT = r'''
import json, sys, pathlib, fnmatch, time
req = json.loads(sys.stdin.read())
print(json.dumps({"event": "start", "text": "DEMO executor started (no AI involved)"}), flush=True)
root = pathlib.Path(".")
targets = [p for p in sorted(root.rglob("*.tex")) if any(fnmatch.fnmatch(p.as_posix(), pat) or pat in ("**", "**/*") for pat in req["write_scope"])]
changed = []
for p in targets[:1]:
    text = p.read_text()
    lines = [ln.rstrip() for ln in text.splitlines()]
    note = "% [DEMO MODE] placeholder edit for: " + req["goal"][:120].replace("\n", " ")
    p.write_text(note + "\n" + "\n".join(lines) + "\n")
    changed.append(p.as_posix())
    print(json.dumps({"event": "edit", "path": p.as_posix()}), flush=True)
time.sleep(0.2)
print(json.dumps({"event": "done", "changed": changed, "summary": "DEMO: added a marker comment and trimmed trailing whitespace"}), flush=True)
'''


class DemoAdapter(Adapter):
    name = "demo"
    label = "Demo executor"
    binary_setting = ""

    def resolve(self) -> str | None:
        return sys.executable

    def binary(self) -> str:
        return sys.executable

    def _health(self) -> dict:
        return {"status": "available", "installed": True, "version": "demo", "auth": "ok",
                "detail": "DEMO_MODE: fixed placeholder edits, no AI service is called"}

    def extra_ro_binds(self) -> list[str]:
        from app.sandbox import python_binds

        return python_binds()

    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        return [exe, "-I", "-c", DEMO_SCRIPT]

    def stdin_payload(self, spec: RunSpec) -> bytes:
        return spec.prompt.encode()  # the orchestrator sends JSON for the demo executor

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        ev = jline(line)
        if not ev:
            return []
        if ev.get("event") == "edit":
            return [{"type": "file_change", "text": ev["path"], "data": {"demo": True}}]
        if ev.get("event") == "done":
            handle.state["done"] = ev
            return [{"type": "result", "text": ev["summary"], "data": {"demo": True}}]
        return [{"type": "status", "text": ev.get("text", ""), "data": {"demo": True}}]

    def finalize(self, handle: RunHandle, exit_code: int | None) -> RunResult:
        done = handle.state.get("done")
        ok = exit_code == 0 and done is not None
        return RunResult(status="succeeded" if ok else "failed", exit_code=exit_code,
                         final_message=(done or {}).get("summary", ""), data={"demo": True})
