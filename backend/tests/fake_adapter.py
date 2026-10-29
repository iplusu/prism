"""TEST FAKE adapter wrapping fake_cli.py (registered in place of a real tool
name only inside tests)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.adapters.base import Adapter, RunHandle, RunResult, RunSpec, jline

SCRIPT = Path(__file__).with_name("fake_cli.py")


class FakeAdapter(Adapter):
    label = "Fake CLI (test)"
    binary_setting = ""

    def __init__(self, name: str, behaviour: str):
        self.name = name
        self.behaviour = behaviour

    def resolve(self):
        return sys.executable

    def binary(self):
        return sys.executable

    def _health(self):
        return {"status": "available", "installed": True, "version": "fake", "auth": "ok", "detail": "test fake"}

    def extra_ro_binds(self) -> list[str]:
        from app.sandbox import python_binds

        return python_binds()

    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        # the script is copied next to the run dir so it is visible in the sandbox
        target = spec.run_dir / "fake_cli.py"
        target.write_text(SCRIPT.read_text())
        return [exe, "-I", str(target), self.behaviour]

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        ev = jline(line)
        if not ev:
            return []
        if ev.get("event") == "start":
            handle.state["pid"] = ev.get("pid")
        if ev.get("event") == "done":
            handle.state["done"] = ev
            return [{"type": "result", "text": ev["summary"], "data": {}}]
        return [{"type": "status", "text": json.dumps(ev), "data": {}}]

    def finalize(self, handle: RunHandle, exit_code):
        done = handle.state.get("done")
        ok = exit_code == 0 and done is not None
        return RunResult(status="succeeded" if ok else "failed", exit_code=exit_code,
                         final_message=(done or {}).get("summary", ""))

