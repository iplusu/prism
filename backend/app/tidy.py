"""Tidy a .tex file's indentation with latexindent. The result is only ever offered as a proposal (diff, verification,
explicit apply); nothing is written here. The file is formatted in an empty temporary directory with a temporary HOME,
so no project or user latexindent settings are read."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from .compile.latex import tex_bin_dir


class TidyError(RuntimeError):
    pass


def tidy(text: str) -> str:
    d = tex_bin_dir()
    exe = Path(d) / "latexindent" if d else None
    if exe is None or not exe.exists():
        raise TidyError("latexindent is not installed on this server")
    with tempfile.TemporaryDirectory(prefix="prism-tidy-") as tmp:
        src = Path(tmp) / "in.tex"
        src.write_text(text, encoding="utf-8")
        try:
            r = subprocess.run([str(exe), "in.tex"], cwd=tmp, capture_output=True, timeout=60,
                               env={"PATH": f"{d}:/usr/bin:/bin", "HOME": tmp})
        except (OSError, subprocess.TimeoutExpired) as e:
            raise TidyError(f"latexindent failed: {type(e).__name__}") from e
    if r.returncode != 0 or not r.stdout.strip():
        raise TidyError("latexindent failed: " + r.stderr.decode("utf-8", "replace")[-300:])
    return r.stdout.decode("utf-8")
