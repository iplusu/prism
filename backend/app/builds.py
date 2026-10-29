"""Build requests and execution (execution runs in the worker)."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jobs
from .compile.latex import compile_project
from .config import get_settings
from .models import Build, Project, now
from .projects import documents, store
from .security.redact import redact


def build_dir(bid: str) -> Path:
    return get_settings().data_dir / "builds" / bid


def pdf_path(bid: str) -> Path:
    return build_dir(bid) / "output.pdf"


def request_build(db: Session, project: Project, user_name: str, user_id: str | None) -> Build:
    snap = documents.snapshot(project.id, author=user_name, message="Compile")
    b = Build(project_id=project.id, kind="main", revision=snap.revision, source_hash=snap.tree_hash,
              engine=project.engine, main_file=project.main_file, created_by=user_id)
    db.add(b)
    db.flush()
    project.last_build_id = b.id
    jobs.enqueue(db, "compile", project.id, b.id)
    db.commit()
    return b


def run_build(db: Session, build: Build, files: dict[str, bytes] | None = None, should_cancel=lambda: False) -> Build:
    build.status = "running"
    build.started_at = now()
    db.commit()
    if files is None:
        files = store.files_at(build.project_id, build.revision)
    res = compile_project(files, build_dir(build.id), build.main_file, build.engine, should_cancel=should_cancel)
    build.status = res.status
    build.isolation = res.isolation
    build.exit_code = res.exit_code
    build.log_text = redact(res.log[-400_000:])
    build.diagnostics = res.diagnostics[:500]
    build.has_pdf = res.pdf_path is not None
    build.error = redact(res.error)
    build.finished_at = now()
    build.duration_seconds = res.duration
    # Remove sources/aux files; keep the PDF.
    src = build_dir(build.id) / "src"
    if src.exists():
        shutil.rmtree(src, ignore_errors=True)
    if build.kind == "main":
        proj = db.get(Project, build.project_id)
        if proj is not None and build.status == "succeeded":
            prev = proj.last_success_build_id
            proj.last_success_build_id = build.id
            db.commit()
            _prune(db, proj, keep={build.id, prev})
    db.commit()
    return build


def _prune(db: Session, proj: Project, keep: set, retain: int = 10) -> None:
    rows = db.execute(select(Build).where(Build.project_id == proj.id, Build.kind == "main")
                      .order_by(Build.created_at.desc())).scalars().all()
    for b in rows[retain:]:
        if b.id in keep or b.id == proj.last_success_build_id:
            continue
        if b.has_pdf:
            shutil.rmtree(build_dir(b.id), ignore_errors=True)
            b.has_pdf = False
    db.commit()


def build_json(b: Build | None, include_log: bool = False) -> dict | None:
    if b is None:
        return None
    d = {
        "id": b.id, "project_id": b.project_id, "kind": b.kind, "status": b.status, "revision": b.revision,
        "source_hash": b.source_hash, "engine": b.engine, "main_file": b.main_file, "isolation": b.isolation,
        "exit_code": b.exit_code, "diagnostics": b.diagnostics, "has_pdf": b.has_pdf, "error": b.error,
        "created_at": b.created_at.isoformat() if b.created_at else None,
        "finished_at": b.finished_at.isoformat() if b.finished_at else None,
        "duration_seconds": b.duration_seconds, "proposal_id": b.proposal_id,
    }
    if include_log:
        d["log"] = b.log_text
    return d
