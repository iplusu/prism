from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import jobs
from ..db import SessionLocal, get_db
from ..deps import current_user, get_project_for
from ..models import Message, Task, TaskEvent, ToolRun, User, now

router = APIRouter(prefix="/api", tags=["tasks"])
ACTIVE = ("queued", "planning", "running", "verifying")


class Selection(BaseModel):
    from_line: int = Field(ge=1)
    to_line: int = Field(ge=1)
    text: str = Field(max_length=100_000)


class ReviewIn(BaseModel):
    """Reviewer report: which tools review and as whom (a persona the user writes)."""
    tools: list[Literal["codex", "claude", "antigravity"]] | None = None  # default: every tool that is allowed and available
    persona: str = Field(default="", max_length=2000)  # shared by all reviewers, e.g. "an ACL area chair"
    personas: dict[Literal["codex", "claude", "antigravity"], str] = Field(default_factory=dict)  # per-tool override

    @field_validator("personas")
    @classmethod
    def _short(cls, v):
        if any(len(x) > 2000 for x in v.values()):
            raise ValueError("a persona is limited to 2000 characters")
        return v


class ContextIn(BaseModel):
    scope: Literal["selection", "file", "project"] = "project"
    path: str | None = None
    selection: Selection | None = None
    answer_only: bool = False  # analysis presets: the task may only answer, never edit or delegate
    review: ReviewIn | None = None  # Reviewer report: independent read-only reviews by several tools, then one consolidated report


class TaskIn(BaseModel):
    request: str = Field(min_length=1, max_length=20_000)
    mode: Literal["auto", "llm", "codex", "claude", "antigravity", "demo"] = "auto"
    context: ContextIn = Field(default_factory=ContextIn)


def task_json(t: Task) -> dict:
    return {"id": t.id, "project_id": t.project_id, "status": t.status, "mode": t.mode, "request": t.request,
            "context_spec": t.context_spec, "contract": t.contract, "plan_source": t.plan_source,
            "executor_used": t.executor_used, "base_revision": t.base_revision, "proposal_id": t.proposal_id,
            "error": t.error, "demo": t.demo, "retry_of": t.retry_of, "cancel_requested": t.cancel_requested,
            "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}


def event_json(e: TaskEvent) -> dict:
    return {"id": e.id, "task_id": e.task_id, "ts": e.ts.isoformat(), "type": e.type, "message": e.message, "data": e.data}


def _create(db: Session, pid: str, user: User, body: TaskIn, retry_of: str | None = None) -> Task:
    if body.mode == "demo":
        from ..config import get_settings

        if not get_settings().demo_mode:
            raise HTTPException(400, "demo executor is only available with DEMO_MODE=true")
    ctx = body.context.model_dump()
    t = Task(project_id=pid, user_id=user.id, mode=body.mode, request=body.request, context_spec=ctx, retry_of=retry_of)
    db.add(t)
    db.flush()
    db.add(Message(project_id=pid, user_id=user.id, role="user", content=body.request, task_id=t.id,
                   meta={"mode": body.mode, "context": ctx}))
    jobs.enqueue(db, "agent", pid, t.id)
    db.commit()
    jobs.emit(db, t.id, "status", "queued")
    return t


@router.post("/projects/{pid}/tasks")
def create_task(pid: str, body: TaskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user, "editor")
    return task_json(_create(db, pid, user, body))


@router.get("/projects/{pid}/tasks")
def list_tasks(pid: str, limit: int = 50, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    rows = db.execute(select(Task).where(Task.project_id == pid).order_by(Task.created_at.desc()).limit(min(limit, 200))).scalars()
    return [task_json(t) for t in rows]


@router.get("/projects/{pid}/messages")
def messages(pid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user)
    rows = db.execute(select(Message).where(Message.project_id == pid, Message.user_id == user.id)
                      .order_by(Message.id.desc()).limit(200)).scalars().all()
    return [{"id": m.id, "role": m.role, "content": m.content, "task_id": m.task_id, "meta": m.meta,
             "created_at": m.created_at.isoformat()} for m in reversed(rows)]


def _task_for(db: Session, tid: str, user: User, min_role: str = "viewer") -> Task:
    t = db.get(Task, tid) if tid.isalnum() else None
    if t is None:
        raise HTTPException(404, "task not found")
    get_project_for(db, t.project_id, user, min_role)
    return t


@router.get("/tasks/{tid}")
def get_task(tid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_for(db, tid, user)
    runs = db.execute(select(ToolRun).where(ToolRun.task_id == tid).order_by(ToolRun.started_at)).scalars()
    out = task_json(t)
    out["tool_runs"] = [{"id": r.id, "executor": r.executor, "attempt": r.attempt, "status": r.status,
                         "exit_code": r.exit_code, "session_id": r.session_id, "isolation": r.isolation,
                         "summary": r.summary, "changed_files": r.changed_files, "violations": r.violations,
                         "started_at": r.started_at.isoformat(),
                         "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs]
    out["events"] = [event_json(e) for e in db.execute(select(TaskEvent).where(TaskEvent.task_id == tid)
                                                        .order_by(TaskEvent.id)).scalars()]
    return out


@router.get("/tasks/{tid}/events")
def task_events(tid: str, after: int = 0, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _task_for(db, tid, user)
    rows = db.execute(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.id > after).order_by(TaskEvent.id)).scalars()
    return [event_json(e) for e in rows]


@router.get("/tasks/{tid}/stream")
async def stream(tid: str, request: Request, after: int = 0, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    """Server-sent events: task events as they are recorded, then `end`."""
    _task_for(db, tid, user)
    # EventSource reconnects send Last-Event-ID; honour it so events are not replayed.
    leid = request.headers.get("last-event-id", "")
    if leid.isdigit():
        after = max(after, int(leid))

    async def gen():
        last = after
        idle = 0
        while True:
            if await request.is_disconnected():
                return
            s = SessionLocal()
            try:
                rows = s.execute(select(TaskEvent).where(TaskEvent.task_id == tid, TaskEvent.id > last)
                                 .order_by(TaskEvent.id).limit(200)).scalars().all()
                t = s.get(Task, tid)
                status = t.status if t else "failed"
            finally:
                s.close()
            for e in rows:
                last = e.id
                yield f"id: {e.id}\nevent: task\ndata: {json.dumps(event_json(e))}\n\n"
            if status not in ACTIVE and not rows:
                yield f"event: end\ndata: {json.dumps({'status': status})}\n\n"
                return
            idle = 0 if rows else idle + 1
            if idle and idle % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/tasks/{tid}/cancel")
def cancel(tid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_for(db, tid, user, "editor")
    if t.status not in ACTIVE:
        raise HTTPException(409, f"task is already {t.status}")
    t.cancel_requested = True
    t.updated_at = now()
    if t.status == "queued":
        t.status = "cancelled"
    db.commit()
    jobs.emit(db, tid, "status", "cancel requested")
    return task_json(t)


@router.post("/tasks/{tid}/retry")
def retry(tid: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_for(db, tid, user, "editor")
    if t.status in ACTIVE:
        raise HTTPException(409, "task is still running")
    body = TaskIn(request=t.request, mode=t.mode, context=ContextIn(**(t.context_spec or {})))
    return task_json(_create(db, t.project_id, user, body, retry_of=t.id))
