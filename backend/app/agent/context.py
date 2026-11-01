"""Assemble project context for the orchestrator without a vector database:
file tree, section outline, citation keys, labels, the requested scope
(selection / current file / project), keyword-ranked sections, build log."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..projects.store import is_text

_SECTION = re.compile(r"\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\s*(?:\[[^\]]*\])?\{([^}]*)\}")
_LABEL = re.compile(r"\\label\{([^}]+)\}")
_CITE = re.compile(r"\\(?:cite|citep|citet|citealp|citeauthor|citeyear|parencite|textcite|autocite|footcite)\*?(?:\[[^\]]*\]){0,2}\{([^}]+)\}")
_BIBENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.I)
_BIBTITLE = re.compile(r"title\s*=\s*[{\"](.+?)[}\"]\s*,?\s*$", re.I | re.M)
_WORD = re.compile(r"[A-Za-z\uac00-\ud7a3][A-Za-z0-9\uac00-\ud7a3\-]{2,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "please", "into", "about",
         "make", "section", "paper", "change", "add", "can", "you", "그리고", "해줘", "주세요"}


def outline(files: dict[str, bytes]) -> list[dict]:
    out = []
    for path in sorted(files):
        if not path.endswith(".tex"):
            continue
        text = files[path].decode("utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.split("%", 1)[0] if not line.lstrip().startswith("\\%") else line
            for m in _SECTION.finditer(stripped):
                out.append({"file": path, "line": i, "level": m.group(1), "title": m.group(2).strip()})
    return out


def labels(files: dict[str, bytes]) -> list[dict]:
    out = []
    for path in sorted(files):
        if not path.endswith(".tex"):
            continue
        for i, line in enumerate(files[path].decode("utf-8", errors="replace").splitlines(), 1):
            for m in _LABEL.finditer(line):
                out.append({"label": m.group(1), "file": path, "line": i})
    return out


def bib_entries(files: dict[str, bytes]) -> list[dict]:
    out = []
    for path in sorted(files):
        if not path.endswith(".bib"):
            continue
        text = files[path].decode("utf-8", errors="replace")
        starts = list(_BIBENTRY.finditer(text))
        for idx, m in enumerate(starts):
            if m.group(1).lower() in ("comment", "string", "preamble"):
                continue
            body = text[m.end(): starts[idx + 1].start() if idx + 1 < len(starts) else len(text)]
            t = _BIBTITLE.search(body)
            out.append({"key": m.group(2), "type": m.group(1).lower(), "file": path,
                        "title": re.sub(r"[{}]", "", t.group(1))[:160] if t else ""})
    return out


def citations_used(files: dict[str, bytes]) -> set[str]:
    keys = set()
    for path, data in files.items():
        if path.endswith(".tex"):
            for m in _CITE.finditer(data.decode("utf-8", errors="replace")):
                keys.update(k.strip() for k in m.group(1).split(",") if k.strip())
    return keys


def _chunks(files: dict[str, bytes]) -> list[dict]:
    """Split .tex files at sectioning commands."""
    chunks = []
    for path in sorted(files):
        if not path.endswith(".tex"):
            continue
        lines = files[path].decode("utf-8", errors="replace").splitlines()
        start, title = 0, path
        for i, line in enumerate(lines):
            m = _SECTION.search(line)
            if m and i > start:
                chunks.append({"file": path, "start": start + 1, "end": i, "title": title, "text": "\n".join(lines[start:i])})
                start, title = i, m.group(2)
            elif m:
                title = m.group(2)
        if start < len(lines):
            chunks.append({"file": path, "start": start + 1, "end": len(lines), "title": title, "text": "\n".join(lines[start:])})
    return chunks


MAX_CHUNK_CHARS = 5000  # a section longer than this is cut into pieces, so one huge section cannot crowd out (or be dropped whole from) the context


def _pieces(chunk: dict) -> list[dict]:
    """Cut an over-long chunk into consecutive pieces of at most MAX_CHUNK_CHARS, preferably at blank lines.
    Line numbers stay those of the file."""
    if len(chunk["text"]) <= MAX_CHUNK_CHARS:
        return [chunk]
    lines = chunk["text"].split("\n")
    out, cur, size, first = [], [], 0, chunk["start"]
    for i, ln in enumerate(lines):
        cur.append(ln)
        size += len(ln) + 1
        at_break = not ln.strip()
        if size >= MAX_CHUNK_CHARS or (size >= MAX_CHUNK_CHARS * 0.6 and at_break):
            out.append({**chunk, "start": first, "end": chunk["start"] + i, "text": "\n".join(cur)})
            cur, size, first = [], 0, chunk["start"] + i + 1
    if cur:
        out.append({**chunk, "start": first, "end": chunk["end"], "text": "\n".join(cur)})
    return out


def _keywords(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)} - _STOP


def _numbered(text: str, first_line: int = 1) -> str:
    return "\n".join(f"{first_line + i:5d}| {ln}" for i, ln in enumerate(text.splitlines()))


@dataclass
class BuiltContext:
    text: str
    included: list[str] = field(default_factory=list)
    truncated: bool = False


def build_context(files: dict[str, bytes], *, request: str, spec: dict, main_file: str, budget: int,
                  diagnostics: list[dict] | None = None, pages: str = "") -> BuiltContext:
    parts: list[str] = []
    included: list[str] = []
    used = 0
    truncated = False

    def add(block: str, tag: str | None = None, required: bool = False) -> bool:
        nonlocal used, truncated
        if used + len(block) > budget and not required:
            truncated = True
            return False
        if used + len(block) > budget:
            block = block[: max(0, budget - used)] + "\n[... truncated ...]"
            truncated = True
        parts.append(block)
        used += len(block)
        if tag:
            included.append(tag)
        return True

    tree = "\n".join(f"- {p}{'' if is_text(p, d) else '  (binary)'}" for p, d in sorted(files.items()))
    add(f"## Project files (main file: {main_file})\n{tree}\n", required=True)
    if pages:
        add(f"## PDF page count\n{pages}\n", required=True)
    ol = outline(files)
    if ol:
        add("## Outline\n" + "\n".join(f"- [{o['level']}] {o['title']}  ({o['file']}:{o['line']})" for o in ol) + "\n")
    bib = bib_entries(files)
    if bib:
        add("## Bibliography keys\n" + "\n".join(f"- {b['key']}: {b['title']}" for b in bib[:300]) + "\n")
    lb = labels(files)
    if lb:
        add("## Labels\n" + ", ".join(f"{x['label']} ({x['file']}:{x['line']})" for x in lb[:300]) + "\n")
    if diagnostics:
        errs = [d for d in diagnostics if d.get("severity") in ("error", "warning")][:30]
        if errs:
            add("## Latest build diagnostics\n" + "\n".join(
                f"- {d['severity']}: {d.get('file') or '?'}:{d.get('line') or '?'} {d['message'][:200]}" for d in errs) + "\n")

    scope = spec.get("scope", "project")
    path = spec.get("path")
    if scope == "selection" and spec.get("selection"):
        sel = spec["selection"]
        add(f"## Selected text ({path} lines {sel.get('from_line')}-{sel.get('to_line')})\n```latex\n{sel.get('text', '')[:budget // 2]}\n```\n",
            tag=f"selection:{path}", required=True)
    if scope in ("selection", "file") and path and path in files and is_text(path, files[path]):
        add(f"## Current file: {path} (line-numbered)\n```\n{_numbered(files[path].decode('utf-8'))}\n```\n", tag=path,
            required=scope == "file")

    if scope == "project":
        kw = _keywords(request)
        pieces = [q for c in _chunks(files) for q in _pieces(c)]
        # best keyword match first; ties keep document order (sorted() is stable)
        ranked = sorted(pieces, key=lambda c: -len(kw & _keywords(c["text"] + " " + c["title"])))
        for c in ranked:
            if c["file"] in included:
                continue
            block = f"## {c['file']} lines {c['start']}-{c['end']} ({c['title']})\n```\n{_numbered(c['text'], c['start'])}\n```\n"
            if add(block, tag=f"{c['file']}:{c['start']}-{c['end']}") is False and budget - used < 300:
                break  # a piece that does not fit is skipped; smaller ones after it may still fit
    if truncated:
        parts.append("## NOTE\nThe context budget was used up: some parts of the project text are NOT shown above. "
                     "Do not assume the shown text is the whole document; say what you could not see instead of guessing.\n")
    return BuiltContext(text="\n".join(parts), included=included, truncated=truncated)
