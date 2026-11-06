"""A PDF of a proposal with its changes marked (latexdiff: additions blue and underlined, deletions red and struck out).

`\\input`/`\\include` are inlined here, from the project's own files only, instead of with `latexdiff --flatten`,
which would read any path on the server. The marked source is compiled by the normal sandboxed compiler.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from ..compile.latex import compile_project, tex_bin_dir
from ..models import Project, Proposal
from . import service

_INPUT = re.compile(r"\\(?:input|include)\{([^}]+)\}")
MAX_DEPTH = 6


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def lock_for(prop_id: str) -> threading.Lock:
    """One build at a time per proposal (two clicks must not compile into the same directory)."""
    with _locks_guard:
        return _locks.setdefault(prop_id, threading.Lock())


def _inline(files: dict[str, bytes], path: str, depth: int = 0) -> str:
    text = files[path].decode("utf-8", errors="replace")
    if depth >= MAX_DEPTH:
        return text

    def sub(m: re.Match) -> str:
        name = m.group(1).strip()
        for cand in (name, name + ".tex"):
            if cand in files and cand.endswith(".tex"):
                return _inline(files, cand, depth + 1)
        return m.group(0)  # not a project file: left as is, the compiler reports it

    return _INPUT.sub(sub, text)


def marked_pdf_path(prop: Proposal, doc: str | None = None) -> Path:
    """One cache per document: the primary one keeps the old location, the others sit in a folder named after a hash of the file name."""
    base = service.pdir(prop.id) / "marked"
    if doc is None:
        return base / "output.pdf"
    return base / ("doc-" + hashlib.sha256(doc.encode()).hexdigest()[:12]) / "output.pdf"


def pick(prop: Proposal, project: Project, doc: str | None) -> str | None:
    """The document asked for (None = the primary one, which uses the original cache location); ValueError if the proposal has no such document."""
    base = service.store.files_at(prop.project_id, prop.base_revision)
    targets = service.verify_targets(project, base, service.candidate_files(prop, base), [f["path"] for f in prop.files])
    if doc is None or doc == targets[0]:
        return None
    if doc not in targets:
        raise ValueError(f"{doc} is not one of the documents this proposal changes")
    return doc


def build(prop: Proposal, project: Project, doc: str | None = None) -> dict:
    """Make (or reuse) the marked PDF of one document of the proposal (default: the primary one).
    Returns {"ok": bool, "error": str, "cached": bool, "target": the document}."""
    base = service.store.files_at(prop.project_id, prop.base_revision)
    cand = service.candidate_files(prop, base)
    targets = service.verify_targets(project, base, cand, [f["path"] for f in prop.files])
    main = doc or targets[0]  # the document this proposal changes (usually the main file)
    if main not in targets:
        raise ValueError(f"{main} is not one of the documents this proposal changes")
    out = marked_pdf_path(prop, None if main == targets[0] else main)
    marker = out.parent / "target.txt"
    # a PDF cached before the target was recorded was made from the main file
    if out.is_file() and (marker.read_text().strip() if marker.is_file() else project.main_file) == main:
        return {"ok": True, "error": "", "cached": True, "target": main}
    d = tex_bin_dir()
    exe = Path(d) / "latexdiff" if d else None
    if exe is None or not exe.exists():
        return {"ok": False, "error": "latexdiff is not installed on this server (tlmgr install latexdiff)", "cached": False}
    if main not in base or main not in cand:
        return {"ok": False, "error": f"main file {main!r} is missing in the base or the candidate", "cached": False}
    with tempfile.TemporaryDirectory(prefix="prism-latexdiff-") as tmp:
        t = Path(tmp)
        (t / "old.tex").write_text(_inline(base, main), encoding="utf-8")
        (t / "new.tex").write_text(_inline(cand, main), encoding="utf-8")
        try:
            r = subprocess.run([str(exe), "old.tex", "new.tex"], cwd=t, capture_output=True, timeout=120,
                               env={"PATH": f"{d}:/usr/bin:/bin", "HOME": tmp})
        except (OSError, subprocess.TimeoutExpired) as e:
            return {"ok": False, "error": f"latexdiff failed: {type(e).__name__}", "cached": False}
        diff = r.stdout.decode("utf-8", errors="replace")
        if r.returncode != 0 or "\\begin{document}" not in diff:
            return {"ok": False, "error": "latexdiff failed: " + r.stderr.decode("utf-8", "replace")[-400:], "cached": False}
    files = {k: v for k, v in cand.items() if not k.endswith(".tex") or k == main}
    marked = "prism-marked.tex"
    files[marked] = diff.encode("utf-8")
    bdir = service.pdir(prop.id) / "marked-build"
    try:
        res = compile_project(files, bdir, marked, project.engine)
        if res.status != "succeeded" or not res.pdf_path or not Path(res.pdf_path).is_file():
            tail = (res.error or res.log or "")[-400:]
            return {"ok": False, "error": f"the marked source did not compile ({res.status}): {tail}", "cached": False}
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(res.pdf_path, out)
        marker.write_text(main)
    finally:
        shutil.rmtree(bdir, ignore_errors=True)
    return {"ok": True, "error": "", "cached": False, "target": main}
