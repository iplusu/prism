from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

CONFLICT_MARKERS = ("<<<<<<< ", "=======", ">>>>>>> ")


def merge3(base: str, current: str, proposal: str) -> tuple[str, int]:
    """3-way text merge via `git merge-file`. Returns (merged_text,
    conflict_count); conflicting regions carry diff3 markers."""
    with tempfile.TemporaryDirectory(prefix="prism-merge-") as d:
        p = Path(d)
        (p / "current").write_text(current)
        (p / "base").write_text(base)
        (p / "proposal").write_text(proposal)
        r = subprocess.run(
            ["git", "merge-file", "-p", "--diff3", "-L", "current manuscript", "-L", "proposal base", "-L", "proposal",
             str(p / "current"), str(p / "base"), str(p / "proposal")],
            capture_output=True, timeout=60,
        )
        if r.returncode < 0 or r.returncode > 127:
            raise RuntimeError(f"git merge-file failed: {r.stderr.decode(errors='replace')}")
        return r.stdout.decode("utf-8", errors="replace"), r.returncode


def has_conflict_markers(text: str) -> bool:
    return any(line.startswith(CONFLICT_MARKERS) for line in text.splitlines()) and "<<<<<<< " in text
