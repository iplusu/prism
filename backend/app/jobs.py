"""Durable job queue stored in the database.

PostgreSQL: rows are claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, so
several workers can run safely. SQLite (development/tests): a conditional
UPDATE claims the row; run a single worker.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Job, TaskEvent, now

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


def enqueue(db: Session, kind: str, project_id: str, ref_id: str, payload: dict | None = None) -> Job:
    j = Job(kind=kind, project_id=project_id, ref_id=ref_id, payload=payload or {})
    db.add(j)
    db.flush()
    return j


def claim(db: Session, kinds: list[str] | None = None) -> Job | None:
    q = select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
    if kinds:
        q = q.where(Job.kind.in_(kinds))
    if get_settings().is_postgres:
        job = db.execute(q.with_for_update(skip_locked=True)).scalar_one_or_none()
        if job is None:
            db.rollback()
            return None
        job.status = "running"
        job.worker_id = WORKER_ID
        job.started_at = job.heartbeat_at = now()
        db.commit()
        return job
    job = db.execute(q).scalar_one_or_none()
    if job is None:
        return None
    res = db.execute(
        update(Job).where(Job.id == job.id, Job.status == "queued")
        .values(status="running", worker_id=WORKER_ID, started_at=now(), heartbeat_at=now())
    )
    db.commit()
    if res.rowcount != 1:
        return None
    db.refresh(job)
    return job


def finish(db: Session, job: Job, status: str, error: str = "") -> None:
    job.status = status
    job.error = error[:4000]
    job.finished_at = now()
    db.commit()


def heartbeat(db: Session, job_id: str) -> None:
    db.execute(update(Job).where(Job.id == job_id).values(heartbeat_at=now()))
    db.commit()


def requeue_stale(db: Session, older_than_seconds: int = 120) -> int:
    """Jobs whose worker died (no heartbeat) are marked failed, not silently
    re-run: agent tasks have side effects the user should see."""
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    rows = db.execute(select(Job).where(Job.status == "running", Job.heartbeat_at < cutoff)).scalars().all()
    for j in rows:
        j.status = "failed"
        j.error = "worker stopped responding"
        j.finished_at = now()
    db.commit()
    return len(rows)


def emit(db: Session, task_id: str, type_: str, message: str = "", data: dict | None = None, commit: bool = True) -> None:
    from .security.redact import redact

    db.add(TaskEvent(task_id=task_id, type=type_, message=redact(message)[:8000], data=data or {}))
    if commit:
        db.commit()
