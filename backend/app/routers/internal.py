"""Service-to-service API used by the collaboration server. Authenticated by
a shared secret; never exposed through the public reverse proxy (see
docs/deployment.md)."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..projects import documents, store
from ..security.paths import PathError

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def _auth(secret: str | None) -> None:
    s = get_settings()
    if not s.collab_shared_secret or not secret or not hmac.compare_digest(secret, s.collab_shared_secret):
        raise HTTPException(403, "forbidden")


def _pid(pid: str) -> str:
    if not pid.isalnum() or not store.repo_dir(pid).is_dir():
        raise HTTPException(404, "project not found")
    return pid


class TextsIn(BaseModel):
    files: dict[str, str | None]


@router.get("/projects/{pid}/texts")
def texts(pid: str, x_prism_internal: str | None = Header(default=None)):
    _auth(x_prism_internal)
    _pid(pid)
    out = {}
    for rel, data in store.read_all(pid).items():
        if store.is_text(rel, data):
            out[rel] = data.decode("utf-8")
    return {"files": out}


@router.put("/projects/{pid}/texts")
def write_through(pid: str, body: TextsIn, x_prism_internal: str | None = Header(default=None)):
    _auth(x_prism_internal)
    _pid(pid)
    try:
        for rel, text in body.files.items():
            if text is not None and not store.is_text(rel, text.encode("utf-8")):
                raise HTTPException(400, f"{rel} is not a text file type")
        documents.write_through(pid, body.files)
    except PathError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
