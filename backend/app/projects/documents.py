"""Live manuscript state.

* Collaboration disabled: the project working tree on disk is the live state;
  writes are serialized with an inter-process file lock and are conditional on
  the caller's expected content hash.
* Collaboration enabled: the collab service (Yjs) is authoritative for text
  files. Snapshots are read from it and conditional changes are sent to it, so
  connected editors receive them as ordinary document updates. Binary files
  (figures, PDFs) live on disk only.

Nothing here ever runs `git checkout` over the live state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from . import store

ANY = "*"  # expected_sha wildcard: no precondition


class ConflictError(RuntimeError):
    def __init__(self, message: str, details: list[dict] | None = None):
        super().__init__(message)
        self.details = details or []


class CollabUnavailable(RuntimeError):
    pass


@dataclass
class Change:
    path: str
    content: bytes | None  # None = delete
    expected_sha: str | None = ANY  # None = must not exist; ANY = unconditional


@dataclass
class Snapshot:
    revision: str
    files: dict[str, bytes] = field(default_factory=dict)
    tree_hash: str = ""


# ------------------------------------------------------------------ collab ---

def _collab_headers() -> dict:
    return {"X-Prism-Internal": get_settings().collab_shared_secret}


def _collab_get_texts(pid: str) -> dict[str, str]:
    s = get_settings()
    try:
        r = httpx.get(f"{s.collab_internal_url}/internal/projects/{pid}/texts", headers=_collab_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise CollabUnavailable(f"collaboration service unreachable: {e}") from e
    if r.status_code != 200:
        raise CollabUnavailable(f"collaboration service error {r.status_code}: {r.text[:200]}")
    return r.json()["files"]


def _collab_apply(pid: str, changes: list[dict]) -> None:
    s = get_settings()
    try:
        r = httpx.post(f"{s.collab_internal_url}/internal/projects/{pid}/apply", headers=_collab_headers(),
                       json={"changes": changes}, timeout=30)
    except httpx.HTTPError as e:
        raise CollabUnavailable(f"collaboration service unreachable: {e}") from e
    if r.status_code == 409:
        body = r.json()
        raise ConflictError("document changed concurrently", body.get("conflicts", []))
    if r.status_code != 200:
        raise CollabUnavailable(f"collaboration service error {r.status_code}: {r.text[:200]}")


def collab_reset(pid: str) -> None:
    """Drop the collab copy so it reloads from disk (used after import)."""
    s = get_settings()
    if not s.collab_enabled:
        return
    try:
        httpx.post(f"{s.collab_internal_url}/internal/projects/{pid}/reload", headers=_collab_headers(), timeout=30)
    except httpx.HTTPError as e:
        raise CollabUnavailable(f"collaboration service unreachable: {e}") from e


# ------------------------------------------------------------------- public ---

def live_files(pid: str) -> dict[str, bytes]:
    s = get_settings()
    disk = store.read_all(pid)
    if not s.collab_enabled:
        return disk
    texts = _collab_get_texts(pid)
    out = {p: d for p, d in disk.items() if not store.is_text(p, d)}
    for p, t in texts.items():
        out[p] = t.encode("utf-8")
    return out


def snapshot(pid: str, author: str = "Prism", message: str = "Snapshot") -> Snapshot:
    """Capture a consistent copy of the live state and record it in history."""
    files = live_files(pid)
    with store.project_lock(pid):
        store.sync_tree(pid, files)
        rev = store.commit(pid, message, author)
    return Snapshot(revision=rev, files=files, tree_hash=store.tree_hash(files))


def apply_changes(pid: str, changes: list[Change]) -> None:
    """Apply all changes or none. Preconditions are checked against the latest
    live state; a mismatch raises ConflictError and nothing is written."""
    s = get_settings()
    for c in changes:
        c.path = store.normalize(c.path)
    if s.collab_enabled:
        text_changes = [c for c in changes if c.content is None or store.is_text(c.path, c.content)]
        bin_changes = [c for c in changes if c not in text_changes]
        with store.project_lock(pid):
            _check_disk(pid, bin_changes)
            if text_changes:
                _collab_apply(pid, [
                    {"path": c.path, "expected_sha": c.expected_sha,
                     "text": None if c.content is None else c.content.decode("utf-8")}
                    for c in text_changes
                ])
            for c in changes:  # mirror to disk immediately
                if c.content is None:
                    store.delete_file(pid, c.path)
                else:
                    store.write_file(pid, c.path, c.content)
        return
    with store.project_lock(pid):
        _check_disk(pid, changes)
        for c in changes:
            if c.content is None:
                store.delete_file(pid, c.path)
            else:
                store.write_file(pid, c.path, c.content)


def _check_disk(pid: str, changes: list[Change]) -> None:
    conflicts = []
    for c in changes:
        if c.expected_sha == ANY:
            continue
        try:
            cur = store.sha256(store.read_file(pid, c.path))
        except FileNotFoundError:
            cur = None
        if cur != c.expected_sha:
            conflicts.append({"path": c.path, "expected": c.expected_sha, "actual": cur})
    if conflicts:
        raise ConflictError("document changed concurrently", conflicts)


def write_through(pid: str, files: dict[str, str | None]) -> None:
    """Called by the collab service to mirror text state onto disk."""
    with store.project_lock(pid):
        for rel, text in files.items():
            rel = store.normalize(rel)
            if text is None:
                store.delete_file(pid, rel)
            else:
                store.write_file(pid, rel, text.encode("utf-8"))
