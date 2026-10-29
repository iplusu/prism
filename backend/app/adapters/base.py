"""Common machinery for external agent CLIs.

Each adapter builds an argv list (never a shell string) and sends the prompt
through stdin. Each run gets its own work directory, session id, log file and
time limit. The three CLIs have different event formats, so every adapter
parses its own stdout into normalized events:

    {"type": "init" | "message" | "tool" | "file_change" | "status" | "error" | "result",
     "text": str, "data": dict}
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..sandbox import ETC_FOR_NETWORK, Limits, SandboxSpec, binary_binds, bwrap_works, kill_tree, spawn
from ..security.redact import redact

AUTH_HINTS = ("not logged in", "authentication required", "please log in", "please login", "unauthorized",
              "invalid api key", "invalid_api_key", "401", "login required", "no credentials", "oauth token", "auth required")


@dataclass
class RunSpec:
    run_id: str
    workdir: Path  # copy of the snapshot; the only writable project location
    run_dir: Path  # logs and tool-private output (outside workdir)
    prompt: str
    timeout_seconds: int
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class RunResult:
    status: str  # succeeded | failed | timeout | cancelled | needs_auth | unavailable
    exit_code: int | None = None
    final_message: str = ""
    session_id: str = ""
    error: str = ""
    isolation: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class RunHandle:
    spec: RunSpec
    proc: subprocess.Popen | None
    isolation: str
    started: float = field(default_factory=time.monotonic)
    events: queue.Queue = field(default_factory=queue.Queue)
    state: dict = field(default_factory=dict)  # adapter-specific parse state
    stderr_tail: list[str] = field(default_factory=list)
    timed_out: bool = False
    cancelled: bool = False
    threads: list[threading.Thread] = field(default_factory=list)


class AdapterError(RuntimeError):
    pass


class Adapter:
    name = "base"
    label = "Base"
    binary_setting = ""
    #: flags this adapter relies on; verified against the installed `--help`
    required_flags: tuple[str, ...] = ()
    help_args: tuple[str, ...] = ("--help",)

    _health_cache: dict = {}

    # ---------------------------------------------------------------- info ---
    def binary(self) -> str:
        return getattr(get_settings(), self.binary_setting)

    def resolve(self) -> str | None:
        b = self.binary()
        return shutil.which(b) if b else None

    def tool_home(self) -> Path:
        p = get_settings().data_dir / "tool-homes" / self.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def tool_env(self) -> dict[str, str]:
        """Environment for the tool process (also used for health checks)."""
        return {}

    def capabilities(self) -> dict:
        return {"streaming_events": True, "structured_result": True, "session_id": True, "cancel": True,
                "writes_files": True, "runs_shell": False, "prompt_via": "stdin"}

    def health(self, force: bool = False) -> dict:
        cached = self._health_cache.get(self.name)
        if cached and not force and time.time() - cached["checked_at"] < 60:
            return cached
        h = self._health()
        h["checked_at"] = time.time()
        h["name"] = self.name
        h["label"] = self.label
        self._health_cache[self.name] = h
        return h

    def _run_quick(self, args: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8", "NO_COLOR": "1",
               "HOME": str(self.tool_home()), **self.tool_env()}
        return subprocess.run(args, capture_output=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)

    def _health(self) -> dict:
        exe = self.resolve()
        if not exe:
            return {"status": "unavailable", "installed": False, "version": "", "auth": "unknown",
                    "detail": f"'{self.binary()}' not found on PATH (set {self.binary_setting.upper()})"}
        try:
            v = self._run_quick([exe, "--version"])
            version = (v.stdout or v.stderr).decode(errors="replace").strip().splitlines()[0][:120] if (v.stdout or v.stderr) else ""
        except Exception as e:
            return {"status": "error", "installed": True, "version": "", "auth": "unknown", "detail": f"--version failed: {e}"}
        try:
            h = self._run_quick([exe, *self.help_args])
            help_text = (h.stdout + h.stderr).decode(errors="replace")
        except Exception as e:
            return {"status": "error", "installed": True, "version": version, "auth": "unknown", "detail": f"--help failed: {e}"}
        missing = [f for f in self.required_flags if f not in help_text]
        if missing:
            return {"status": "incompatible", "installed": True, "version": version, "auth": "unknown",
                    "detail": "installed version lacks required options: " + ", ".join(missing)}
        auth, detail = self.check_auth(exe)
        status = {"ok": "available", "missing": "needs_auth"}.get(auth, "available")
        return {"status": status, "installed": True, "version": version, "auth": auth, "detail": detail}

    def check_auth(self, exe: str) -> tuple[str, str]:
        """Return ("ok"|"missing"|"unknown", detail)."""
        return "unknown", "authentication cannot be checked without running a task"

    # ----------------------------------------------------------------- run ---
    def build_argv(self, exe: str, spec: RunSpec) -> list[str]:
        raise NotImplementedError

    def stdin_payload(self, spec: RunSpec) -> bytes:
        return spec.prompt.encode("utf-8")

    def parse_line(self, handle: RunHandle, line: str) -> list[dict]:
        raise NotImplementedError

    def finalize(self, handle: RunHandle, exit_code: int | None) -> RunResult:
        raise NotImplementedError

    def start(self, spec: RunSpec) -> RunHandle:
        s = get_settings()
        exe = self.resolve()
        if not exe:
            raise AdapterError(f"{self.label} is not installed")
        spec.run_dir.mkdir(parents=True, exist_ok=True)
        home = self.tool_home()
        env = {
            "PATH": ":".join(dict.fromkeys([os.path.dirname(exe), *self._path_extra(), "/usr/local/bin", "/usr/bin", "/bin"])),
            "HOME": str(home), "LANG": "C.UTF-8", "NO_COLOR": "1", "TERM": "dumb",
            "TMPDIR": "/tmp", "CI": "1",
            # Recursion guard: a tool must not call back into the orchestrator.
            "PRISM_AGENT_DEPTH": "1",
            **self.tool_env(),
        }
        want_sandbox = s.tool_sandbox == "bwrap"
        ok, why = bwrap_works()
        if want_sandbox and not ok:
            if not s.allow_unsandboxed_tools:
                raise AdapterError(f"tool sandbox unavailable ({why}); set ALLOW_UNSANDBOXED_TOOLS=true to run without isolation")
            want_sandbox = False
        elif not want_sandbox and not s.allow_unsandboxed_tools:
            raise AdapterError("TOOL_SANDBOX=none requires ALLOW_UNSANDBOXED_TOOLS=true")
        ro = binary_binds(exe) + self.extra_ro_binds() + [b for b in s.tool_extra_ro_binds.split(":") if b]
        sb = SandboxSpec(
            argv=self.build_argv(exe, spec), workdir=spec.workdir, env=env, ro_binds=ro,
            rw_binds=[str(home), str(spec.run_dir), *self._extra_rw()], etc_files=ETC_FOR_NETWORK + ["/etc/passwd", "/etc/group"],
            network=True,  # the CLIs call their vendors' APIs
            limits=Limits(max_processes=None, max_file_mb=512),
        )
        proc = spawn(sb, sandboxed=want_sandbox, stdin=subprocess.PIPE)
        handle = RunHandle(spec=spec, proc=proc, isolation="bwrap" if want_sandbox else "none")
        log_fh = open(spec.run_dir / "tool.log", "a", encoding="utf-8")
        handle.state["log"] = log_fh
        log_fh.write(f"# {self.name} run {spec.run_id} isolation={handle.isolation}\n# argv: {json.dumps([redact(a) for a in sb.argv])}\n")

        def feed():
            try:
                assert proc.stdin
                proc.stdin.write(self.stdin_payload(spec))
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        def read_out():
            assert proc.stdout
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                log_fh.write("OUT " + redact(line) + "\n")
                log_fh.flush()
                try:
                    for ev in self.parse_line(handle, line):
                        handle.events.put(ev)
                except Exception as e:  # malformed output must not kill the run
                    handle.events.put({"type": "error", "text": f"unparsed output: {e}", "data": {}})

        def read_err():
            assert proc.stderr
            for raw in proc.stderr:
                line = redact(raw.decode("utf-8", errors="replace").rstrip("\n"))
                log_fh.write("ERR " + line + "\n")
                handle.stderr_tail = (handle.stderr_tail + [line])[-50:]

        for fn in (feed, read_out, read_err):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            handle.threads.append(t)
        return handle

    def extra_ro_binds(self) -> list[str]:
        return []

    def _path_extra(self) -> list[str]:
        node = shutil.which("node")
        return [os.path.dirname(node)] if node else []

    def _extra_rw(self) -> list[str]:
        return []

    def stream_events(self, handle: RunHandle, should_cancel=lambda: False, poll: float = 0.2) -> Iterator[dict]:
        """Yield normalized events until the process exits, the time limit is
        reached, or cancellation is requested."""
        assert handle.proc
        while True:
            try:
                yield handle.events.get(timeout=poll)
                continue
            except queue.Empty:
                pass
            if handle.proc.poll() is not None:
                for t in handle.threads[1:]:
                    t.join(timeout=5)
                while not handle.events.empty():
                    yield handle.events.get()
                return
            if time.monotonic() - handle.started > handle.spec.timeout_seconds:
                handle.timed_out = True
                kill_tree(handle.proc)
                yield {"type": "error", "text": f"time limit of {handle.spec.timeout_seconds}s reached; process stopped", "data": {}}
            elif should_cancel():
                self.cancel(handle)
                yield {"type": "status", "text": "cancelled by user; process stopped", "data": {}}

    def cancel(self, handle: RunHandle) -> None:
        handle.cancelled = True
        if handle.proc:
            kill_tree(handle.proc)

    def collect_result(self, handle: RunHandle) -> RunResult:
        assert handle.proc
        code = handle.proc.wait()
        for t in handle.threads:
            t.join(timeout=5)
        log = handle.state.pop("log", None)
        if log:
            log.write(f"# exit code {code}\n")
            log.close()
        if handle.cancelled:
            r = RunResult(status="cancelled", exit_code=code, error="cancelled")
        elif handle.timed_out:
            r = RunResult(status="timeout", exit_code=code, error=f"exceeded {handle.spec.timeout_seconds}s")
        else:
            r = self.finalize(handle, code)
            blob = (r.error + " " + " ".join(handle.stderr_tail)).lower()
            if r.status != "succeeded" and any(h in blob for h in AUTH_HINTS):
                r.status = "needs_auth"
        r.isolation = handle.isolation
        r.session_id = r.session_id or handle.state.get("session_id", "") or handle.spec.session_id
        if r.status != "succeeded" and not r.error:
            r.error = "\n".join(handle.stderr_tail[-5:])[:1000] or f"exit code {code}"
        r.error = redact(r.error)
        return r


def jline(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        v = json.loads(line)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None
