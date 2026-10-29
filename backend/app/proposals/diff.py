"""Line diff with addressable hunks so a subset of changes can be applied."""

from __future__ import annotations

import difflib


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def hunks(base: str, new: str, context: int = 3) -> list[dict]:
    a, b = _lines(base), _lines(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out = []
    for idx, (tag, i1, i2, j1, j2) in enumerate(op for op in sm.get_opcodes() if op[0] != "equal"):
        lines = []
        for ln in a[max(0, i1 - context):i1]:
            lines.append({"t": " ", "s": ln.rstrip("\n")})
        for ln in a[i1:i2]:
            lines.append({"t": "-", "s": ln.rstrip("\n")})
        for ln in b[j1:j2]:
            lines.append({"t": "+", "s": ln.rstrip("\n")})
        for ln in a[i2:i2 + context]:
            lines.append({"t": " ", "s": ln.rstrip("\n")})
        out.append({"id": idx, "kind": tag, "base_start": i1 + 1, "base_end": i2, "new_start": j1 + 1, "new_end": j2,
                    "context_before": min(context, i1), "lines": lines})
    return out


def apply_hunks(base: str, new: str, selected: set[int]) -> str:
    a, b = _lines(base), _lines(new)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[str] = []
    idx = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(a[i1:i2])
            continue
        out.extend(b[j1:j2] if idx in selected else a[i1:i2])
        idx += 1
    return "".join(out)


def stats(base: str, new: str) -> dict:
    added = removed = 0
    for ln in difflib.unified_diff(_lines(base), _lines(new), n=0):
        if ln.startswith("+") and not ln.startswith("+++"):
            added += 1
        elif ln.startswith("-") and not ln.startswith("---"):
            removed += 1
    return {"added": added, "removed": removed}
