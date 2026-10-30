from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import synctex
from ..builds import build_json, pdf_path, request_build
from ..db import get_db
from ..deps import current_user, get_project_for
from ..models import Build, User
from ..projects import store
from ..projects.documents import CollabUnavailable

router = APIRouter(prefix="/api", tags=["builds"])


@router.post("/projects/{pid}/builds")
def compile_project(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    pending = db.execute(select(Build).where(Build.project_id == pid, Build.kind == "main",
                                             Build.status.in_(("queued", "running")))).scalars().first()
    try:
        b = request_build(db, p, user.display_name or user.username, user.id)
    except CollabUnavailable as e:
        raise HTTPException(503, str(e)) from e
    out = build_json(b)
    out["queued_behind"] = pending.id if pending else None
    return out


@router.get("/projects/{pid}/builds")
def list_builds(pid: str, limit: int = 20, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    rows = db.execute(select(Build).where(Build.project_id == pid, Build.kind == "main")
                      .order_by(Build.created_at.desc()).limit(min(limit, 100))).scalars()
    return [build_json(b) for b in rows]


def _build_for(db: Session, bid: str, user: User) -> Build:
    b = db.get(Build, bid) if bid.isalnum() else None
    if b is None:
        raise HTTPException(404, "build not found")
    get_project_for(db, b.project_id, user)
    return b


@router.get("/builds/{bid}")
def get_build(bid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return build_json(_build_for(db, bid, user), include_log=True)


@router.get("/builds/{bid}/pdf")
def build_pdf(bid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = _build_for(db, bid, user)
    if not b.has_pdf or not pdf_path(b.id).exists():
        raise HTTPException(404, "this build has no PDF")
    return FileResponse(pdf_path(b.id), media_type="application/pdf",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})


def _sync(fn, *a):
    try:
        return fn(*a)
    except synctex.SyncTexError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/builds/{bid}/synctex/forward")
def synctex_forward(bid: str, file: str, line: int, col: int = 1, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Where a source line appears in this build's PDF (PDF points, origin top-left)."""
    b = _build_for(db, bid, user)
    return {"build_id": b.id, "boxes": _sync(synctex.forward, b.id, file, line, col)}


@router.get("/builds/{bid}/synctex/inverse")
def synctex_inverse(bid: str, page: int, x: float, y: float, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    """Source file and line at a PDF position of this build (null when it is not a project file)."""
    b = _build_for(db, bid, user)
    names = {n for n in store.git(b.project_id, "ls-tree", "-r", "-z", "--name-only", b.revision).decode().split("\0") if n}
    return {"build_id": b.id, "source": _sync(synctex.inverse, b.id, page, x, y, names)}
