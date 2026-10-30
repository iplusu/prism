"""Inline AI completion for the editor (ghost text, accepted with Tab).

The browser sends the text around the cursor; the backend asks the configured
orchestrator LLM for a short continuation. The result is only ever inserted
into the editor by the user pressing Tab, so it needs no verification flow.
Completion competes with real tasks for a shared GPU, so it is limited to one
request per user at a time and a short timeout.
"""

from __future__ import annotations

import re
import threading
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import current_user, get_project_for
from ..llm.provider import LLMError, get_provider
from ..models import User

router = APIRouter(tags=["complete"])

MAX_PREFIX = 3000
MAX_SUFFIX = 1000
MAX_TOKENS = 64

_SYSTEM = (
    "You complete LaTeX manuscripts. The user message holds the text before and after the cursor, "
    "split by the marker <CURSOR>. Reply with ONLY the text to insert at the cursor: usually the rest "
    "of the current sentence or line, at most two sentences. Do not repeat text that is already before "
    "the cursor, do not explain, do not use code fences. Keep the document's language, notation and "
    "LaTeX style. If nothing sensible can be added, reply with nothing."
)

_models_cache: tuple[float, list[str]] = (0.0, [])


def _served_models(provider) -> list[str]:
    at, ids = _models_cache
    if ids and time.monotonic() - at < 60:
        return ids
    ids = [m["id"] for m in provider.list_models() if isinstance(m.get("id"), str)]
    globals()["_models_cache"] = (time.monotonic(), ids)
    return ids


def default_model() -> str:
    s = get_settings()
    return s.llm_completion_model or s.llm_model


@router.get("/api/complete/models")
def models(user: User = Depends(current_user)):
    try:
        ids = _served_models(get_provider())
    except LLMError as e:
        raise HTTPException(503, f"model list unavailable: {e}") from e
    return {"default": default_model(), "models": ids}


_busy: set[str] = set()
_busy_lock = threading.Lock()


class CompleteIn(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    prefix: str = Field(max_length=MAX_PREFIX * 2)
    suffix: str = Field(default="", max_length=MAX_SUFFIX * 2)
    model: str | None = Field(default=None, max_length=200)  # must be one the server lists


def clean_completion(raw: str, prefix: str) -> str:
    """Strip what a chat model adds around an insertion: reasoning blocks, fences, a repeated prefix."""
    t = re.sub(r"<think>.*?</think>", "", raw, flags=re.S)
    t = re.sub(r"^\s*```[a-zA-Z]*\n?|\n?```\s*$", "", t)
    t = t.replace("<CURSOR>", "")
    if not t.strip():
        return ""
    tail = prefix[-200:]
    # model restated the end of the prefix: keep only what comes after it
    for n in range(min(len(tail), len(t)), 3, -1):
        if t.startswith(tail[-n:]):
            t = t[n:]
            break
    return t.rstrip() if "\n" not in t.strip() else t


@router.post("/api/projects/{pid}/complete")
def complete(pid: str, body: CompleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    get_project_for(db, pid, user, "editor")
    prefix, suffix = body.prefix[-MAX_PREFIX:], body.suffix[:MAX_SUFFIX]
    if len(prefix.strip()) < 3:
        return {"completion": "", "model": ""}
    s = get_settings()
    try:
        provider = get_provider()
    except LLMError as e:
        raise HTTPException(503, f"AI completion unavailable: {e}") from e
    model = body.model or default_model()
    if model != default_model():  # the configured default is trusted; anything else must be served
        try:
            served = _served_models(provider)
        except LLMError as e:
            raise HTTPException(503, f"AI completion unavailable: {e}") from e
        if model not in served:
            raise HTTPException(400, f"model '{model}' is not served by the LLM server")
    provider.model = model
    with _busy_lock:
        if user.id in _busy:
            raise HTTPException(429, "a completion is already running")
        _busy.add(user.id)
    try:
        res = provider.chat(
            [{"role": "system", "content": _SYSTEM},
             {"role": "user", "content": f"File: {body.path}\n\n{prefix}<CURSOR>{suffix}"}],
            max_tokens=MAX_TOKENS, temperature=0.0, timeout=s.llm_completion_timeout_seconds,
            # reasoning models would spend the whole budget on hidden thinking (vLLM chat-template switch)
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
    except LLMError as e:
        raise HTTPException(504 if e.kind == "timeout" else 502, f"AI completion failed ({e.kind}): {e}") from e
    finally:
        with _busy_lock:
            _busy.discard(user.id)
    return {"completion": clean_completion(res.content, prefix), "model": res.model,
            "latency_seconds": round(res.latency_seconds, 2)}
