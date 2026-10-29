from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..builds import pdf_path
from ..db import get_db
from ..deps import current_user, get_project_for
from ..models import Build, Proposal, User
from ..proposals import service

router = APIRouter(prefix="/api", tags=["proposals"])


class SelectIn(BaseModel):
    # path -> hunk ids. Files without hunks (binary, added, deleted) are taken
    # whole when their list is non-empty.
    selections: dict[str, list[int]]


class ResolveIn(BaseModel):
    resolutions: dict[str, str] = Field(default_factory=dict, description="path -> fully resolved file text")
    choices: dict[str, Literal["current", "proposal"]] = Field(
        default_factory=dict, description="path -> keep the current file or take the proposal's version "
                                          "(for binary, added or deleted files)")


@router.get("/projects/{pid}/proposals")
def list_proposals(pid: str, status: str = "open", limit: int = 200, user: User = Depends(current_user),
                   db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    q = select(Proposal).where(Proposal.project_id == pid).order_by(Proposal.created_at.desc()).limit(max(1, min(limit, 1000)))
    if status == "open":
        q = q.where(Proposal.status.in_(("verifying", "ready", "conflict", "applying")))
    return [service.proposal_json(db, p) for p in db.execute(q).scalars()]


def _prop(db: Session, prop_id: str, user: User, role: str = "viewer") -> Proposal:
    p = db.get(Proposal, prop_id) if prop_id.isalnum() else None
    if p is None:
        raise HTTPException(404, "proposal not found")
    get_project_for(db, p.project_id, user, role)
    return p


@router.get("/proposals/{prop_id}")
def get_proposal(prop_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return service.proposal_json(db, _prop(db, prop_id, user), detail=True)


@router.get("/proposals/{prop_id}/pdf")
def proposal_pdf(prop_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _prop(db, prop_id, user)
    b = db.get(Build, p.candidate_build_id) if p.candidate_build_id else None
    if b is None or not b.has_pdf or not pdf_path(b.id).exists():
        raise HTTPException(404, "no candidate PDF")
    return FileResponse(pdf_path(b.id), media_type="application/pdf")


@router.post("/proposals/{prop_id}/apply")
def apply(prop_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _prop(db, prop_id, user, "editor")
    try:
        service.request_apply(db, p, user.id, user.display_name or user.username)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return service.proposal_json(db, p)


@router.post("/proposals/{prop_id}/reject")
def reject(prop_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _prop(db, prop_id, user, "editor")
    try:
        service.reject(db, p, user.id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return service.proposal_json(db, p)


@router.post("/proposals/{prop_id}/select")
def select_hunks(prop_id: str, body: SelectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _prop(db, prop_id, user, "editor")
    try:
        child = service.select_hunks(db, p, body.selections, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return service.proposal_json(db, child)


@router.post("/proposals/{prop_id}/resolve")
def resolve(prop_id: str, body: ResolveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _prop(db, prop_id, user, "editor")
    try:
        child = service.resolve(db, p, body.resolutions, {"id": user.id, "name": user.display_name or user.username},
                                choices=body.choices)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return service.proposal_json(db, child)
