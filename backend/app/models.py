from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC timestamps on every backend (SQLite drops tzinfo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(256))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    main_file: Mapped[str] = mapped_column(String(512), default="main.tex")
    engine: Mapped[str] = mapped_column(String(16), default="pdflatex")
    # {allowed_executors, default_executor, run_timeout_seconds, task_timeout_seconds, max_attempts}
    agent_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    last_build_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_success_build_id: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # owner | editor | viewer


class Build(Base):
    __tablename__ = "builds"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="main")  # main | proposal
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # queued | running | succeeded | failed | cancelled | blocked
    status: Mapped[str] = mapped_column(String(16), default="queued")
    revision: Mapped[str] = mapped_column(String(64), default="")
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    engine: Mapped[str] = mapped_column(String(16), default="pdflatex")
    main_file: Mapped[str] = mapped_column(String(512), default="main.tex")
    isolation: Mapped[str] = mapped_column(String(32), default="")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_text: Mapped[str] = mapped_column(Text, default="")
    diagnostics: Mapped[list] = mapped_column(JSON, default=list)
    has_pdf: Mapped[bool] = mapped_column(Boolean, default=False)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_lines: Mapped[list | None] = mapped_column(JSON, nullable=True)  # text lines per PDF page
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class Task(Base):
    """A user request handled by the LLM orchestrator."""

    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # queued | planning | running | verifying | needs_review | completed | failed | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued")
    mode: Mapped[str] = mapped_column(String(16), default="auto")  # auto|llm|codex|claude|antigravity
    request: Mapped[str] = mapped_column(Text)
    context_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    contract: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    plan_source: Mapped[str] = mapped_column(String(32), default="")  # llm-tools|llm-json|server|demo
    executor_used: Mapped[str] = mapped_column(String(32), default="")
    base_revision: Mapped[str] = mapped_column(String(64), default="")
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ToolRun(Base):
    """One external tool invocation (one attempt)."""

    __tablename__ = "tool_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    executor: Mapped[str] = mapped_column(String(32))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    # running | succeeded | failed | timeout | cancelled | rejected | unavailable
    status: Mapped[str] = mapped_column(String(16), default="running")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_id: Mapped[str] = mapped_column(String(128), default="")
    isolation: Mapped[str] = mapped_column(String(32), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    changed_files: Mapped[list] = mapped_column(JSON, default=list)
    violations: Mapped[list] = mapped_column(JSON, default=list)
    log_path: Mapped[str] = mapped_column(String(1024), default="")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | system
    content: Mapped[str] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Proposal(Base):
    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    origin: Mapped[str] = mapped_column(String(32), default="agent")  # agent|selection|resolution|restore|merge
    base_revision: Mapped[str] = mapped_column(String(64))
    # verifying | ready | conflict | applying | applied | rejected | failed | superseded
    status: Mapped[str] = mapped_column(String(16), default="verifying")
    title: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    executor: Mapped[str] = mapped_column(String(32), default="")
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{path, change, binary}]
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    conflicts: Mapped[list] = mapped_column(JSON, default=list)
    candidate_build_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_revision: Mapped[str] = mapped_column(String(64), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)


class Job(Base):
    """Durable work queue row, claimed by worker processes."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))  # compile | agent | verify_proposal | apply_proposal
    project_id: Mapped[str] = mapped_column(String(32), index=True)
    ref_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    worker_id: Mapped[str] = mapped_column(String(128), default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ProjectVersion(Base):
    """Named versions (git commits the user chose to label)."""

    __tablename__ = "project_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    revision: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=now)
