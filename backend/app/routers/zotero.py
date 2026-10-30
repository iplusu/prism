from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import zotero
from ..agent.context import bib_entries
from ..db import get_db
from ..deps import current_user, get_project_for
from ..models import User, now
from ..projects import store
from ..projects.documents import Change
from .files import _apply, _live, _norm

router = APIRouter(tags=["zotero"])


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except zotero.ZoteroError as e:
        raise HTTPException(e.status, str(e)) from e


class KeyIn(BaseModel):
    api_key: str = Field(min_length=1, max_length=100)


class LibIn(BaseModel):
    api_key: str | None = Field(default=None, max_length=100)
    library: str = Field(max_length=30)


class ImportIn(LibIn):
    collection: str | None = Field(default=None, max_length=16)
    bib_file: str | None = Field(default=None, max_length=300)


@router.post("/api/zotero/libraries")
def libraries(body: KeyIn, user: User = Depends(current_user)):
    return _call(zotero.libraries, body.api_key)


@router.post("/api/zotero/collections")
def collections(body: LibIn, user: User = Depends(current_user)):
    return _call(zotero.collections, body.library, body.api_key)


@router.post("/api/projects/{pid}/zotero/import")
def import_from_zotero(pid: str, body: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    exported, total = _call(zotero.fetch_bibtex, body.library, body.collection, body.api_key)
    files = _live(pid)
    bibs = sorted(f for f in files if f.endswith(".bib"))
    target = _norm(body.bib_file) if body.bib_file else (bibs[0] if len(bibs) == 1 else "references.bib")
    if not target.endswith(".bib"):
        raise HTTPException(400, "target must be a .bib file")
    merged = zotero.merge({f: files[f] for f in bibs}, exported, {e["key"] for e in bib_entries(files)})
    summary = {"file": target, "total_in_zotero": total, "added": len(merged["entries"]),
               "skipped_existing": merged["skipped"], "renamed": merged["renamed"],
               "truncated": total > zotero.MAX_ENTRIES}
    if merged["entries"]:
        old = files.get(target)
        text = (old or b"").decode("utf-8", errors="replace")
        new = (text.rstrip() + "\n\n" if text.strip() else "") + "\n\n".join(merged["entries"]) + "\n"
        _apply(pid, [Change(path=target, content=new.encode(), expected_sha=store.sha256(old) if old is not None else None)])
        p.updated_at = now()
        db.commit()
    return summary
