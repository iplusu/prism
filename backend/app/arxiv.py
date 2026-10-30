"""arXiv submission preflight and source package.

Every rule below comes from arXiv's own help pages (info.arxiv.org/help/faq/mistakes.html and
info.arxiv.org/help/submit_tex.html); the rule's source wording is in each message. It cannot promise that arXiv
accepts a paper (arXiv's TeX Live and its automatic main-file detection are the real test), only catch the
documented causes of failure before upload."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from .review import _BIBITEM, _GRAPHIC, _GRAPHICSPATH, _INPUT, _line, _strip_comments  # noqa: F401

_AUX_EXT = (".aux", ".log", ".dvi", ".out", ".toc", ".blg", ".fls", ".fdb_latexmk", ".synctex.gz")
_PDF_FIG = (".pdf", ".png", ".jpg", ".jpeg")
_BADNAME = re.compile(r"[\s&]")
_BIBLIO = re.compile(r"\\bibliography\{([^}]+)\}")
_BIBLATEX = re.compile(r"\\usepackage(?:\[([^\]]*)\])?\{biblatex\}")
_ABS = re.compile(r"^(/|[A-Za-z]:[\\/]|~)")


def _stem(main: str) -> str:
    return posixpath.splitext(posixpath.basename(main))[0]


def check(files: dict[str, bytes], main_file: str, engine: str, tex_version: str = "") -> dict:
    tex = {p: _strip_comments(b.decode("utf-8", errors="replace")) for p, b in files.items() if p.endswith(".tex")}
    issues: list[dict] = []

    def add(severity: str, kind: str, path: str, line: int | None, message: str):
        issues.append({"kind": kind, "severity": severity, "path": path, "line": line, "message": message})

    if engine in ("xelatex", "lualatex"):
        add("error", "engine", main_file, None,
            f"The project is set to {engine}. arXiv: \"does not support processing with: XeTeX and its variants "
            "including LuaTeX\". Use pdflatex (or latex) for the submission.")

    for p in sorted(files):
        base = posixpath.basename(p)
        if any(part.startswith(".") for part in p.split("/")):
            add("warning", "hidden_file", p, None, "Hidden files and directories are deleted by arXiv (\"Hidden files will be deleted upon announcement\").")
        if _BADNAME.search(p):
            add("error", "filename", p, None, "arXiv: \"Do not use spaces in filenames\"; avoid symbols such as & in file and directory names.")
        if p.lower().endswith(_AUX_EXT) or (p == _stem(main_file) + ".pdf"):
            add("warning", "aux_file", p, None, "Auxiliary/output files (.aux, .log, .dvi, the compiled PDF, ...) \"will be automatically removed\"; leave them out.")
        if base.lower() in ("psfig.sty",):
            add("error", "psfig", p, None, "arXiv: \"You may not include your own psfig.sty, as this will cause your source to fail.\"")
        if p.lower().endswith(".pdf") and (b"/JavaScript" in files[p] or b"/JS " in files[p] or b"/JS(" in files[p]):
            add("error", "javascript", p, None, "This PDF contains JavaScript; arXiv: \"Submissions with embedded JavaScript are automatically rejected.\"")

    uses_bibtex = False
    for path, text in tex.items():
        for m in _GRAPHIC.finditer(text):
            name = m.group(1).strip()
            ln = _line(text, m.start())
            if _ABS.match(name):
                add("error", "absolute_path", path, ln, f"Absolute path '{name}'. arXiv: \"you must use relative filenames instead of absolute filenames\".")
            if _BADNAME.search(name):
                add("error", "filename", path, ln, f"'{name}' has a space or '&'. arXiv: \"Do not use spaces in filenames, in either your figure inclusion commands or directory names.\"")
            ext = posixpath.splitext(name)[1].lower()
            if ext in (".eps", ".ps") and engine == "pdflatex":
                add("error", "eps_figure", path, ln, f"'{name}': with pdfLaTeX arXiv accepts only .pdf, .jpg and .png figures; it \"does not perform on the fly figure file conversion\". Convert the figure first.")
        for m in _INPUT.finditer(text):
            kind, name = m.group(1), m.group(2).strip()
            ln = _line(text, m.start())
            if _ABS.match(name):
                add("error", "absolute_path", path, ln, f"Absolute path '{name}'. arXiv: \"you must use relative filenames instead of absolute filenames\".")
            if kind == "include" and "/" in name:
                add("error", "include_subdir", path, ln, f"\\include{{{name}}}: arXiv: \"All files included via \\include instead of \\input must be in the top level directory.\" Use \\input or move the file.")
        for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]+)\}", text):
            pkgs = [x.strip() for x in m.group(1).split(",")]
            if "minted" in pkgs:
                add("error", "minted", path, _line(text, m.start()), "minted needs --shell-escape; arXiv: \"cannot process using the --shell-escape option\".")
        for m in re.finditer(r"\\pdfoutput\b", text):
            add("warning", "pdfoutput", path, _line(text, m.start()), "arXiv: do not use \\pdfoutput to change the output format; use the ifpdf package for conditional branching.")
        if _BIBLIO.search(text):
            uses_bibtex = True

    main_text = tex.get(main_file, "")
    uses_biblatex = bool(_BIBLATEX.search(main_text))
    bbl_name = _stem(main_file) + ".bbl"
    bbl_files = [p for p in files if p.endswith(".bbl")]
    if uses_bibtex or uses_biblatex:
        if bbl_name in files:
            m = re.search(rb"biblatex bbl format version (\d+\.\d+)", files[bbl_name])
            if m and m.group(1) != b"3.3":
                add("warning", "bbl_version", bbl_name, None,
                    f"This .bbl is biblatex format {m.group(1).decode()}; arXiv: \"For TeX Live 2025, we only support bbl format version 3.3\" (3.2 only on its TeX Live 2023 setting).")
        else:
            wrong = [p for p in bbl_files]
            if wrong:
                add("error", "bbl_name", wrong[0], None, f"arXiv: \"the name of the .bbl file must match the name of the main .tex file\" (expected {bbl_name}).")
            else:
                add("warning", "bbl_missing", main_file, None,
                    f"No {bbl_name}. arXiv can run BibTeX itself when all .bib files are included, but the safe way is to upload the .bbl "
                    "produced by your own compile: use Export > \"arXiv source (.zip)\", which adds it.")
    add("info", "tex_version", main_file, None,
        "arXiv compiles with its own TeX Live (documented: 2025 by default, 2023 as an option)"
        + (f"; this server compiles with: {tex_version}" if tex_version else "")
        + ". A paper that builds here can still fail there (package versions, missing files); view arXiv's generated PDF before submitting.")
    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["path"], i["line"] or 0))
    counts = {s: sum(1 for i in issues if i["severity"] == s) for s in order}
    return {"issues": issues, "counts": counts}


def source_zip(files: dict[str, bytes], main_file: str, bbl: bytes | None) -> bytes:
    """Project sources for upload: no hidden/aux/output files, plus the .bbl of the last compile named after the main file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in sorted(files.items()):
            if any(part.startswith(".") for part in rel.split("/")) or rel.lower().endswith(_AUX_EXT) or rel == _stem(main_file) + ".pdf":
                continue
            if rel == _stem(main_file) + ".bbl" and bbl is not None:
                continue  # replaced below
            zf.writestr(rel, data)
        if bbl is not None:
            zf.writestr(_stem(main_file) + ".bbl", bbl)
    return buf.getvalue()


def needs_bbl(files: dict[str, bytes], main_file: str) -> bool:
    tex = {p: _strip_comments(b.decode("utf-8", errors="replace")) for p, b in files.items() if p.endswith(".tex")}
    return any(_BIBLIO.search(t) for t in tex.values()) or bool(_BIBLATEX.search(tex.get(main_file, "")))
