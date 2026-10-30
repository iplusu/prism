"""SyncTeX lookups: source position <-> PDF position for a finished build.

The compile keeps `output.synctex.gz` next to `output.pdf`. The official `synctex` tool reads it (inside the
same sandbox as the compile) and its text output is parsed here. Only numbers and a project-relative path
reach its command line."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from .builds import build_dir
from .config import get_settings
from .compile.latex import sandbox_status, tex_status
from .sandbox import Limits, SandboxSpec, kill_tree, spawn

TIMEOUT = 15.0
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./ +\-]+$")


class SyncTexError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _run(bid: str, args: list[str]) -> str:
    bdir = build_dir(bid)
    if not (bdir / "output.synctex.gz").exists():
        raise SyncTexError("this build has no SyncTeX data (compile again)", 404)
    ts, sb = tex_status(), sandbox_status()
    if not ts["available"]:
        raise SyncTexError(ts["detail"], 503)
    tool = os.path.join(ts["bin_dir"], "synctex")
    if not os.path.exists(tool):
        raise SyncTexError("the synctex tool is not installed (tlmgr install synctex)", 503)
    if sb["mode"] == "blocked":
        raise SyncTexError(sb["detail"], 503)
    out_file = bdir / f"_synctex-{os.getpid()}-{time.monotonic_ns()}.out"
    spec = SandboxSpec(argv=[tool, *args], workdir=bdir, env={"PATH": ts["bin_dir"], "HOME": "/tmp"},
                       ro_binds=[ts["bin_dir"], os.path.dirname(os.path.dirname(os.path.realpath(ts["bin_dir"])))],
                       network=False, limits=Limits(memory_mb=512, cpu_seconds=20, max_processes=get_settings().compile_max_processes))
    try:
        with open(out_file, "wb") as f:
            proc = spawn(spec, sandboxed=sb["mode"] == "bwrap", stdout=f, stderr=subprocess.STDOUT)
            t0 = time.monotonic()
            while proc.poll() is None:
                if time.monotonic() - t0 > TIMEOUT:
                    kill_tree(proc)
                    raise SyncTexError("synctex timed out")
                time.sleep(0.02)
        text = out_file.read_text(errors="replace")
    finally:
        out_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise SyncTexError("synctex failed")
    return text


def _fields(text: str) -> list[dict]:
    """Result blocks as dicts (a lookup may return several)."""
    blocks, cur = [], None
    for ln in text.splitlines():
        if ln.startswith("SyncTeX result begin"):
            cur = {}
        elif ln.startswith("SyncTeX result end"):
            if cur:
                blocks.append(cur)
            cur = None
        elif cur is not None and ":" in ln:
            k, v = ln.split(":", 1)
            if k in ("Page", "x", "y", "h", "v", "W", "H", "Line", "Column", "Input"):
                cur.setdefault(k, v.strip())
    return blocks


def forward(bid: str, path: str, line: int, col: int = 1) -> list[dict]:
    """Rectangles (PDF points from the page's top-left) that a source line produced."""
    if not _SAFE_PATH.match(path) or ".." in path.split("/") or path.startswith("/"):
        raise SyncTexError("bad path", 400)
    text = _run(bid, ["view", "-i", f"{max(1, int(line))}:{max(0, int(col))}:{path}", "-o", "output.pdf"])
    out = []
    for b in _fields(text):
        try:
            h, v, w, hh = float(b["h"]), float(b["v"]), float(b["W"]), float(b["H"])
            out.append({"page": int(b["Page"]), "x": h, "y": v - hh, "w": w, "h": hh})
        except (KeyError, ValueError):
            continue
    return out


def inverse(bid: str, page: int, x: float, y: float, source_files: set[str]) -> dict | None:
    """Source file and line at a PDF position; None when it maps to something that is not a project file."""
    if page < 1 or not (0 <= x < 5000 and 0 <= y < 5000):
        raise SyncTexError("bad position", 400)
    text = _run(bid, ["edit", "-o", f"{int(page)}:{x:.2f}:{y:.2f}:output.pdf"])
    for b in _fields(text):
        raw = b.get("Input", "")
        rel = raw.split("/src/", 1)[1] if "/src/" in raw else raw
        rel = re.sub(r"^(\./)+", "", rel).replace("/./", "/")
        if rel in source_files and b.get("Line", "").lstrip("-").isdigit():
            return {"path": rel, "line": max(1, int(b["Line"])), "column": max(0, int(b.get("Column", "0") or 0))}
    return None
