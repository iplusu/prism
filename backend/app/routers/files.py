from __future__ import annotations

import mimetypes
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import arxiv, review
from ..agent.context import bib_entries, labels, outline
from ..compile.latex import tex_status
from ..config import get_settings
from ..db import get_db
from ..deps import current_user, get_project_for
from ..models import Project, User, now
from ..projects import documents, store
from ..projects.documents import ANY, Change, ConflictError, CollabUnavailable
from ..security.paths import PathError

router = APIRouter(prefix="/api/projects/{pid}", tags=["files"])


class SaveIn(BaseModel):
    path: str
    text: str
    base_sha: str | None = Field(default=None, description="sha256 of the content the edit was based on")


class CreateIn(BaseModel):
    path: str
    text: str = ""


class RenameIn(BaseModel):
    src: str
    dst: str


def _norm(path: str) -> str:
    try:
        return store.normalize(path)
    except PathError as e:
        raise HTTPException(400, str(e)) from e


def _live(pid: str) -> dict[str, bytes]:
    try:
        return documents.live_files(pid)
    except CollabUnavailable as e:
        raise HTTPException(503, str(e)) from e


def _apply(pid: str, changes: list[Change]) -> None:
    try:
        documents.apply_changes(pid, changes)
    except ConflictError as e:
        raise HTTPException(409, {"message": "the file changed since you loaded it", "conflicts": e.details}) from e
    except CollabUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except PathError as e:
        raise HTTPException(400, str(e)) from e


def _touch(db: Session, p: Project) -> None:
    p.updated_at = now()
    db.commit()


@router.get("/files")
def list_files(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    files = _live(pid)
    return [{"path": rel, "size": len(data), "is_text": store.is_text(rel, data)} for rel, data in sorted(files.items())]


@router.get("/files/content")
def read_file(pid: str, path: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    rel = _norm(path)
    data = _live(pid).get(rel)
    if data is None:
        raise HTTPException(404, "file not found")
    if not store.is_text(rel, data):
        raise HTTPException(415, "binary file; use /files/raw")
    return {"path": rel, "text": data.decode("utf-8"), "sha": store.sha256(data)}


@router.get("/files/raw")
def raw_file(pid: str, path: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    rel = _norm(path)
    data = _live(pid).get(rel)
    if data is None:
        raise HTTPException(404, "file not found")
    mt = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if mt in ("text/html", "image/svg+xml", "application/xhtml+xml", "application/javascript"):
        mt = "text/plain"  # never render project content as active content
    return Response(data, media_type=mt, headers={"X-Content-Type-Options": "nosniff",
                                                  "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.put("/files/content")
def save_file(pid: str, body: SaveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    rel = _norm(body.path)
    data = body.text.encode("utf-8")
    if len(data) > get_settings().max_text_file_mb * 1024 * 1024:
        raise HTTPException(413, "file too large")
    if not store.is_text(rel, data):
        raise HTTPException(400, "not an editable text file type")
    _apply(pid, [Change(path=rel, content=data, expected_sha=body.base_sha if body.base_sha is not None else ANY)])
    _touch(db, p)
    return {"path": rel, "sha": store.sha256(data)}


@router.post("/files")
def create_file(pid: str, body: CreateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    rel = _norm(body.path)
    data = body.text.encode("utf-8")
    if not store.is_text(rel, data):
        raise HTTPException(400, "only text files can be created here; use upload for other files")
    _apply(pid, [Change(path=rel, content=data, expected_sha=None)])
    _touch(db, p)
    return {"path": rel, "sha": store.sha256(data)}


@router.post("/files/upload")
def upload(pid: str, file: UploadFile = File(...), directory: str = Form(""), user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    name = (file.filename or "upload").rsplit("/", 1)[-1]
    rel = _norm(f"{directory.strip('/')}/{name}" if directory.strip("/") else name)
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "file too large")
    _apply(pid, [Change(path=rel, content=data, expected_sha=ANY)])
    _touch(db, p)
    return {"path": rel, "size": len(data), "is_text": store.is_text(rel, data)}


@router.post("/files/rename")
def rename(pid: str, body: RenameIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    src, dst = _norm(body.src), _norm(body.dst)
    files = _live(pid)
    if src not in files:
        raise HTTPException(404, "file not found")
    if dst in files:
        raise HTTPException(409, "target exists")
    data = files[src]
    _apply(pid, [Change(path=dst, content=data, expected_sha=None),
                 Change(path=src, content=None, expected_sha=store.sha256(data))])
    if p.main_file == src:
        p.main_file = dst
    _touch(db, p)
    return {"path": dst}


@router.delete("/files")
def delete(pid: str, path: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    rel = _norm(path)
    files = _live(pid)
    if rel not in files:
        raise HTTPException(404, "file not found")
    _apply(pid, [Change(path=rel, content=None, expected_sha=store.sha256(files[rel]))])
    _touch(db, p)
    return {"ok": True}


@router.get("/outline")
def project_outline(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    files = _live(pid)
    return {"sections": outline(files), "labels": labels(files), "bib": bib_entries(files)}


@router.get("/search")
def search(pid: str, q: str, regex: bool = False, case: bool = False, user: User = Depends(current_user),
           db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    if not q or len(q) > 500:
        raise HTTPException(400, "query must be 1-500 characters")
    flags = 0 if case else re.I
    try:
        rx = re.compile(q if regex else re.escape(q), flags)
    except re.error as e:
        raise HTTPException(400, f"invalid regular expression: {e}") from e
    results = []
    for rel, data in sorted(_live(pid).items()):
        if not store.is_text(rel, data):
            continue
        for i, line in enumerate(data.decode("utf-8").splitlines(), 1):
            m = rx.search(line[:5000])
            if m:
                results.append({"path": rel, "line": i, "col": m.start() + 1, "text": line[:300]})
                if len(results) >= 500:
                    return {"results": results, "truncated": True}
    return {"results": results, "truncated": False}


@router.get("/review")
def review_manuscript(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "viewer")
    return review.check(_live(pid), p.main_file)


@router.get("/arxiv-check")
def arxiv_check(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Documented arXiv submission pitfalls in the live text (see app/arxiv.py for the sources)."""
    p, _ = get_project_for(db, pid, user, "viewer")
    return arxiv.check(_live(pid), p.main_file, p.engine, tex_status().get("detail", ""))
