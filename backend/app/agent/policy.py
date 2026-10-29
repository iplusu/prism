"""Executor selection and budgets. All limits are decided here, by the server,
regardless of what the orchestrator model asked for."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..adapters.registry import adapters
from ..config import get_settings
from ..models import Project, ToolRun

REAL_EXECUTORS = ["codex", "claude", "antigravity"]


def effective_policy(project: Project) -> dict:
    s = get_settings()
    p = dict(project.agent_policy or {})
    order = [e.strip() for e in s.agent_default_order.split(",") if e.strip()]
    allowed = p.get("allowed_executors")
    if allowed is None:
        allowed = list(REAL_EXECUTORS)
    if s.demo_mode and "demo" not in allowed:
        allowed = allowed + ["demo"]
    return {
        "allowed_executors": [e for e in allowed if e in REAL_EXECUTORS + ["demo"]],
        "default_executor": p.get("default_executor", "auto"),
        "preference_order": p.get("preference_order") or order,
        # per-kind routing, e.g. {"bibliography": ["claude", "codex"]}; empty = use preference_order
        "routing": p.get("routing") or {},
        "allow_external": p.get("allow_external", True),
        "run_timeout_seconds": min(int(p.get("run_timeout_seconds") or s.agent_run_timeout_seconds), s.agent_run_timeout_seconds),
        "task_timeout_seconds": min(int(p.get("task_timeout_seconds") or s.agent_task_timeout_seconds), s.agent_task_timeout_seconds),
        "max_attempts": max(1, min(int(p.get("max_attempts") or s.agent_max_attempts), s.agent_max_attempts)),
    }


def recent_failures(db: Session, project_id: str) -> dict[str, str]:
    since = datetime.now(UTC) - timedelta(minutes=get_settings().agent_recent_failure_minutes)
    rows = db.execute(
        select(ToolRun.executor, ToolRun.status, ToolRun.started_at)
        .where(ToolRun.project_id == project_id).order_by(ToolRun.started_at.desc()).limit(50)
    ).all()
    out: dict[str, str] = {}
    seen = set()
    for ex, status, started in rows:
        if ex in seen:
            continue
        seen.add(ex)
        st = started if started.tzinfo else started.replace(tzinfo=UTC)
        if st >= since and status in ("failed", "timeout", "needs_auth", "unavailable"):
            out[ex] = status
    return out


def select_executor(db: Session, project: Project, *, requested: str, task_kind: str,
                    exclude: set[str] | None = None) -> tuple[str | None, str, list[dict]]:
    """Return (executor, reason, considered). `requested` is the user's mode
    or the model's suggestion; explicit user choice wins but must still be
    allowed and available."""
    pol = effective_policy(project)
    exclude = exclude or set()
    reg = adapters()
    fails = recent_failures(db, project.id)
    considered = []
    s = get_settings()

    def usable(name: str) -> tuple[bool, str]:
        if name not in pol["allowed_executors"]:
            return False, "not allowed by project policy"
        a = reg.get(name)
        if a is None:
            return False, "no adapter"
        h = a.health()
        if h["status"] not in ("available",):
            return False, f"{h['status']}: {h.get('detail', '')}"
        if name in exclude:
            return False, "already tried in this task"
        return True, "ok"

    if requested in REAL_EXECUTORS or requested == "demo":
        ok, why = usable(requested)
        considered.append({"executor": requested, "usable": ok, "reason": why})
        return (requested if ok else None), (f"user selected {requested}" if ok else f"{requested} cannot run: {why}"), considered

    if not pol["allow_external"]:
        return None, "external tools are disabled for this project", considered
    order = list(pol["routing"].get(task_kind) or pol["preference_order"])
    if s.demo_mode:
        order = ["demo"] + [e for e in order if e != "demo"]
    order += [e for e in REAL_EXECUTORS if e not in order]
    deferred = []
    for name in order:
        ok, why = usable(name)
        if ok and name in fails:
            deferred.append(name)
            considered.append({"executor": name, "usable": True, "reason": f"deferred: recent {fails[name]}"})
            continue
        considered.append({"executor": name, "usable": ok, "reason": why})
        if ok:
            return name, f"auto: first available in policy order for '{task_kind}'", considered
    if deferred:
        return deferred[0], "auto: only tools with a recent failure are available", considered
    return None, "no external tool is available (see Settings → Tools)", considered
