"""Reviewer report: independent read-only reviews by several external tools, consolidated by the orchestrator LLM.

  Codex / Claude Code / Antigravity each get a private copy of the project and a prompt that makes them a
  reviewer (the persona comes from the user). They run in parallel, each in the usual bwrap sandbox, and
  can only read: anything they write stays in their private copy and is discarded. The orchestrator model
  then merges the reports into one: consensus, disagreements and a prioritized plan. A tool that fails is
  reported as failed; nothing is filled in for it.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from .. import jobs
from ..adapters.base import AdapterError, RunSpec
from ..adapters.registry import get_adapter
from ..config import get_settings
from ..db import SessionLocal
from ..llm.provider import LLMError
from ..models import ToolRun, now
from ..security.paths import is_protected
from ..security.redact import redact

DEFAULT_PERSONA = "a critical but fair peer reviewer for the venue this manuscript appears to target"
MAX_REVIEW_CHARS = 14_000  # per review, when handed to the synthesis prompt
LABELS = {"codex": "Codex", "claude": "Claude Code", "antigravity": "Antigravity", "demo": "Demo"}


def reviewer_prompt(persona: str, main_file: str, request: str) -> str:
    return "\n".join([
        "You are reviewing a LaTeX research manuscript. The current directory is a private, read-only-by-contract copy.",
        f"Review it as: {persona}",
        "",
        "Rules:",
        "  - READ the files; do NOT modify, create or delete any file, and do not run LaTeX or the network.",
        "  - Base every point on what is in the files; quote file and line for each. Do not invent related work, citations or results.",
        "  - Be specific and independent: another reviewer is reviewing the same paper separately.",
        "",
        "Write your review with exactly these sections:",
        "  1. Summary of the claimed contribution (2-3 sentences)",
        "  2. Strengths",
        "  3. Weaknesses (each: file:line, what is wrong, why it matters)",
        "  4. Questions for the authors",
        "  5. Concrete improvements, most important first (each: what to change, where)",
        "",
        f"Main file: {main_file}",
        *([f"The user's request: {request}"] if request.strip() else []),
        "Reply in the language of the manuscript. Your final message must be the complete review.",
    ])


def run_review_tool(*, task_id: str, project_id: str, ex: str, files: dict[str, bytes], prompt: str, timeout: int,
                    should_cancel: Callable[[], bool]) -> dict:
    """One reviewer, with its own DB session (called from a thread). Returns {tool, status, text, error}."""
    adapter = get_adapter(ex)
    label = LABELS.get(ex, ex)
    if adapter is None:
        return {"tool": ex, "status": "unavailable", "text": "", "error": "no adapter"}
    run_root = get_settings().data_dir / "tasks" / task_id / f"review-{ex}"
    if run_root.exists():
        shutil.rmtree(run_root)
    work = run_root / "work"
    work.mkdir(parents=True)
    original: dict[str, bytes] = {}
    for rel, data in files.items():
        if is_protected(rel):
            continue
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        original[rel] = data
    db = SessionLocal()
    try:
        run = ToolRun(task_id=task_id, project_id=project_id, executor=ex, attempt=1, log_path=str(run_root / "run" / "tool.log"))
        db.add(run)
        db.commit()
        spec = RunSpec(run_id=run.id, workdir=work, run_dir=run_root / "run", prompt=prompt, timeout_seconds=timeout)
        run.session_id = spec.session_id
        db.commit()
        try:
            handle = adapter.start(spec)
        except AdapterError as e:
            run.status, run.summary, run.finished_at = "unavailable", str(e), now()
            db.commit()
            jobs.emit(db, task_id, "tool_error", f"{label}: {e}", {"executor": ex})
            return {"tool": ex, "status": "unavailable", "text": "", "error": str(e)}
        run.isolation = handle.isolation
        db.commit()
        jobs.emit(db, task_id, "tool_start", f"{label} reviewer started (isolation: {handle.isolation}, limit {timeout}s)",
                  {"executor": ex, "isolation": handle.isolation})
        n = 0
        for ev in adapter.stream_events(handle, should_cancel=should_cancel):
            n += 1
            if n <= 200:
                jobs.emit(db, task_id, "tool_event", ev.get("text", "")[:2000], {"executor": ex, "kind": ev.get("type")})
        result = adapter.collect_result(handle)
        run.exit_code, run.finished_at = result.exit_code, now()
        text = (result.final_message or "").strip()
        if result.status == "succeeded" and not text:
            result.status, result.error = "failed", "the tool finished but returned no review text"
        run.status = result.status
        run.summary = (text if result.status == "succeeded" else result.error)[:4000]
        # Reviewers must not edit. Whatever they wrote stays in the private copy; record that it happened.
        touched = sorted(r for r, d in original.items() if (work / r).exists() and (work / r).read_bytes() != d)
        if touched:
            jobs.emit(db, task_id, "review_note", f"{label} modified {len(touched)} file(s) in its private copy; discarded", {"executor": ex, "files": touched[:20]})
        db.commit()
        jobs.emit(db, task_id, "tool_end", f"{label} reviewer {result.status}: {len(text)} chars" if result.status == "succeeded"
                  else f"{label} reviewer {result.status}: {result.error[:300]}", {"executor": ex, "status": result.status})
        return {"tool": ex, "status": result.status, "text": redact(text) if result.status == "succeeded" else "", "error": result.error}
    finally:
        db.close()
        shutil.rmtree(work, ignore_errors=True)


SYNTH_SYSTEM = (
    "You consolidate independent peer-review reports about one LaTeX manuscript into a single report for its authors. "
    "Use ONLY what the reports say (and the project facts given). Attribute every point to the reviewer(s) who made it "
    "using their names in square brackets. Never invent weaknesses, references, numbers or file locations. "
    "If a reviewer did not deliver, say so; do not guess what they would have said."
)


def synthesis_messages(request: str, personas: dict[str, str], reviews: list[dict], main_file: str) -> list[dict]:
    parts = []
    for r in reviews:
        name = LABELS.get(r["tool"], r["tool"])
        if r["status"] == "succeeded":
            body = r["text"][:MAX_REVIEW_CHARS] + ("\n[... review truncated ...]" if len(r["text"]) > MAX_REVIEW_CHARS else "")
            parts.append(f"## Review by {name} (persona: {personas.get(r['tool'], DEFAULT_PERSONA)})\n{body}")
        else:
            parts.append(f"## {name}: NO REVIEW ({r['status']}: {r['error'][:200]})")
    user = (
        f"Main file: {main_file}\nUser's request: {request or '(none: a general reviewer report)'}\n\n"
        + "\n\n".join(parts)
        + "\n\n# Write the consolidated report with these sections\n"
        "1. Overall assessment (2-4 sentences)\n"
        "2. Points several reviewers agree on (with file:line, naming who)\n"
        "3. Points raised by only one reviewer (name them; say if it needs checking)\n"
        "4. Where the reviewers disagree\n"
        "5. Prioritized improvement plan: numbered, most important first; each item says what to change and where\n"
        "6. Reviewers that did not deliver\n"
        "Reply in the language of the manuscript."
    )
    return [{"role": "system", "content": SYNTH_SYSTEM}, {"role": "user", "content": user}]


def synthesize(provider, messages: list[dict]) -> str:
    """The consolidated report. Merging reports is summarisation, so the first attempt switches the model's hidden
    reasoning off (a reasoning model otherwise spends its whole output budget thinking about 30k characters of
    input and returns nothing); if that gives nothing, one more attempt with reasoning on and a larger budget."""
    base = max(get_settings().llm_max_output_tokens, 8_000)
    attempts = [(base, {"chat_template_kwargs": {"enable_thinking": False}}), (16_384, None)]
    last = ""
    for max_tokens, extra in attempts:
        res = provider.chat(messages, temperature=0.2, max_tokens=max_tokens, extra_body=extra)
        if res.content.strip():
            return res.content.strip()
        last = res.finish_reason
    raise LLMError("protocol", f"the model produced no report (finish_reason={last or 'unknown'}; its output budget may have been used up by hidden reasoning)")


def raw_reviews_text(reviews: list[dict]) -> str:
    out = []
    for r in reviews:
        name = LABELS.get(r["tool"], r["tool"])
        out.append(f"===== {name} =====\n{r['text']}" if r["status"] == "succeeded" else f"===== {name}: no review ({r['status']}) =====\n{r['error'][:300]}")
    return "\n\n".join(out)


def tool_candidates(requested: list[str] | None, allowed: list[str], available: Callable[[str], tuple[bool, str]]) -> tuple[list[str], list[dict]]:
    """Which tools will review, and which were left out (with the reason)."""
    from .policy import REAL_EXECUTORS

    want = [t for t in (requested or REAL_EXECUTORS) if t in REAL_EXECUTORS or t == "demo"]
    use, skipped = [], []
    for t in want:
        if t not in allowed:
            skipped.append({"tool": t, "reason": "not allowed by the project policy"})
            continue
        ok, why = available(t)
        (use if ok else skipped).append(t if ok else {"tool": t, "reason": why})
    return use, skipped


__all__ = ["DEFAULT_PERSONA", "run_review_tool", "reviewer_prompt", "synthesis_messages", "synthesize", "raw_reviews_text", "tool_candidates", "LABELS"]
