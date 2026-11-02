"""Word counts for requests like "keep it under 8000 words", measured with texcount (the standard LaTeX counter).
texcount counts running text, headings and captions; it skips math, the bibliography and most floats' content, and
journals may count differently, so the figure is a guide rather than the journal's own number."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from ..compile.latex import tex_bin_dir

_TARGET = re.compile(r"(\d[\d,]{0,6})\s*(?:words?\b|단어|자\b)", re.I)
_TOTAL = re.compile(r"^(\d+)\+(\d+)\+(\d+) .*File\(s\) total", re.M)
_SINGLE = re.compile(r"^(\d+)\+(\d+)\+(\d+) .*File: ", re.M)  # a project without \input has no "total" line


def target_words(request: str) -> int | None:
    """The word limit a request names ("8,000 words", "8000단어 이내"), or None."""
    m = _TARGET.search(request or "")
    n = int(m.group(1).replace(",", "")) if m else 0
    return n if 50 <= n <= 1_000_000 else None


def count_words(files: dict[str, bytes], main_file: str) -> dict | None:
    """{text, headers, captions, total} for main_file and everything it \\inputs, or None if texcount is unavailable or fails."""
    d = tex_bin_dir()
    exe = Path(d) / "texcount" if d else None
    if exe is None or not exe.exists() or main_file not in files:
        return None
    with tempfile.TemporaryDirectory(prefix="prism-words-") as tmp:
        root = Path(tmp)
        for rel, data in files.items():
            if not rel.endswith((".tex", ".sty", ".cls")):
                continue
            p = (root / rel).resolve()
            if root.resolve() not in p.parents:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        try:
            out = subprocess.run([str(exe), "-merge", "-inc", "-brief", main_file], cwd=root, capture_output=True,
                                 timeout=30, env={"PATH": f"{d}:/usr/bin:/bin", "HOME": tmp})
        except (OSError, subprocess.TimeoutExpired):
            return None
    text = out.stdout.decode("utf-8", "replace")
    m = _TOTAL.search(text) or _SINGLE.search(text)
    if not m:
        return None
    t, h, c = (int(x) for x in m.groups())
    return {"text": t, "headers": h, "captions": c, "total": t + h + c}


def note(counts: dict | None, target: int | None) -> str:
    if counts is None:
        return "unknown: the word counter (texcount) could not measure this project. Do not guess the word count."
    s = (f"about {counts['total']} words ({counts['text']} text + {counts['headers']} headings + {counts['captions']} captions; "
         "texcount: math, the bibliography and abstract metadata handling may differ from the journal's own count)")
    if target is None:
        return s + "."
    gap = counts["total"] - target
    return s + (f". Requested limit {target} words: " + (f"over by about {gap} words." if gap > 0 else f"met, about {-gap} words of room."))
