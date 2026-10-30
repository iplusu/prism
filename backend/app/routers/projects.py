from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import arxiv
from ..agent.policy import REAL_EXECUTORS, effective_policy
from ..builds import build_json, pdf_path
from ..config import REPO_ROOT, get_settings
from ..db import get_db
from ..deps import current_user, get_project_for, project_role, user_json
from ..models import Build, Membership, Project, ProjectVersion, Proposal, Task, User, now
from ..projects import documents, store
from ..projects import templates as kits
from ..proposals import diff as pdiff
from ..proposals import service as proposals
from ..security.passwords import sign
from ..security.zipimport import ZipImportError, extract_project_zip

router = APIRouter(prefix="/api/projects", tags=["projects"])

BLANK_MAIN = r"""\documentclass[11pt]{article}
\usepackage[utf8]{inputenc}
\usepackage{amsmath}
\usepackage{hyperref}

\title{Untitled}
\author{}
\date{}

\begin{document}
\maketitle

\section{Introduction}

\end{document}
"""


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    template: str = "blank"  # blank | sample | an id from projects/templates.py


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    main_file: str | None = None
    engine: str | None = None


class PolicyIn(BaseModel):
    allow_external: bool = True
    allowed_executors: list[str] = Field(default_factory=lambda: list(REAL_EXECUTORS))
    default_executor: str = "auto"
    preference_order: list[str] = Field(default_factory=list)
    routing: dict[str, list[str]] = Field(default_factory=dict)
    run_timeout_seconds: int = Field(default=600, ge=30)
    task_timeout_seconds: int = Field(default=1500, ge=60)
    max_attempts: int = Field(default=2, ge=1, le=5)


class MemberIn(BaseModel):
    username: str
    role: str = "editor"


class VersionIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)


def _guess_main(files: list[str], read) -> str:
    tex = [f for f in files if f.endswith(".tex")]
    for pref in ("main.tex", "paper.tex", "manuscript.tex", "thesis.tex"):
        if pref in tex:
            return pref
    for f in sorted(tex, key=lambda x: (x.count("/"), x)):
        try:
            if re.search(rb"^\s*\\documentclass", read(f), re.M):
                return f
        except Exception:
            continue
    return tex[0] if tex else "main.tex"


def project_json(db: Session, p: Project, role: str | None, detail: bool = False) -> dict:
    last = db.get(Build, p.last_build_id) if p.last_build_id else None
    ok = db.get(Build, p.last_success_build_id) if p.last_success_build_id else None
    active = db.execute(select(func.count()).select_from(Task).where(
        Task.project_id == p.id, Task.status.in_(("queued", "planning", "running", "verifying")))).scalar()
    review = db.execute(select(func.count()).select_from(Proposal).where(
        Proposal.project_id == p.id, Proposal.status.in_(("ready", "conflict", "verifying", "applying")))).scalar()
    d = {
        "id": p.id, "name": p.name, "description": p.description, "main_file": p.main_file, "engine": p.engine,
        "role": role, "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat(),
        "last_build": build_json(last), "last_success_build": build_json(ok),
        "active_tasks": active, "open_proposals": review,
    }
    if detail:
        d["policy"] = effective_policy(p)
        d["members"] = [
            {"user": user_json(u), "role": m.role}
            for m, u in db.execute(select(Membership, User).join(User, User.id == Membership.user_id)
                                   .where(Membership.project_id == p.id).order_by(User.username))
        ]
    return d


def _touch(db: Session, p: Project) -> None:
    p.updated_at = now()
    db.commit()


@router.get("")
def list_projects(q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.is_admin:
        rows = db.execute(select(Project).order_by(Project.updated_at.desc())).scalars().all()
    else:
        rows = db.execute(select(Project).join(Membership, Membership.project_id == Project.id)
                          .where(Membership.user_id == user.id).order_by(Project.updated_at.desc())).scalars().all()
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r.name.lower() or ql in (r.description or "").lower()]
    return [project_json(db, p, project_role(db, p.id, user)) for p in rows]


