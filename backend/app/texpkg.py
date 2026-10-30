"""Missing LaTeX packages: find what a failed build asked for and install it with tlmgr.

Only a file name that a real build log of the project reported as "not found" is resolved, through
`tlmgr search --global --file`, to a package of the official TeX Live repository; only that package
is installed. No shell is involved and no name comes from the user except that file name.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

from .compile.latex import tex_bin_dir

# `! LaTeX Error: File `titlesec.sty' not found.`  (also .cls, .def, ...), plus the pdfTeX/biber style
_MISSING = re.compile(r"LaTeX Error: File [`'\"]([A-Za-z0-9._+-]+\.(?:sty|cls|clo|def|cfg|fd|bst|bbx|cbx|lbx|ldf|tex))['\"] not found")
_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,80}\.(?:sty|cls|clo|def|cfg|fd|bst|bbx|cbx|lbx|ldf|tex)$")
_PKG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,60}$")
_lock = threading.Lock()  # one tlmgr at a time (it locks the installation itself)


class TexPackageError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def missing_files(log: str) -> list[str]:
    """Files a build reported as missing, in order of first appearance."""
    seen: list[str] = []
    for m in _MISSING.finditer(log or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def is_safe_file(name: str) -> bool:
    return bool(_SAFE_FILE.match(name))


def _tlmgr() -> list[str]:
    d = tex_bin_dir()
    exe = Path(d) / "tlmgr" if d else None
    if not exe or not exe.exists():
        raise TexPackageError("tlmgr is not available on this server", 503)
    return [str(exe)]


def _run(args: list[str], timeout: float) -> str:
    env = {"HOME": os.environ.get("HOME", ""), "PATH": f"{tex_bin_dir()}:/usr/bin:/bin", "LANG": "C"}
    try:
        r = subprocess.run([*_tlmgr(), *args], capture_output=True, text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise TexPackageError("tlmgr timed out", 504) from e
    if r.returncode != 0:
        raise TexPackageError(f"tlmgr failed: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def parse_search(out: str, filename: str) -> list[str]:
    """Package names whose file list contains exactly this file name (tlmgr search output)."""
    pkgs: list[str] = []
    cur = None
    for line in out.splitlines():
        if line and not line[0].isspace() and line.rstrip().endswith(":"):
            cur = line.rstrip()[:-1]
        elif cur and line.strip().endswith("/" + filename) and _PKG.match(cur) and cur not in pkgs:
            pkgs.append(cur)
    return pkgs


def find_package(filename: str) -> str | None:
    if not is_safe_file(filename):
        raise TexPackageError("not a LaTeX support file name", 400)
    pkgs = parse_search(_run(["search", "--global", "--file", f"/{filename}"], 60), filename)
    return pkgs[0] if pkgs else None


def installed(filename: str) -> bool:
    exe = Path(tex_bin_dir() or "") / "kpsewhich"
    if not exe.exists():
        return False
    r = subprocess.run([str(exe), filename], capture_output=True, text=True, timeout=20, env={"HOME": os.environ.get("HOME", ""), "PATH": f"{tex_bin_dir()}:/usr/bin:/bin"})
    return bool(r.stdout.strip())


def install_for(filename: str) -> dict:
    """Install the TeX Live package that provides `filename`; verify the file is found afterwards."""
    with _lock:
        if installed(filename):
            return {"file": filename, "package": None, "installed": True, "note": "already available"}
        pkg = find_package(filename)
        if not pkg:
            raise TexPackageError(f"no TeX Live package provides {filename}; it may be a file of your own project", 404)
        _run(["install", pkg], 300)
        if not installed(filename):
            raise TexPackageError(f"installed {pkg}, but {filename} is still not found", 502)
        return {"file": filename, "package": pkg, "installed": True, "note": ""}
