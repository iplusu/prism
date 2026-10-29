from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from ..adapters.registry import adapters
from ..compile.latex import sandbox_status, tex_status
from ..config import get_settings
from ..deps import current_user, require_admin
from ..llm.diagnostics import load_capabilities, run_connection_test
from ..models import User
from ..sandbox import bwrap_works

router = APIRouter(prefix="/api/system", tags=["system"])


def _llm_config() -> dict:
    s = get_settings()
    return {"provider": s.llm_provider, "base_url": s.llm_base_url, "model": s.llm_model,
            "api_key_set": bool(s.llm_api_key), "configured": s.llm_configured,
            "timeout_seconds": s.llm_timeout_seconds, "context_chars": s.llm_context_chars,
            "planning_mode_setting": s.llm_planning_mode}


def _collab() -> dict:
    s = get_settings()
    if not s.collab_enabled:
        return {"enabled": False, "status": "disabled"}
    try:
        r = httpx.get(f"{s.collab_internal_url}/health", timeout=3)
        return {"enabled": True, "status": "ok" if r.status_code == 200 else f"http {r.status_code}"}
    except httpx.HTTPError as e:
        return {"enabled": True, "status": f"unreachable: {e}"}


def _tools(force: bool = False) -> list[dict]:
    out = []
    for name, a in adapters().items():
        h = dict(a.health(force=force))
        h["capabilities"] = a.capabilities()
        out.append(h)
    return out


@router.get("/status")
async def status(user: User = Depends(current_user)):
    s = get_settings()
    ok, why = bwrap_works()
    return {
        "demo_mode": s.demo_mode,
        "llm": _llm_config(),
        "llm_last_test": load_capabilities(),
        "tex": await run_in_threadpool(tex_status),
        "compile_sandbox": sandbox_status(),
        "tool_sandbox": {"mode": s.tool_sandbox, "bwrap": ok, "detail": why,
                         "allow_unsandboxed": s.allow_unsandboxed_tools},
        "collab": await run_in_threadpool(_collab),
        "tools": await run_in_threadpool(_tools),
        "limits": {"run_timeout_seconds": s.agent_run_timeout_seconds, "task_timeout_seconds": s.agent_task_timeout_seconds,
                   "max_attempts": s.agent_max_attempts, "compile_timeout_seconds": s.compile_timeout_seconds,
                   "compile_memory_mb": s.compile_memory_mb},
        "agent_default_order": s.agent_default_order,
    }


@router.post("/llm/test")
async def llm_test(user: User = Depends(current_user)):
    return await run_in_threadpool(run_connection_test)


@router.post("/tools/refresh")
async def tools_refresh(user: User = Depends(require_admin)):
    return await run_in_threadpool(_tools, True)
