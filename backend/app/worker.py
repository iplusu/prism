"""Background worker: compiles, agent tasks, proposal verification and apply.

Run separately from the web process:  python -m app.worker
Several workers may run against PostgreSQL.
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from . import jobs
from .builds import run_build, run_build_auto
from .config import get_settings
from .db import SessionLocal
from .models import Build, Job, Project, Proposal, Task, now
from .security.redact import redact

log = logging.getLogger("prism.worker")
_stop = threading.Event()
# Job kinds are taken by separate threads so that a long AI task never holds up a compile (or the other way round).
LANES = [["compile"], ["verify_proposal", "apply_proposal"], ["agent"]]


def _heartbeat_loop(job_id: str, done: threading.Event) -> None:
    while not done.wait(15):
        db = SessionLocal()
        try:
            jobs.heartbeat(db, job_id)
        finally:
            db.close()


def handle(job: Job) -> None:
    db = SessionLocal()
    try:
        if job.kind == "compile":
            b = db.get(Build, job.ref_id)
            if b is None:
                return
            try:
                run_build_auto(db, b)
            except Exception as e:  # noqa: BLE001
                # Never leave the build "running": the UI would show it compiling forever.
                db.rollback()
                b = db.get(Build, job.ref_id)
                if b is not None and b.status in ("queued", "running"):
                    b.status = "failed"
                    b.error = redact(f"{type(e).__name__}: {e}")[:2000]
                    b.finished_at = now()
                    db.commit()
                raise
        elif job.kind == "agent":
            from .agent.orchestrator import Runner

            t = db.get(Task, job.ref_id)
            if t is None:
                return
            if t.cancel_requested:
                t.status = "cancelled"
                db.commit()
                return
            Runner(db, t).run()
        elif job.kind == "verify_proposal":
            from .proposals import service

            p = db.get(Proposal, job.ref_id)
            if p is not None and p.status == "verifying":
                try:
                    service.verify(db, p)
                except Exception as e:
                    p.status = "failed"
                    p.error = f"verification failed: {e}"[:2000]
                    db.commit()
                    raise
        elif job.kind == "apply_proposal":
            from .builds import request_build
            from .proposals import service

            p = db.get(Proposal, job.ref_id)
            if p is None or p.status != "applying":
                return
            author = job.payload.get("author") or "Prism"
            try:
                p = service.apply(db, p, author=author)
            except Exception as e:
                p.status = "ready"
                p.error = f"apply failed: {e}"[:2000]
                db.commit()
                raise
            if p.status == "applied":
                proj = db.get(Project, p.project_id)
                if proj is not None:
                    request_build(db, proj, author, p.decided_by)
        else:
            raise RuntimeError(f"unknown job kind {job.kind}")
    finally:
        db.close()


def run_once(kinds: list[str] | None = None) -> bool:
    db = SessionLocal()
    try:
        job = jobs.claim(db, kinds)
    finally:
        db.close()
    if job is None:
        return False
    done = threading.Event()
    hb = threading.Thread(target=_heartbeat_loop, args=(job.id, done), daemon=True)
    hb.start()
    status, err = "succeeded", ""
    try:
        handle(job)
    except Exception as e:  # noqa: BLE001
        log.exception("job %s failed", job.id)
        status, err = "failed", str(e)
    finally:
        done.set()
        db = SessionLocal()
        try:
            j = db.get(Job, job.id)
            if j is not None:
                jobs.finish(db, j, status, err)
        finally:
            db.close()
    return True


def drain(kinds: list[str] | None = None, max_jobs: int = 100) -> int:
    """Process queued jobs until none are left (used by tests)."""
    n = 0
    while n < max_jobs and run_once(kinds):
        n += 1
    return n


def recover_orphans(db) -> None:
    """After a restart: tasks and builds whose job died are reported as failed, not left spinning."""
    # Tasks whose job died are reported as failed rather than left hanging.
    for t in db.query(Task).filter(Task.status.in_(("planning", "running", "verifying"))).all():
        if not db.query(Job).filter(Job.ref_id == t.id, Job.status.in_(("queued", "running"))).count():
            t.status = "failed"
            t.error = "worker restarted while the task was running"
            t.updated_at = now()
    # Builds whose job died stay "running" and the UI spins (and disables Compile) forever.
    for b in db.query(Build).filter(Build.status.in_(("queued", "running"))).all():
        if not db.query(Job).filter(Job.ref_id == b.id, Job.status.in_(("queued", "running"))).count():
            b.status = "failed"
            b.error = "the worker stopped before this build finished"
            b.finished_at = now()
    db.commit()


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Prism background worker")
    ap.add_argument("--kinds", default="", help="comma-separated job kinds to take "
                    "(compile,verify_proposal,apply_proposal,agent); default: all")
    args = ap.parse_args()
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()] or None
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    from .db import engine
    from .models import Base

    if not s.is_postgres:
        Base.metadata.create_all(engine())
    db = SessionLocal()
    try:
        n = jobs.requeue_stale(db, older_than_seconds=0 if not s.is_postgres else 120)
        recover_orphans(db)
        if n:
            log.warning("marked %d stale jobs as failed", n)
    finally:
        db.close()
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    lanes = [kinds] if kinds else LANES
    log.info("worker started (db=%s, lanes=%s)", "postgres" if s.is_postgres else "sqlite", lanes)
    threads = [threading.Thread(target=_lane_loop, args=(lane, s.worker_poll_seconds), name=f"lane-{'+'.join(lane or ['all'])}")
               for lane in lanes]
    for th in threads:
        th.start()
    for th in threads:
        th.join()


def _lane_loop(kinds: list[str] | None, poll: float) -> None:
    while not _stop.is_set():
        try:
            busy = run_once(kinds)
        except Exception:  # noqa: BLE001 - a lane must not die; the others keep going
            log.exception("lane %s: claiming a job failed", kinds)
            busy = False
        if not busy:
            time.sleep(poll)


if __name__ == "__main__":
    main()
