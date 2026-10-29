"""Process isolation helpers.

`bwrap` (bubblewrap) is used when available: new user/pid/ipc/uts/net
namespaces, a read-only view of system directories, only the job's work
directory writable, a private /tmp, cleared environment, die-with-parent.
Resource limits are applied with setrlimit before exec. Processes are started
in their own session so the whole tree can be killed.

If bwrap cannot be used, callers decide whether to refuse (default) or to run
with rlimits only — and must report that the run was not isolated.
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


@dataclass
class Limits:
    memory_mb: int | None = None  # RLIMIT_AS
    cpu_seconds: int | None = None
    max_processes: int | None = None
    max_file_mb: int | None = None
    open_files: int = 1024


@dataclass
class SandboxSpec:
    argv: list[str]
    workdir: Path  # bound read-write at the same path
    env: dict[str, str] = field(default_factory=dict)
    ro_binds: list[str] = field(default_factory=list)
    rw_binds: list[str] = field(default_factory=list)
    etc_files: list[str] = field(default_factory=list)
    network: bool = False
    limits: Limits = field(default_factory=Limits)


SYSTEM_RO = ["/usr", "/opt"]
ETC_FOR_NETWORK = [
    "/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl", "/etc/ca-certificates",
    "/etc/pki", "/etc/host.conf", "/etc/gai.conf", "/etc/localtime",
]


@lru_cache
def bwrap_path() -> str | None:
    return shutil.which("bwrap")


@lru_cache
def bwrap_works() -> tuple[bool, str]:
    b = bwrap_path()
    if not b:
        return False, "bubblewrap (bwrap) is not installed"
    try:
        r = subprocess.run(
            [b, "--unshare-all", "--die-with-parent", *_root_binds(), "--proc", "/proc", "--dev", "/dev",
             "--", "/usr/bin/true"],
            capture_output=True, timeout=15,
        )
    except Exception as e:  # pragma: no cover
        return False, f"bwrap failed to start: {e}"
    if r.returncode != 0:
        return False, "bwrap cannot create namespaces here: " + r.stderr.decode(errors="replace").strip()[:300]
    return True, "bwrap namespaces available"


def _root_binds() -> list[str]:
    args: list[str] = []
    for d in SYSTEM_RO:
        if os.path.isdir(d):
            args += ["--ro-bind", d, d]
    for d in ("/bin", "/sbin", "/lib", "/lib64", "/lib32"):
        if os.path.islink(d):
            args += ["--symlink", os.readlink(d), d]
        elif os.path.isdir(d):
            args += ["--ro-bind", d, d]
    return args


def build_bwrap_argv(spec: SandboxSpec) -> list[str]:
    b = bwrap_path()
    assert b
    args = [b, "--unshare-user", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"]
    if not spec.network:
        args.append("--unshare-net")
    args += ["--die-with-parent", "--new-session", "--clearenv"]
    args += _root_binds()
    for e in spec.etc_files:
        if os.path.exists(e):
            args += ["--ro-bind", e, e]
    seen = set()
    for given in spec.ro_binds:
        # Bind both the path as given (it may traverse symlinked directories)
        # and its resolved location.
        for d in dict.fromkeys([os.path.abspath(given), os.path.realpath(given)]):
            if d in seen or not os.path.exists(d) or any(d == s or d.startswith(s + "/") for s in SYSTEM_RO):
                continue
            seen.add(d)
            args += ["--ro-bind", d, d]
    args += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for d in spec.rw_binds:
        args += ["--bind", d, d]
    args += ["--bind", str(spec.workdir), str(spec.workdir), "--chdir", str(spec.workdir)]
    for k, v in spec.env.items():
        args += ["--setenv", k, v]
    args.append("--")
    return args + spec.argv


def _preexec(limits: Limits):
    def fn():  # runs in the child between fork and exec
        os.setsid()
        if limits.memory_mb:
            b = limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (b, b))
        if limits.cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 5))
        if limits.max_processes:
            resource.setrlimit(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        if limits.max_file_mb:
            b = limits.max_file_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (b, b))
        resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return fn


def spawn(spec: SandboxSpec, *, sandboxed: bool, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
          stderr=subprocess.PIPE) -> subprocess.Popen:
    """Start the process. Arguments are passed as an argv list — never through
    a shell."""
    if sandboxed:
        argv = build_bwrap_argv(spec)
        env = {"PATH": "/usr/bin:/bin"}
    else:
        argv = spec.argv
        env = dict(spec.env)
    return subprocess.Popen(
        argv, cwd=spec.workdir, env=env, stdin=stdin, stdout=stdout, stderr=stderr,
        preexec_fn=_preexec(spec.limits), close_fds=True,
    )


def kill_tree(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Terminate the whole process group (bwrap's --die-with-parent and the
    pid namespace take care of descendants inside the sandbox)."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass


def python_binds() -> list[str]:
    """Interpreter locations for adapters that run Python scripts."""
    import sys

    out = [sys.prefix, sys.base_prefix]
    if os.path.islink(sys.executable):
        target = os.readlink(sys.executable)
        if os.path.isabs(target):
            out.append(os.path.dirname(os.path.dirname(target)))
    return out


def binary_binds(binary: str) -> list[str]:
    """Directories needed to run an installed CLI inside the sandbox: its real
    install location and, for node-based CLIs, the node runtime."""
    path = shutil.which(binary) or binary
    if not os.path.exists(path):
        return []
    binds = [os.path.dirname(os.path.abspath(path))]
    # Follow the symlink chain (e.g. ~/.local/bin/x -> npm/bin/x -> ../lib/...)
    # and expose every hop's directory.
    hop = os.path.abspath(path)
    for _ in range(20):
        if not os.path.islink(hop):
            break
        target = os.readlink(hop)
        hop = os.path.normpath(os.path.join(os.path.dirname(hop), target))
        binds.append(os.path.dirname(hop))
    real = os.path.realpath(path)
    parts = real.split("/")
    if "node_modules" in parts:
        idx = parts.index("node_modules")
        binds.append("/".join(parts[:idx + 1]))
    else:
        binds.append(os.path.dirname(real))
    try:
        with open(real, "rb") as fh:
            first = fh.readline(200)
        if first.startswith(b"#!") and b"node" in first:
            node = shutil.which("node")
            if node:
                binds.append(os.path.dirname(os.path.dirname(os.path.realpath(node))))
                binds.append(os.path.dirname(node))
    except OSError:
        pass
    return binds
