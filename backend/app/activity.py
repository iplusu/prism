"""Pushes made through the API (not the browser): recorded so the project page can show that a push arrived and whether it worked.

A browser is recognised by its User-Agent (Mozilla/...); anything else (curl, python-requests, an agent) counts as an API client,
and a client can name itself with the X-Prism-Client header. Editor autosaves are never recorded. Several file writes by the same
client within a minute are one push (one row, a file count). A failed write is its own row with the reason."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import timedelta

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ProjectActivity, User, now

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


def listing(db: Session, pid: str, limit: int = 30) -> list[dict]:
    rows = db.execute(select(ProjectActivity).where(ProjectActivity.project_id == pid).order_by(ProjectActivity.id.desc()).limit(limit)).scalars().all()
    return [{"id": r.id, "client": r.client, "ok": r.ok, "count": r.count, "paths": r.paths or [], "error": r.error,
             "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()} for r in rows]
