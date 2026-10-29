"""Connection test for the orchestrator LLM server.

Checks, in order:
 1. reachability of the API from this backend
 2. the models the server reports and whether LLM_MODEL is one of them
 3. a plain completion and a streamed completion
 4. structured output: tool calling and JSON response format
 5. timeout / failure handling of the client (a deliberately tiny timeout must
    surface as a clean `timeout` error)

The result is stored so that planning (LLM_PLANNING_MODE=auto) uses the
capabilities that were actually observed instead of assuming them.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from ..config import get_settings
from .provider import LLMError, OpenAICompatibleProvider, get_provider

_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "report_sum",
        "description": "Report the sum of two integers.",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}, "sum": {"type": "integer"}},
            "required": ["a", "b", "sum"],
        },
    },
}


def _store_path() -> Path:
    return get_settings().data_dir / "llm_capabilities.json"


def load_capabilities() -> dict | None:
    p = _store_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except ValueError:
        return None
    s = get_settings()
    # Only trust results recorded for the currently configured endpoint/model.
    if data.get("base_url") != s.llm_base_url or data.get("model") != s.llm_model:
        return None
    return data


def _step(name: str):
    return {"name": name, "status": "skipped", "detail": "", "duration_ms": None}


def run_connection_test() -> dict:
    s = get_settings()
    report = {
        "base_url": s.llm_base_url, "model": s.llm_model, "provider": s.llm_provider,
        "tested_at": datetime.now(UTC).isoformat(), "steps": [], "capabilities": {}, "ok": False,
    }
    steps = {k: _step(k) for k in ("reachability", "models", "chat", "streaming", "tool_calling", "json_output", "timeout_handling")}
    report["steps"] = list(steps.values())
    try:
        prov = get_provider()
    except LLMError as e:
        steps["reachability"].update(status="failed", detail=str(e))
        return report

    # 1 + 2 ---------------------------------------------------------------
    t0 = time.monotonic()
    try:
        models = prov.list_models()
        steps["reachability"].update(status="passed", detail=f"{s.llm_base_url} answered /models",
                                     duration_ms=int((time.monotonic() - t0) * 1000))
        ids = [m.get("id", "") for m in models]
        ctx = {}
        for m in models:
            for key in ("max_model_len", "context_length", "context_window", "max_context_length"):
                if key in m:
                    ctx[m.get("id", "")] = {key: m[key]}
            meta = m.get("meta") or {}
            if isinstance(meta, dict) and "n_ctx_train" in meta:
                ctx[m.get("id", "")] = {"n_ctx_train": meta["n_ctx_train"]}
        report["models"] = ids
        report["context_reported"] = ctx or "not reported by server"
        if s.llm_model in ids:
            steps["models"].update(status="passed", detail=f"LLM_MODEL '{s.llm_model}' is served. Models: {', '.join(ids)}")
        else:
            steps["models"].update(status="failed", detail=f"LLM_MODEL '{s.llm_model}' not in served models: {', '.join(ids) or '(none)'}")
    except LLMError as e:
        steps["reachability"].update(status="failed", detail=str(e), duration_ms=int((time.monotonic() - t0) * 1000))
        steps["models"].update(status="skipped", detail="server not reachable")
        report["error_kind"] = e.kind
        return report

    # 3 ------------------------------------------------------------------
    msgs = [{"role": "user", "content": "Reply with exactly the word: pong"}]
    try:
        r = prov.chat(msgs, max_tokens=16, temperature=0)
        ok = bool(r.content.strip())
        steps["chat"].update(status="passed" if ok else "failed", detail=f"reply={r.content.strip()[:60]!r} model={r.model!r}",
                             duration_ms=int(r.latency_seconds * 1000))
        report["served_model_id"] = r.model
    except LLMError as e:
        steps["chat"].update(status="failed", detail=str(e))
    t0 = time.monotonic()
    try:
        chunks, first = [], None
        for d in prov.chat_stream(msgs, max_tokens=16):
            if first is None:
                first = time.monotonic() - t0
            chunks.append(d)
        text = "".join(chunks)
        steps["streaming"].update(status="passed" if chunks else "failed",
                                  detail=f"{len(chunks)} chunks, first after {int((first or 0) * 1000)} ms, text={text.strip()[:40]!r}",
                                  duration_ms=int((time.monotonic() - t0) * 1000))
    except LLMError as e:
        steps["streaming"].update(status="failed", detail=str(e))

    # 4 ------------------------------------------------------------------
    caps = {"tool_calling": False, "json_object": False, "json_schema": False, "streaming": steps["streaming"]["status"] == "passed"}
    try:
        r = prov.chat([{"role": "user", "content": "Use the report_sum tool to report the sum of 17 and 25."}],
                      tools=[_PROBE_TOOL], tool_choice="auto", max_tokens=200, temperature=0)
        call = next((c for c in r.tool_calls if c.name == "report_sum"), None)
        if call:
            try:
                args = json.loads(call.arguments)
                caps["tool_calling"] = isinstance(args, dict) and "sum" in args
                steps["tool_calling"].update(status="passed" if caps["tool_calling"] else "failed",
                                             detail=f"tool call arguments={call.arguments[:120]}", duration_ms=int(r.latency_seconds * 1000))
            except ValueError:
                steps["tool_calling"].update(status="failed", detail=f"tool call with invalid JSON arguments: {call.arguments[:120]}")
        else:
            steps["tool_calling"].update(status="failed", detail=f"no tool call returned (finish_reason={r.finish_reason!r}, content={r.content[:80]!r})")
    except LLMError as e:
        steps["tool_calling"].update(status="failed", detail=f"tools request rejected: {e}")
    jdetail = []
    try:
        r = prov.chat([{"role": "user", "content": 'Return a JSON object {"answer": <the sum of 2 and 3>}.'}],
                      response_format={"type": "json_object"}, max_tokens=60, temperature=0)
        caps["json_object"] = isinstance(json.loads(r.content), dict)
        jdetail.append(f"json_object ok: {r.content.strip()[:60]}")
    except (LLMError, ValueError) as e:
        jdetail.append(f"json_object failed: {str(e)[:120]}")
    try:
        schema = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"], "additionalProperties": False}
        r = prov.chat([{"role": "user", "content": "What is 2 + 3? Answer as JSON."}],
                      response_format={"type": "json_schema", "json_schema": {"name": "answer", "schema": schema, "strict": True}},
                      max_tokens=60, temperature=0)
        obj = json.loads(r.content)
        caps["json_schema"] = isinstance(obj, dict) and isinstance(obj.get("answer"), int)
        jdetail.append(f"json_schema {'ok' if caps['json_schema'] else 'returned wrong shape'}: {r.content.strip()[:60]}")
    except (LLMError, ValueError) as e:
        jdetail.append(f"json_schema failed: {str(e)[:120]}")
    steps["json_output"].update(status="passed" if (caps["json_object"] or caps["json_schema"]) else "failed",
                                detail="; ".join(jdetail))

    # 5 ------------------------------------------------------------------
    if isinstance(prov, OpenAICompatibleProvider):
        # A long generation under a 50 ms budget must surface as a clean
        # `timeout` error instead of hanging the worker.
        tiny = OpenAICompatibleProvider(prov.base_url, prov.model, prov.api_key, timeout=0.05, connect_timeout=0.05)
        t0 = time.monotonic()
        try:
            tiny.chat([{"role": "user", "content": "Count from 1 to 300, one number per line."}], max_tokens=512)
            steps["timeout_handling"].update(status="skipped", detail="the server answered within 50 ms; timeout path not exercised")
        except LLMError as e:
            took = int((time.monotonic() - t0) * 1000)
            steps["timeout_handling"].update(
                status="passed" if e.kind in ("timeout", "connect") and took < 2000 else "failed",
                detail=f"50 ms budget surfaced as '{e.kind}' after {took} ms "
                       f"(configured: total {s.llm_timeout_seconds}s, connect {s.llm_connect_timeout_seconds}s)")
    if caps["tool_calling"]:
        caps["planning_mode"] = "tools"
    elif caps["json_schema"] or caps["json_object"]:
        caps["planning_mode"] = "json"
    else:
        caps["planning_mode"] = "json-prompt"  # JSON requested in the prompt, validated server-side
    report["capabilities"] = caps
    report["ok"] = all(steps[k]["status"] == "passed" for k in ("reachability", "models", "chat"))
    _store_path().write_text(json.dumps(report, indent=2))
    return report