def _new_project(db: Session, user: User, name: str) -> Project:
    p = Project(name=name.strip(), created_by=user.id, agent_policy={})
    db.add(p)
    db.flush()
    db.add(Membership(project_id=p.id, user_id=user.id, role="owner"))
    return p


@router.get("/templates")
def list_templates(user: User = Depends(current_user)):
    return kits.catalogue()


def _from_kit(db: Session, user: User, name: str, template_id: str) -> Project:
    s = get_settings()
    try:
        kit = kits.get(template_id)
    except kits.TemplateError as e:
        raise HTTPException(400, str(e)) from e
    with tempfile.TemporaryDirectory(prefix="prism-kit-") as td:
        zp, dest = Path(td) / "kit.zip", Path(td) / "x"
        try:
            kits.download(kit, zp)
            res = extract_project_zip(zp, dest, max_total_bytes=s.max_unzipped_mb * 1024 * 1024,
                                      max_entries=s.max_zip_entries)
            kits.prepare(kit, dest, res.files)
        except (kits.TemplateError, ZipImportError) as e:
            raise HTTPException(502, str(e)) from e
        p = _new_project(db, user, name)
        p.main_file = kit.main_file
        p.description = f"From the {kit.label} template ({kit.source})"
        store.init_project(p.id, source_dir=dest, author=user.display_name or user.username)
    return p


@router.post("")
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.template not in ("blank", "sample"):
        p = _from_kit(db, user, body.name, body.template)
        db.commit()
        return project_json(db, p, "owner", detail=True)
    p = _new_project(db, user, body.name)
    if body.template == "sample":
        src = REPO_ROOT / "sample" / "paper"
        if not src.is_dir():
            raise HTTPException(500, "sample project missing")
        store.init_project(p.id, source_dir=src, author=user.display_name or user.username)
    else:
        store.init_project(p.id, files={"main.tex": BLANK_MAIN.encode(), "references.bib": b""},
                           author=user.display_name or user.username)
    db.commit()
    return project_json(db, p, "owner", detail=True)


