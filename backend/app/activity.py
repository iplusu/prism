"""Pushes made through the API (not the browser): recorded so the project page can show that a push arrived and whether it worked.

A browser is recognised by its User-Agent (Mozilla/...); anything else (curl, python-requests, an agent) counts as an API client,
and a client can name itself with the X-Prism-Client header. Editor autosaves are never recorded. Several file writes by the same
client within a minute are one push (one row, a file count). A failed write is its own row with the reason."""

from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import ProjectActivity, User, now
from .projects import store

WINDOW = timedelta(seconds=60)
MAX_PATHS = 20


def client_of(request: Request) -> str | None:
    """Name of the API client, or None for a browser."""
    named = re.sub(r"[^\w .@:/+-]", "", request.headers.get("x-prism-client", ""))[:60].strip()
    if named:
        return named
    ua = request.headers.get("user-agent", "")
    if not ua or ua.startswith("Mozilla/"):
        return None
    return ua.split(" ")[0][:60] or "api client"


def record(db: Session, pid: str, user: User, client: str, ok: bool, path: str, error: str = "") -> None:
    t = now()
    if ok:
        row = db.execute(select(ProjectActivity).where(ProjectActivity.project_id == pid, ProjectActivity.client == client, ProjectActivity.ok.is_(True),
                                                      ProjectActivity.updated_at >= t - WINDOW).order_by(ProjectActivity.id.desc()).limit(1)).scalar_one_or_none()
        if row is not None:
            row.count += 1
            if path not in row.paths and len(row.paths) < MAX_PATHS:
                row.paths = [*row.paths, path]
            row.updated_at = t
            db.commit()
            return
    db.add(ProjectActivity(project_id=pid, user_id=user.id, client=client, ok=ok, count=1, paths=[path], error=error[:400], created_at=t, updated_at=t))
    db.commit()


@contextmanager
def track(db: Session, pid: str, user: User, request: Request, path: str):
    """Around one file write: record success or failure when an API client (not a browser) made it."""
    client = client_of(request)
    if client is None:
        yield
        return
    try:
        yield
    except HTTPException as e:
        db.rollback()
        record(db, pid, user, client, False, path, str(e.detail))
        raise
    record(db, pid, user, client, True, path)


_ZERO = "0" * 40
_PUSH_LINE = re.compile(r"^([0-9a-f]{40}) ([0-9a-f]{40}) (.*?) <[^>]*> (\d+) [+-]\d{4}$")
_import_lock = threading.Lock()


def import_git_pushes(db: Session, pid: str) -> None:
    """A `git push` straight into the project's repository (receive.denyCurrentBranch=updateInstead) does not go through the API.
    Git records every such update in the repo's HEAD reflog with the message "push", so those lines are read here and each push
    becomes one activity row (once: the commit it moved main to is the key). Only successful pushes leave a trace on this side;
    a push git refuses is seen by the pusher, not by Prism."""
    log = store.repo_dir(pid) / ".git" / "logs" / "HEAD"
    try:
        lines = log.read_text(errors="replace").splitlines()[-300:]
    except OSError:
        return
    found = []
    for ln in lines:
        head, _, msg = ln.partition("\t")
        m = _PUSH_LINE.match(head)
        if m and msg.strip() == "push":
            found.append(m.groups())
    if not found:
        return
    with _import_lock:
        known = set(db.execute(select(ProjectActivity.rev).where(ProjectActivity.project_id == pid, ProjectActivity.rev.is_not(None))).scalars())
        for old, new, who, ts in found:
            if new in known:
                continue
            files: list[str] = []
            if old != _ZERO:
                files = [x for x in store.git(pid, "diff", "--name-only", old, new, check=False).decode(errors="replace").splitlines() if x]
            when = datetime.fromtimestamp(int(ts), UTC)
            # the reflog names whoever runs this server's git, not the pusher: the author of the pushed commit is the useful name
            who = store.git(pid, "log", "-1", "--format=%an", new, check=False).decode(errors="replace").strip() or who
            db.add(ProjectActivity(project_id=pid, user_id=None, client=f"git push · {who.strip()[:60]}", ok=True, count=len(files),
                                   paths=files[:MAX_PATHS], rev=new, created_at=when, updated_at=when))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
            known.add(new)


def listing(db: Session, pid: str, limit: int = 30) -> list[dict]:
    import_git_pushes(db, pid)
    rows = db.execute(select(ProjectActivity).where(ProjectActivity.project_id == pid).order_by(ProjectActivity.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "client": r.client, "ok": r.ok, "count": r.count, "paths": r.paths or [], "error": r.error,
             "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()} for r in rows]
