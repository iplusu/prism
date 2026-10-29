"""Project-relative path validation.

All file paths that cross a trust boundary (API input, ZIP entries, agent
output, model-proposed scopes) go through `normalize_rel_path`.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path


class PathError(ValueError):
    pass


_BAD_CHARS = re.compile(r"[\x00-\x1f\\:*?\"<>|]")
MAX_PATH_LEN = 400
MAX_DEPTH = 16

# Files that configure or instruct tools/build systems. They may exist in a
# project (users import them), but they are never executed by the compiler
# (latexmk runs with -norc), never copied into an agent's work directory, and
# agents may never create or modify them.
PROTECTED_DIRS = {".git", ".claude", ".codex", ".gemini", ".agents", ".agent", ".antigravity", ".prism", ".vscode"}
PROTECTED_NAMES = {
    "CLAUDE.md", "CLAUDE.local.md", ".mcp.json", "AGENTS.md", "AGENTS.override.md",
    "GEMINI.md", "latexmkrc", ".latexmkrc", ".gitattributes", ".gitmodules",
}


def normalize_rel_path(p: str) -> str:
    if not isinstance(p, str) or not p.strip():
        raise PathError("empty path")
    if len(p) > MAX_PATH_LEN:
        raise PathError("path too long")
    if _BAD_CHARS.search(p):
        raise PathError("path contains forbidden characters")
    if p.startswith("/") or p.startswith("~"):
        raise PathError("absolute paths are not allowed")
    norm = posixpath.normpath(p)
    parts = norm.split("/")
    if norm in (".", "") or any(part in ("..", "") for part in parts):
        raise PathError("path escapes the project")
    if len(parts) > MAX_DEPTH:
        raise PathError("path too deep")
    if parts[0] == ".git":
        raise PathError(".git is reserved")
    return norm


def is_protected(rel: str) -> bool:
    parts = rel.split("/")
    return parts[-1] in PROTECTED_NAMES or any(part in PROTECTED_DIRS for part in parts)


def safe_join(root: Path, rel: str) -> Path:
    """Join and re-verify that the resolved path stays under root and does not
    traverse symlinks."""
    rel = normalize_rel_path(rel)
    root_r = root.resolve()
    target = root_r / rel
    # Reject any symlink component (existing ones), even if it points inside.
    cur = root_r
    for part in rel.split("/"):
        cur = cur / part
        if cur.is_symlink():
            raise PathError("symbolic links are not allowed")
    if not str(target.resolve()).startswith(str(root_r) + "/"):
        raise PathError("path escapes the project")
    return target


def glob_match(rel: str, patterns: list[str]) -> bool:
    """`**` matches across directories; plain `*` within one segment."""
    for pat in patterns:
        if pat in ("**", "**/*"):
            return True
        rx = _glob_to_regex(pat)
        if re.fullmatch(rx, rel):
            return True
    return False


def _glob_to_regex(pat: str) -> str:
    out = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pat.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def validate_scope_pattern(pat: str) -> str:
    """Scopes are project-relative globs. They must not escape the project."""
    if not isinstance(pat, str) or not pat.strip():
        raise PathError("empty scope pattern")
    pat = pat.strip()
    if pat in ("**", "**/*", "*"):
        return pat
    if pat.startswith("/") or ".." in pat.split("/") or "\\" in pat or "\x00" in pat:
        raise PathError(f"invalid scope pattern: {pat}")
    return pat
