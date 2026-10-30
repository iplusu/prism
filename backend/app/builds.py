"""Build requests and execution (execution runs in the worker)."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jobs, texpkg
from .compile.latex import compile_project
from .config import get_settings
from .models import Build, Project, now
from .projects import documents, store
from .security.redact import redact

log = logging.getLogger("prism.builds")


def build_dir(bid: str) -> Path:
    return get_settings().data_dir / "builds" / bid


def pdf_path(bid: str) -> Path:
    return build_dir(bid) / "output.pdf"


class BuildTargetError(ValueError):
    """The requested file cannot be compiled as a document."""


def _resolve_target(project_id: str, revision: str, requested: str, project_main: str, fallback: bool) -> tuple[str, str | None]:
    """The .tex file to compile and, when the request had to fall back to the main file, the file asked for."""
    try:
        rel = store.normalize(requested)
    except Exception as e:  # noqa: BLE001
        raise BuildTargetError(f"invalid file name: {e}") from e
    if not rel.lower().endswith(".tex"):
        raise BuildTargetError(f"{rel} is not a .tex file")
    try:
        text = store.git(project_id, "show", f"{revision}:{rel}").decode("utf-8", errors="replace")
    except store.StoreError as e:
        raise BuildTargetError(f"{rel} does not exist in the project") from e
    if "\\documentclass" in text:
        return rel, None
    if fallback and rel != project_main:
        return project_main, rel
    raise BuildTargetError(f"{rel} is not a standalone document (it has no \\documentclass); compile the main file instead")


def request_build(db: Session, project: Project, user_name: str, user_id: str | None, main_file: str | None = None,
                  fallback_to_main: bool = False) -> tuple[Build, str | None]:
    """Queue a compile of `main_file` (default: the project's main file); returns the build and, if the
    request fell back to the main file because the file is not a standalone document, the file asked for."""
    snap = documents.snapshot(project.id, author=user_name, message="Compile")
    target, fell_back_from = project.main_file, None
    if main_file and main_file != project.main_file:
        target, fell_back_from = _resolve_target(project.id, snap.revision, main_file, project.main_file, fallback_to_main)
    b = Build(project_id=project.id, kind="main", revision=snap.revision, source_hash=snap.tree_hash,
              engine=project.engine, main_file=target, created_by=user_id)
    db.add(b)
    db.flush()
    project.last_build_id = b.id
    jobs.enqueue(db, "compile", project.id, b.id)
    db.commit()
    return b, fell_back_from


def run_build(db: Session, build: Build, files: dict[str, bytes] | None = None, should_cancel=lambda: False,
              hold_missing: bool = False, log_prefix: str = "") -> Build:
    """hold_missing: a failure caused by missing TeX files is not reported as final (status stays `running`)
    because the caller is about to install the packages and rebuild."""
    build.status = "running"
    build.started_at = now()
    db.commit()
    if files is None:
        files = store.files_at(build.project_id, build.revision)
    res = compile_project(files, build_dir(build.id), build.main_file, build.engine, should_cancel=should_cancel)
    build.status = res.status
    build.isolation = res.isolation
    build.exit_code = res.exit_code
    build.log_text = log_prefix + redact(res.log[-400_000:])
    build.diagnostics = res.diagnostics[:500]
    build.has_pdf = res.pdf_path is not None
    build.error = redact(res.error)
    build.finished_at = now()
    build.duration_seconds = res.duration
    if hold_missing and res.status == "failed" and texpkg.missing_files(res.log):
        build.status, build.finished_at = "running", None
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


def _note(installed: list[str]) -> str:
    return f"[Prism] installed TeX package(s) {', '.join(installed)} and rebuilt\n\n"


def run_build_auto(db: Session, build: Build, **kw) -> Build:
    """run_build, and when it fails only because TeX Live packages are missing, install them and rebuild.

    At most 5 files per build and 2 rounds; each file is tried once. A file that no TeX Live package
    provides (for example a file of the user's own project) is left to fail with its normal error. While
    this happens the build stays `running`: the failure is not shown until it is really final.
    """
    s = get_settings()
    auto = build.kind == "main" and s.allow_tex_package_install and s.auto_install_tex_packages
    run_build(db, build, hold_missing=auto, **kw)
    if not auto:
        return build
    tried: set[str] = set()
    installed: list[str] = []
    for rnd in range(2):
        if build.status != "running":  # finished: succeeded, cancelled, or failed for another reason
            break
        wanted = [f for f in texpkg.missing_files(build.log_text or "") if f not in tried][:5]
        got = 0
        for f in wanted:
            tried.add(f)
            try:
                res = texpkg.install_for(f)
            except texpkg.TexPackageError as e:
                log.warning("automatic TeX package install failed for %s: %s", f, e)
                continue
            if res["package"]:
                installed.append(res["package"])
                got += 1
                log.warning("TeX package installed automatically: file=%s package=%s project=%s", f, res["package"], build.project_id)
        if not got:
            build.status, build.finished_at = "failed", now()
            if installed and not (build.log_text or "").startswith("[Prism]"):
                build.log_text = _note(installed) + (build.log_text or "")
            db.commit()
            break
        # the note goes into the same commit as the rebuild's final state, so it is never missing from a finished build
        run_build(db, build, hold_missing=rnd == 0, log_prefix=_note(installed), **kw)
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
        d["missing_files"] = texpkg.missing_files(b.log_text or "") if b.status == "failed" else []
    return d