@router.post("/import")
def import_zip(file: UploadFile = File(...), name: str = Form(""), user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    s = get_settings()
    with tempfile.TemporaryDirectory(prefix="prism-import-") as td:
        zp = Path(td) / "upload.zip"
        size = 0
        with open(zp, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > s.max_upload_mb * 1024 * 1024:
                    raise HTTPException(413, f"upload exceeds {s.max_upload_mb} MB")
                out.write(chunk)
        dest = Path(td) / "x"
        try:
            res = extract_project_zip(zp, dest, max_total_bytes=s.max_unzipped_mb * 1024 * 1024,
                                      max_entries=s.max_zip_entries)
        except ZipImportError as e:
            raise HTTPException(400, str(e)) from e
        pname = name.strip() or Path(file.filename or "Imported project").stem
        p = _new_project(db, user, pname[:200])
        p.main_file = _guess_main(res.files, lambda f: (dest / f).read_bytes())
        store.init_project(p.id, source_dir=dest, author=user.display_name or user.username)
        db.commit()
    out = project_json(db, p, "owner", detail=True)
    out["import"] = {"files": len(res.files), "skipped": res.skipped, "stripped_prefix": res.stripped_prefix}
    return out


@router.get("/{pid}")
def get_project(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user)
    return project_json(db, p, role, detail=True)


@router.patch("/{pid}")
def patch_project(pid: str, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user, "editor")
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description[:2000]
    if body.main_file is not None:
        rel = store.normalize(body.main_file)
        if not rel.endswith(".tex"):
            raise HTTPException(400, "main file must be a .tex file")
        p.main_file = rel
    if body.engine is not None:
        if body.engine not in ("pdflatex", "xelatex", "lualatex"):
            raise HTTPException(400, "engine must be pdflatex, xelatex or lualatex")
        p.engine = body.engine
    _touch(db, p)
    return project_json(db, p, role, detail=True)


@router.put("/{pid}/policy")
def put_policy(pid: str, body: PolicyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user, "owner")
    s = get_settings()
    bad = [e for e in body.allowed_executors + body.preference_order if e not in REAL_EXECUTORS]
    if bad:
        raise HTTPException(400, f"unknown executors: {bad}")
    if body.default_executor not in ("auto", "llm", *REAL_EXECUTORS):
        raise HTTPException(400, "invalid default executor")
    pol = body.model_dump()
    # Server-wide ceilings always apply.
    pol["run_timeout_seconds"] = min(pol["run_timeout_seconds"], s.agent_run_timeout_seconds)
    pol["task_timeout_seconds"] = min(pol["task_timeout_seconds"], s.agent_task_timeout_seconds)
    pol["max_attempts"] = min(pol["max_attempts"], s.agent_max_attempts)
    p.agent_policy = pol
    _touch(db, p)
    return project_json(db, p, role, detail=True)


@router.delete("/{pid}")
def delete_project(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "owner")
    db.delete(p)
    db.commit()
    store.delete_project(pid)
    return {"ok": True}


# ------------------------------------------------------------- members ---

@router.post("/{pid}/members")
def add_member(pid: str, body: MemberIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user, "owner")
    if body.role not in ("owner", "editor", "viewer"):
        raise HTTPException(400, "role must be owner, editor or viewer")
    u = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if u is None:
        raise HTTPException(404, "no such user")
    m = db.execute(select(Membership).where(Membership.project_id == pid, Membership.user_id == u.id)).scalar_one_or_none()
    if m:
        m.role = body.role
    else:
        db.add(Membership(project_id=pid, user_id=u.id, role=body.role))
    db.commit()
    return project_json(db, p, role, detail=True)


@router.delete("/{pid}/members/{uid}")
def remove_member(pid: str, uid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user, "owner")
    m = db.execute(select(Membership).where(Membership.project_id == pid, Membership.user_id == uid)).scalar_one_or_none()
    if m is None:
        raise HTTPException(404, "not a member")
    owners = db.execute(select(func.count()).select_from(Membership).where(
        Membership.project_id == pid, Membership.role == "owner")).scalar()
    if m.role == "owner" and owners <= 1:
        raise HTTPException(400, "a project needs at least one owner")
    db.delete(m)
    db.commit()
    return project_json(db, p, role, detail=True)


# -------------------------------------------------------------- status ---

@router.get("/{pid}/status")
def project_status(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, role = get_project_for(db, pid, user)
    try:
        files = documents.live_files(pid)
        cur = store.tree_hash(files)
        live_error = ""
    except documents.CollabUnavailable as e:
        cur, live_error = "", str(e)
    ok = db.get(Build, p.last_success_build_id) if p.last_success_build_id else None
    last = db.get(Build, p.last_build_id) if p.last_build_id else None
    return {
        "current_hash": cur, "live_error": live_error,
        "last_build": build_json(last), "last_success_build": build_json(ok),
        "pdf_stale": bool(ok and cur and ok.source_hash != cur),
        "active_tasks": [
            {"id": t.id, "status": t.status, "mode": t.mode, "executor": t.executor_used}
            for t in db.execute(select(Task).where(Task.project_id == pid, Task.status.in_(
                ("queued", "planning", "running", "verifying")))).scalars()
        ],
        "open_proposals": db.execute(select(func.count()).select_from(Proposal).where(
            Proposal.project_id == pid, Proposal.status.in_(("ready", "conflict", "verifying", "applying")))).scalar(),
    }


# -------------------------------------------------------------- export ---

@router.get("/{pid}/export.zip")
def export_zip(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user)
    files = documents.live_files(pid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in sorted(files.items()):
            zf.writestr(rel, data)
    buf.seek(0)
    fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", p.name)[:80] or "project"
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}.zip"'})


@router.get("/{pid}/export-arxiv.zip")
def export_arxiv(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Sources for an arXiv upload: no hidden/aux files, with the .bbl of the last compile when the paper uses BibTeX/biblatex."""
    p, _ = get_project_for(db, pid, user)
    files = documents.live_files(pid)
    bbl = None
    if arxiv.needs_bbl(files, p.main_file):
        b = db.get(Build, p.last_success_build_id) if p.last_success_build_id else None
        if b is None or b.source_hash != store.tree_hash(files):
            raise HTTPException(409, "Compile the paper first: arXiv needs the .bbl of a build that matches the current text.")
        bp = pdf_path(b.id).with_name("output.bbl")
        if not bp.exists():
            raise HTTPException(409, "The last build kept no .bbl (it predates this feature): compile again, then export.")
        bbl = bp.read_bytes()
    data = arxiv.source_zip(files, p.main_file, bbl)
    fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", p.name)[:80] or "project"
    return StreamingResponse(io.BytesIO(data), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{fname}-arxiv.zip"'})


@router.get("/{pid}/export.pdf")
def export_pdf(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user)
    if not p.last_success_build_id or not pdf_path(p.last_success_build_id).exists():
        raise HTTPException(404, "no successful build yet")
    fname = re.sub(r"[^A-Za-z0-9_.-]+", "_", p.name)[:80] or "paper"
    return FileResponse(pdf_path(p.last_success_build_id), media_type="application/pdf", filename=f"{fname}.pdf")


# ------------------------------------------------------------ versions ---

@router.get("/{pid}/versions")
def versions(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    labels = {}
    for v in db.execute(select(ProjectVersion).where(ProjectVersion.project_id == pid)).scalars():
        labels.setdefault(v.revision, []).append(v.label)
    return [{**c, "labels": labels.get(c["revision"], [])} for c in store.log(pid, 200)]


@router.post("/{pid}/versions")
def save_version(pid: str, body: VersionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    snap = documents.snapshot(pid, author=user.display_name or user.username, message=f"Version: {body.label}")
    db.add(ProjectVersion(project_id=pid, revision=snap.revision, label=body.label, created_by=user.id))
    _touch(db, p)
    return {"revision": snap.revision, "label": body.label}


@router.get("/{pid}/versions/{rev}/diff")
def version_diff(pid: str, rev: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    if not store.revision_exists(pid, rev):
        raise HTTPException(404, "unknown revision")
    old = store.files_at(pid, rev)
    cur = documents.live_files(pid)
    out = []
    for rel in sorted(set(old) | set(cur)):
        a, b = old.get(rel), cur.get(rel)
        if a == b:
            continue
        item = {"path": rel, "change": "added" if a is None else "deleted" if b is None else "modified"}
        if store.is_text(rel, a if a is not None else b):
            item["hunks"] = pdiff.hunks((a or b"").decode("utf-8", "replace"), (b or b"").decode("utf-8", "replace"))
        out.append(item)
    return {"revision": rev, "files": out}


@router.post("/{pid}/versions/{rev}/restore")
def restore(pid: str, rev: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, _ = get_project_for(db, pid, user, "editor")
    try:
        prop = proposals.restore_version(db, p, rev, {"id": user.id, "name": user.display_name or user.username})
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return proposals.proposal_json(db, prop)


# -------------------------------------------------------------- collab ---

@router.get("/{pid}/collab-token")
def collab_token(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = get_settings()
    p, role = get_project_for(db, pid, user)
    if not s.collab_enabled:
        return {"enabled": False}
    payload = json.dumps({"pid": pid, "uid": user.id, "name": user.display_name or user.username, "role": role,
                          "exp": int(time.time()) + 3600}, separators=(",", ":")).encode()
    tok = base64.urlsafe_b64encode(payload).decode().rstrip("=") + "." + sign(s.collab_shared_secret, payload)
    return {"enabled": True, "url": s.collab_public_url, "room": pid, "token": tok, "readonly": role == "viewer"}
