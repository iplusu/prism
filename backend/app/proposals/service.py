r"""Reviewable change proposals.

Life cycle:  verifying -> ready -> (applying) -> applied
                       \-> failed            \-> conflict -> (resolution proposal)
             any open  -> rejected | superseded

Agents never touch the live manuscript. A proposal stores the base revision
and the candidate content of each changed file. Applying it:

 1. read the latest live state;
 2. if the touched files still equal the proposal base, write the candidate
    conditionally (precondition = current content hash);
 3. otherwise 3-way merge each file (base, current, candidate). Conflicts stop
    the apply and are shown for resolution. A clean merge becomes a new
    `merge` proposal that is re-verified (compiled + checked) and then applied
    with the same conditional write.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs
from ..builds import build_json, run_build
from ..config import get_settings
from ..models import Build, Project, Proposal, Task, now
from ..projects import documents, store
from ..projects.documents import Change, ConflictError
from ..security.paths import is_protected, normalize_rel_path
from . import analysis, diff, merge

OPEN_STATES = ("verifying", "ready", "conflict", "applying")


def pdir(prop_id: str) -> Path:
    return get_settings().data_dir / "proposals" / prop_id


def _side_path(prop_id: str, side: str, rel: str) -> Path:
    return pdir(prop_id) / side / rel


def read_side(prop_id: str, side: str, rel: str) -> bytes | None:
    p = _side_path(prop_id, side, rel)
    return p.read_bytes() if p.is_file() else None


def create(db: Session, project: Project, *, base_revision: str, base_files: dict[str, bytes],
           changes: dict[str, bytes | None], origin: str, title: str, summary: str = "", executor: str = "",
           task_id: str | None = None, parent_id: str | None = None, created_by: str | None = None,
           demo: bool = False, checks: list[str] | None = None, scope_ok: bool = True,
           auto_apply: bool = False, extra_report: dict | None = None) -> Proposal:
    prop = Proposal(project_id=project.id, task_id=task_id, parent_id=parent_id, origin=origin,
                    base_revision=base_revision, title=title[:300], summary=summary, executor=executor,
                    created_by=created_by, demo=demo, status="verifying")
    db.add(prop)
    db.flush()
    files_meta = []
    for rel in sorted(changes):
        rel = normalize_rel_path(rel)
        new = changes[rel]
        old = base_files.get(rel)
        if new is not None and old == new:
            continue
        if old is not None:
            _side_path(prop.id, "base", rel).parent.mkdir(parents=True, exist_ok=True)
            _side_path(prop.id, "base", rel).write_bytes(old)
        if new is not None:
            _side_path(prop.id, "candidate", rel).parent.mkdir(parents=True, exist_ok=True)
            _side_path(prop.id, "candidate", rel).write_bytes(new)
        change = "added" if old is None else ("deleted" if new is None else "modified")
        binary = not store.is_text(rel, new if new is not None else old)
        meta = {"path": rel, "change": change, "binary": binary,
                "base_sha": store.sha256(old) if old is not None else None,
                "new_sha": store.sha256(new) if new is not None else None}
        if not binary:
            meta.update(diff.stats((old or b"").decode("utf-8"), (new or b"").decode("utf-8")))
        files_meta.append(meta)
    prop.files = files_meta
    prop.report = {"checks_requested": checks or ["compiles", "no_new_undefined_references", "no_new_undefined_citations"],
                   "scope_ok": scope_ok, "auto_apply": auto_apply, **(extra_report or {})}
    if not files_meta:
        prop.status = "failed"
        prop.error = "the proposal contains no changes"
    else:
        jobs.enqueue(db, "verify_proposal", project.id, prop.id)
    db.commit()
    return prop


def candidate_files(prop: Proposal, base_files: dict[str, bytes] | None = None) -> dict[str, bytes]:
    files = dict(base_files if base_files is not None else store.files_at(prop.project_id, prop.base_revision))
    for f in prop.files:
        if f["change"] == "deleted":
            files.pop(f["path"], None)
        else:
            data = read_side(prop.id, "candidate", f["path"])
            assert data is not None
            files[f["path"]] = data
    return files


def _base_build(db: Session, project: Project, revision: str, files: dict[str, bytes]) -> Build:
    b = db.execute(select(Build).where(Build.project_id == project.id, Build.revision == revision,
                                       Build.status.in_(("succeeded", "failed")), Build.engine == project.engine,
                                       Build.main_file == project.main_file)
                   .order_by(Build.created_at.desc()).limit(1)).scalar_one_or_none()
    if b is not None:
        return b
    b = Build(project_id=project.id, kind="base", revision=revision, engine=project.engine, main_file=project.main_file,
              source_hash=store.tree_hash(files))
    db.add(b)
    db.commit()
    return run_build(db, b, files=files)


def verify(db: Session, prop: Proposal, should_cancel=lambda: False) -> Proposal:
    """Compile the candidate, compare it with the base, run the checks."""
    project = db.get(Project, prop.project_id)
    assert project is not None
    base = store.files_at(project.id, prop.base_revision)
    cand = candidate_files(prop, base)
    base_build = _base_build(db, project, prop.base_revision, base)
    b = Build(project_id=project.id, kind="proposal", proposal_id=prop.id, revision=prop.base_revision,
              engine=project.engine, main_file=project.main_file, source_hash=store.tree_hash(cand))
    db.add(b)
    db.commit()
    run_build(db, b, files=cand, should_cancel=should_cancel)
    prop.candidate_build_id = b.id
    rep = analysis.analyze(base, cand, [f["path"] for f in prop.files])
    wb = analysis.warning_counts(base_build.diagnostics)
    wc = analysis.warning_counts(b.diagnostics)
    delta = {k: wc[k] - wb[k] for k in wc}
    build_ok = None if b.status in ("blocked", "cancelled") else b.status == "succeeded"
    base_ok = None if base_build.status in ("blocked", "cancelled") else base_build.status == "succeeded"
    checks = analysis.evaluate_checks(prop.report.get("checks_requested", []), rep, build_ok, base_ok,
                                      prop.report.get("scope_ok", True), delta)
    report = dict(prop.report)
    report.update({
        "analysis": rep, "checks": checks,
        "build": {"candidate": b.status, "candidate_isolation": b.isolation, "base": base_build.status,
                  "base_build_id": base_build.id, "warnings_base": wb, "warnings_candidate": wc,
                  "blocked_reason": b.error if b.status == "blocked" else ""},
        "limitations": [
            "A successful compilation does not mean the content or the layout is correct.",
            "No model inspected the rendered PDF; review the candidate PDF yourself.",
        ],
        "verified_at": now().isoformat(),
    })
    prop.report = report
    prop.status = "ready"
    prop.updated_at = now()
    db.commit()
    if report.get("auto_apply"):
        if all(c["status"] != "failed" for c in checks) and build_ok is not False:
            prop.status = "applying"
            jobs.enqueue(db, "apply_proposal", project.id, prop.id, {"user": prop.created_by})
            db.commit()
    return prop


# -------------------------------------------------------------------- apply ---

def request_apply(db: Session, prop: Proposal, user_id: str, author: str = "Prism") -> None:
    if prop.status != "ready":
        raise ValueError(f"proposal is {prop.status}; only verified (ready) proposals can be applied")
    prop.status = "applying"
    prop.decided_by = user_id
    jobs.enqueue(db, "apply_proposal", prop.project_id, prop.id, {"user": user_id, "author": author})
    db.commit()


def apply(db: Session, prop: Proposal, author: str = "Prism", max_races: int = 3) -> Proposal:
    project = db.get(Project, prop.project_id)
    assert project is not None
    for _ in range(max_races):
        live = documents.live_files(project.id)
        stale = []
        changes: list[Change] = []
        for f in prop.files:
            rel = f["path"]
            cur = live.get(rel)
            cur_sha = store.sha256(cur) if cur is not None else None
            new = read_side(prop.id, "candidate", rel)
            if cur_sha == f.get("new_sha"):
                continue  # already in the desired state
            if cur_sha != f.get("base_sha"):
                stale.append(rel)
            changes.append(Change(path=rel, content=new, expected_sha=cur_sha))
        if stale:
            return _merge_stale(db, project, prop, live, author)
        try:
            documents.apply_changes(project.id, changes)
        except ConflictError:
            continue  # someone typed between our read and write; re-evaluate
        snap = documents.snapshot(project.id, author=author, message=f"Apply proposal: {prop.title[:80]}")
        prop.status = "applied"
        prop.applied_revision = snap.revision
        prop.updated_at = now()
        _close_task(db, prop, "completed")
        db.commit()
        return prop
    prop.status = "ready"
    prop.error = "the manuscript kept changing during apply; try again"
    db.commit()
    return prop


def _merge_stale(db: Session, project: Project, prop: Proposal, live: dict[str, bytes], author: str) -> Proposal:
    conflicts = []
    merged: dict[str, bytes | None] = {}
    for f in prop.files:
        rel = f["path"]
        base = read_side(prop.id, "base", rel)
        cand = read_side(prop.id, "candidate", rel)
        cur = live.get(rel)
        if cur == base:
            merged[rel] = cand
            continue
        if cur == cand:
            continue
        if f["binary"] or base is None or cand is None or cur is None:
            conflicts.append({"path": rel, "reason": "changed on both sides (file added/deleted or binary)",
                              "current": None if cur is None or f["binary"] else cur.decode("utf-8"),
                              "proposal": None if cand is None or f["binary"] else cand.decode("utf-8"),
                              "merged": None})
            continue
        text, n = merge.merge3(base.decode("utf-8"), cur.decode("utf-8"), cand.decode("utf-8"))
        if n:
            conflicts.append({"path": rel, "reason": f"{n} conflicting region(s)", "merged": text,
                              "current": cur.decode("utf-8"), "proposal": cand.decode("utf-8")})
        else:
            merged[rel] = text.encode("utf-8")
    if conflicts:
        prop.status = "conflict"
        prop.conflicts = conflicts
        prop.updated_at = now()
        db.commit()
        return prop
    snap = documents.snapshot(project.id, author=author, message="Snapshot before merging proposal")
    child = create(db, project, base_revision=snap.revision, base_files=snap.files, changes=merged, origin="merge",
                   title=prop.title, summary=prop.summary + "\n\n(Re-based onto the latest manuscript by 3-way merge.)",
                   executor=prop.executor, task_id=prop.task_id, parent_id=prop.id, created_by=prop.decided_by,
                   demo=prop.demo, checks=prop.report.get("checks_requested"), scope_ok=prop.report.get("scope_ok", True),
                   auto_apply=True)
    prop.status = "superseded"
    prop.error = f"manuscript changed since the base revision; merged cleanly into proposal {child.id}, which is re-verified before it is applied"
    prop.updated_at = now()
    if prop.task_id:
        t = db.get(Task, prop.task_id)
        if t is not None:
            t.proposal_id = child.id
    db.commit()
    return prop


def _close_task(db: Session, prop: Proposal, status: str) -> None:
    if prop.task_id:
        t = db.get(Task, prop.task_id)
        if t is not None and t.status == "needs_review":
            t.status = status
            t.updated_at = now()


def reject(db: Session, prop: Proposal, user_id: str) -> None:
    if prop.status not in ("ready", "conflict", "verifying", "failed"):
        raise ValueError(f"proposal is {prop.status}")
    prop.status = "rejected"
    prop.decided_by = user_id
    prop.updated_at = now()
    _close_task(db, prop, "completed")
    db.commit()


def select_hunks(db: Session, prop: Proposal, selections: dict[str, list[int]], user_id: str) -> Proposal:
    """Build a new proposal containing only the chosen hunks; it is compiled
    and checked again before it can be applied."""
    if prop.status not in ("ready", "conflict"):
        raise ValueError(f"proposal is {prop.status}")
    project = db.get(Project, prop.project_id)
    base = store.files_at(prop.project_id, prop.base_revision)
    changes: dict[str, bytes | None] = {}
    for f in prop.files:
        rel = f["path"]
        chosen = set(selections.get(rel, []))
        if not chosen:
            continue
        if f["binary"] or f["change"] != "modified":
            changes[rel] = read_side(prop.id, "candidate", rel)  # whole-file change
            continue
        b = read_side(prop.id, "base", rel).decode("utf-8")
        c = read_side(prop.id, "candidate", rel).decode("utf-8")
        changes[rel] = diff.apply_hunks(b, c, chosen).encode("utf-8")
    if not changes:
        raise ValueError("no hunks selected")
    child = create(db, project, base_revision=prop.base_revision, base_files=base, changes=changes, origin="selection",
                   title=f"{prop.title} (selected changes)", summary=prop.summary, executor=prop.executor,
                   task_id=prop.task_id, parent_id=prop.id, created_by=user_id, demo=prop.demo,
                   checks=prop.report.get("checks_requested"), scope_ok=prop.report.get("scope_ok", True))
    prop.status = "superseded"
    prop.updated_at = now()
    if prop.task_id:
        t = db.get(Task, prop.task_id)
        if t is not None:
            t.proposal_id = child.id
    db.commit()
    return child


def resolve(db: Session, prop: Proposal, resolutions: dict[str, str], user: dict,
            choices: dict[str, str] | None = None) -> Proposal:
    """User-decided resolution of a conflicted proposal: edited text per file,
    or a whole-file choice ("current" / "proposal") for binary, added or
    deleted files. The result is a new proposal based on the current
    manuscript, verified before apply."""
    choices = choices or {}
    if prop.status != "conflict":
        raise ValueError("proposal has no conflicts to resolve")
    for rel, text in resolutions.items():
        if merge.has_conflict_markers(text):
            raise ValueError(f"{rel} still contains conflict markers")
    conflict_paths = {c["path"] for c in prop.conflicts}
    missing = conflict_paths - set(resolutions) - set(choices)
    if missing:
        raise ValueError("resolution missing for: " + ", ".join(sorted(missing)))
    project = db.get(Project, prop.project_id)
    snap = documents.snapshot(project.id, author=user["name"], message="Snapshot before conflict resolution")
    changes: dict[str, bytes | None] = {}
    for f in prop.files:
        rel = f["path"]
        if rel in resolutions:
            changes[rel] = resolutions[rel].encode("utf-8")
            continue
        if rel in choices:
            if choices[rel] == "proposal":
                changes[rel] = read_side(prop.id, "candidate", rel)
            continue  # "current": leave the file as it is now
        base = read_side(prop.id, "base", rel)
        cand = read_side(prop.id, "candidate", rel)
        cur = snap.files.get(rel)
        if cur == base:
            changes[rel] = cand
        elif cur != cand and not f["binary"] and base is not None and cand is not None and cur is not None:
            text, n = merge.merge3(base.decode(), cur.decode(), cand.decode())
            if n:
                raise ValueError(f"{rel} now conflicts as well; reload the review")
            changes[rel] = text.encode()
    changes = {k: v for k, v in changes.items() if v != snap.files.get(k)}
    if not changes:
        raise ValueError("the resolution is identical to the current manuscript; reject the proposal instead")
    child = create(db, project, base_revision=snap.revision, base_files=snap.files, changes=changes, origin="resolution",
                   title=f"{prop.title} (resolved)", summary=prop.summary, executor=prop.executor, task_id=prop.task_id,
                   parent_id=prop.id, created_by=user["id"], demo=prop.demo, checks=prop.report.get("checks_requested"),
                   scope_ok=prop.report.get("scope_ok", True))
    prop.status = "superseded"
    prop.updated_at = now()
    db.commit()
    return child


def restore_version(db: Session, project: Project, revision: str, user: dict) -> Proposal:
    if not store.revision_exists(project.id, revision):
        raise ValueError("unknown revision")
    target = store.files_at(project.id, revision)
    snap = documents.snapshot(project.id, author=user["name"], message="Snapshot before restore")
    changes: dict[str, bytes | None] = {}
    for rel in set(target) | set(snap.files):
        if is_protected(rel):
            continue
        if target.get(rel) != snap.files.get(rel):
            changes[rel] = target.get(rel)
    return create(db, project, base_revision=snap.revision, base_files=snap.files, changes=changes, origin="restore",
                  title=f"Restore version {revision[:10]}", summary=f"Return the manuscript to revision {revision}.",
                  created_by=user["id"])


# ------------------------------------------------------------------ views ---

def proposal_json(db: Session, prop: Proposal, detail: bool = False) -> dict:
    d = {
        "id": prop.id, "project_id": prop.project_id, "task_id": prop.task_id, "parent_id": prop.parent_id,
        "origin": prop.origin, "base_revision": prop.base_revision, "status": prop.status, "title": prop.title,
        "summary": prop.summary, "executor": prop.executor, "files": prop.files, "error": prop.error,
        "demo": prop.demo, "applied_revision": prop.applied_revision, "candidate_build_id": prop.candidate_build_id,
        "created_at": prop.created_at.isoformat() if prop.created_at else None,
        "updated_at": prop.updated_at.isoformat() if prop.updated_at else None,
        "checks": (prop.report or {}).get("checks", []),
    }
    if detail:
        d["report"] = prop.report
        d["conflicts"] = prop.conflicts
        d["candidate_build"] = build_json(db.get(Build, prop.candidate_build_id)) if prop.candidate_build_id else None
        fd = []
        for f in prop.files:
            item = dict(f)
            if not f["binary"]:
                b = (read_side(prop.id, "base", f["path"]) or b"").decode("utf-8", errors="replace")
                c = (read_side(prop.id, "candidate", f["path"]) or b"").decode("utf-8", errors="replace")
                item["hunks"] = diff.hunks(b, c)
            fd.append(item)
        d["file_diffs"] = fd
    return d


def delete_files(prop_id: str) -> None:
    shutil.rmtree(pdir(prop_id), ignore_errors=True)
