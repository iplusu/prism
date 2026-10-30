from __future__ import annotations

import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import bibverify
from .. import references as refs
from ..agent.context import bib_entries
from ..db import get_db
from ..deps import current_user, get_project_for
from ..models import User, now
from ..projects import store
from ..projects.documents import Change
from .files import _apply, _live, _norm

router = APIRouter(tags=["references"])


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except refs.ReferenceError as e:
        raise HTTPException(e.status, str(e)) from e


@router.get("/api/references/search")
def search(q: str, source: Literal["arxiv"] = "arxiv", limit: int = 10, user: User = Depends(current_user)):
    return _call(refs.search_arxiv, q, limit)


class ImportIn(BaseModel):
    source: Literal["arxiv", "doi"]
    id: str = Field(min_length=3, max_length=300)
    bib_file: str | None = None


def _entry_blocks(text: str) -> list[tuple[str, str]]:
    starts = list(re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text))
    return [(m.group(2), text[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(text)])
            for i, m in enumerate(starts)]


@router.post("/api/projects/{pid}/references")
def import_reference(pid: str, body: ImportIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    files = _live(pid)
    bibs = sorted(f for f in files if f.endswith(".bib"))
    target = _norm(body.bib_file) if body.bib_file else (bibs[0] if len(bibs) == 1 else "references.bib")
    if not target.endswith(".bib"):
        raise HTTPException(400, "target must be a .bib file")
    for f in bibs:  # already imported? reuse the existing key instead of adding a duplicate
        for key, block in _entry_blocks(files[f].decode("utf-8", errors="replace")):
            if refs.identifies(block, body.source, body.id):
                return {"key": key, "file": f, "added": False}
    bib = _call(refs.fetch_bibtex, body.source, body.id)
    bib, key = _call(refs.with_unique_key, bib, {e["key"] for e in bib_entries(files)})
    old = files.get(target)
    text = (old or b"").decode("utf-8", errors="replace")
    new = (text.rstrip() + "\n\n" if text.strip() else "") + bib + "\n"
    # conditional on the content we read: a concurrent edit makes this a 409, never a silent overwrite
    _apply(pid, [Change(path=target, content=new.encode(), expected_sha=store.sha256(old) if old is not None else None)])
    p.updated_at = now()
    db.commit()
    return {"key": key, "file": target, "added": True, "entry": bib}


@router.post("/api/projects/{pid}/references/verify")
def verify_references(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Look every bibliography entry up in public databases (DOI, arXiv, Crossref/OpenAlex by title)."""
    get_project_for(db, pid, user, "editor")
    entries = bibverify.parse_entries(_live(pid))
    return bibverify.verify(entries)
