"""Deterministic manuscript checks (no LLM): every issue points at a file and
line and is reproducible. Judgement calls (reviewer report, terminology
consistency, claims that need a citation) go through the AI pipeline instead.
"""

from __future__ import annotations

import posixpath
import re

from .agent.context import _BIBENTRY, _CITE, _LABEL
from .proposals.analysis import _REF

_GRAPHIC = re.compile(r"\\includegraphics\*?(?:\[[^\]]*\])?\{([^}]+)\}")
_INPUT = re.compile(r"\\(input|include|subfile)\{([^}]+)\}")
_GRAPHICSPATH = re.compile(r"\\graphicspath\{((?:\{[^}]*\})+)\}")
_BIBITEM = re.compile(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}")
_TODO = re.compile(r"\\todo\b|\bTODO\b|\bFIXME\b|\bXXX\b")
_GRAPHIC_EXT = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".svg")


def _strip_comments(text: str) -> str:
    # drop % comments but keep line structure so line numbers stay right
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in text.split("\n"))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def check(files: dict[str, bytes], main_file: str = "main.tex") -> dict:
    tex = {p: _strip_comments(b.decode("utf-8", errors="replace")) for p, b in files.items() if p.endswith(".tex")}
    bibs = {p: b.decode("utf-8", errors="replace") for p, b in files.items() if p.endswith(".bib")}
    issues: list[dict] = []

    def add(kind: str, severity: str, path: str, line: int | None, message: str):
        issues.append({"kind": kind, "severity": severity, "path": path, "line": line, "message": message})

    bib_keys: dict[str, tuple[str, int]] = {}
    for path, text in bibs.items():
        for m in _BIBENTRY.finditer(text):
            if m.group(1).lower() in ("comment", "string", "preamble"):
                continue
            key = m.group(2)
            if key in bib_keys:
                add("duplicate_bib_key", "error", path, _line(text, m.start()),
                    f"citation key '{key}' is defined twice (also {bib_keys[key][0]}:{bib_keys[key][1]})")
            else:
                bib_keys[key] = (path, _line(text, m.start()))

    for path, text in tex.items():  # manual thebibliography
        for m in _BIBITEM.finditer(text):
            bib_keys.setdefault(m.group(1).strip(), (path, _line(text, m.start())))

    labels: dict[str, tuple[str, int]] = {}
    cited: set[str] = set()
    referenced: set[str] = set()
    graphic_dirs = [""]
    for path, text in tex.items():
        for m in _GRAPHICSPATH.finditer(text):
            graphic_dirs += [d.rstrip("/") for d in re.findall(r"\{([^}]*)\}", m.group(1))]
    for path, text in sorted(tex.items()):
        for m in _LABEL.finditer(text):
            name = m.group(1).strip()
            if name in labels:
                add("duplicate_label", "error", path, _line(text, m.start()),
                    f"label '{name}' is defined twice (also {labels[name][0]}:{labels[name][1]})")
            else:
                labels[name] = (path, _line(text, m.start()))
        for m in _CITE.finditer(text):
            for key in (k.strip() for k in m.group(1).split(",")):
                if not key:
                    continue
                cited.add(key)
                if key not in bib_keys:
                    add("undefined_citation", "error", path, _line(text, m.start()),
                        f"\\cite{{{key}}} has no bibliography entry" if bib_keys else
                        f"\\cite{{{key}}} but the project has no .bib file")
        for m in _REF.finditer(text):
            for name in (k.strip() for k in m.group(1).split(",")):
                referenced.add(name)
        for m in _GRAPHIC.finditer(text):
            target = m.group(1).strip()
            if not any(posixpath.normpath(posixpath.join(d, target + ext)) in files
                       for d in graphic_dirs for ext in _GRAPHIC_EXT):
                add("missing_graphic", "error", path, _line(text, m.start()), f"figure file '{target}' not found")
        for m in _INPUT.finditer(text):
            target = m.group(2).strip()
            cands = [target, target + ".tex"]
            if not any(posixpath.normpath(c) in files for c in cands):
                add("missing_input", "error", path, _line(text, m.start()), f"\\{m.group(1)}{{{target}}}: file not found")
        for m in _TODO.finditer(text):
            add("todo", "warning", path, _line(text, m.start()), "unresolved TODO/FIXME marker")

    for path, text in sorted(tex.items()):
        for m in _REF.finditer(text):
            for name in (k.strip() for k in m.group(1).split(",")):
                if name and name not in labels:
                    add("undefined_ref", "error", path, _line(text, m.start()), f"reference to undefined label '{name}'")
    for name, (path, line) in sorted(labels.items()):
        if name not in referenced:
            add("unreferenced_label", "info", path, line, f"label '{name}' is never referenced")
    for key, (path, line) in sorted(bib_keys.items()):
        if key not in cited:
            add("unused_bib_entry", "info", path, line, f"bibliography entry '{key}' is never cited")

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["path"], i["line"] or 0))
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in order}
    return {"issues": issues, "counts": counts}


_CHKTEX = re.compile(r"^(.+?):(\d+):(Error|Warning|Message):(\d+):(.*)$")


def lint(files: dict[str, bytes], limit: int = 200) -> dict:
    """chktex's typographic and syntax warnings for the .tex files, as review issues. {"issues": [...], "available": bool}.
    Style hints are 'info' (hidden by default in the UI); chktex 'Error' lines are 'warning'. Needs chktex in the TeX bin directory."""
    import subprocess
    import tempfile
    from pathlib import Path

    from .compile.latex import tex_bin_dir
    d = tex_bin_dir()
    exe = Path(d) / "chktex" if d else None
    if exe is None or not exe.exists():
        return {"issues": [], "available": False}
    issues: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="prism-lint-") as tmp:
        root = Path(tmp).resolve()
        names = []
        for rel, data in sorted(files.items()):
            if not rel.endswith(".tex"):
                continue
            p = (root / rel).resolve()
            if root not in p.parents:
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            names.append(rel)
        if not names:
            return {"issues": [], "available": True}
        try:
            out = subprocess.run([str(exe), "-q", "-v0", "-f", "%f:%l:%k:%n:%m\n", *names], cwd=root, capture_output=True,
                                 timeout=60, env={"PATH": f"{d}:/usr/bin:/bin", "HOME": tmp})
        except (OSError, subprocess.TimeoutExpired):
            return {"issues": [], "available": False}
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        m = _CHKTEX.match(line)
        if not m:
            continue
        path, ln, kind, num, msg = m.groups()
        issues.append({"kind": "chktex", "severity": "warning" if kind == "Error" else "info", "path": path.removeprefix("./"),
                       "line": int(ln), "message": f"chktex {num}: {msg.strip()}"})
        if len(issues) >= limit:
            break
    return {"issues": issues, "available": True}
