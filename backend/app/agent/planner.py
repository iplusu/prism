"""Ask the orchestrator LLM for a structured decision and validate it.

Planning mode:
  tools        OpenAI-style tool calling (used when the connection test saw it work)
  json         response_format json_object/json_schema + server-side validation
  json-prompt  JSON requested in the prompt only, validated server-side
The model's output is never executed; it is parsed into `Plan`, validated,
and dispatched by the server.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from ..config import get_settings
from ..llm.diagnostics import load_capabilities, run_connection_test
from ..llm.provider import LLMError, LLMProvider
from .contract import EXECUTORS, Plan, PLAN_JSON_SCHEMA

SYSTEM = """You are the orchestrator of a LaTeX research-paper workspace used by a research group.
You receive the user's request and selected project context. Decide ONE action:

- "answer": reply directly (questions, explanations, reviews, suggestions without editing files).
- "edit": make small, precise text edits yourself. Each edit gives `path`, an exact `find` string that
  occurs exactly once in that file (copy it verbatim from the context, without line-number prefixes),
  and its `replace` text. Use this for local changes (a sentence, a paragraph, a table cell, a bib entry).
- "delegate": hand larger or multi-file work to an external coding agent. Give a precise `goal`,
  `task_kind`, `write_scope` (project-relative glob patterns of files that may be changed; keep it as narrow
  as possible), optional `read_scope`, `constraints`, and `acceptance_checks`.

Rules:
- Never change content the user did not ask to change. Keep citations, labels, numbers and equations
  unless the request is about them.
- Never invent citations or bibliographic data.
- You cannot run commands; never include shell commands.
- Reply in the language the user wrote in.
- The context has a "PDF page count" section. Use it for page-limit requests ("fit in N pages"); you cannot see or compile the PDF,
  so if the count is unknown or from an older version of the text, say so instead of guessing, and never claim a limit is met.
- `message` is shown to the user: for "answer" it is the answer; otherwise a one-paragraph explanation."""

_TOOLS = [
    {"type": "function", "function": {
        "name": "answer", "description": "Reply to the user without changing files.",
        "parameters": {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}}},
    {"type": "function", "function": {
        "name": "edit", "description": "Propose exact find/replace edits to project files.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"}, "summary": {"type": "string"},
            "edits": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}},
                "required": ["path", "find", "replace"]}}},
            "required": ["message", "edits"]}}},
    {"type": "function", "function": {
        "name": "delegate", "description": "Delegate multi-file or larger work to an external coding agent.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string"}, "summary": {"type": "string"},
            "delegation": PLAN_JSON_SCHEMA["$defs"]["DelegationRequest"]},
            "required": ["message", "delegation"]}}},
]


class PlanError(RuntimeError):
    def __init__(self, message: str, raw: str = ""):
        super().__init__(message)
        self.raw = raw


@dataclass
class PlanOutcome:
    plan: Plan
    mode: str
    raw: str
    repaired: bool


def planning_mode() -> str:
    s = get_settings()
    if s.llm_planning_mode in ("tools", "json", "json-prompt"):
        return s.llm_planning_mode
    caps = load_capabilities()
    if caps is None:
        caps = run_connection_test()
    return (caps.get("capabilities") or {}).get("planning_mode") or "json-prompt"


def _extract_json(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        return m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def _parse(mode: str, content: str, tool_calls) -> tuple[Plan, str]:
    if mode == "tools" and tool_calls:
        call = tool_calls[0]
        raw = call.arguments
        try:
            args = json.loads(raw or "{}")
        except ValueError as e:
            raise PlanError(f"tool call arguments are not valid JSON: {e}", raw) from e
        if call.name not in ("answer", "edit", "delegate"):
            raise PlanError(f"unknown tool '{call.name}'", raw)
        if not isinstance(args, dict):
            raise PlanError("tool arguments must be an object", raw)
        args["action"] = call.name
        try:
            return Plan.model_validate(args), raw
        except ValidationError as e:
            raise PlanError(_fmt(e), raw) from e
    if mode == "tools" and content.strip() and not tool_calls:
        return Plan(action="answer", message=content.strip()[:20000]), content
    raw = _extract_json(content)
    try:
        return Plan.model_validate_json(raw), raw
    except ValidationError as e:
        raise PlanError(_fmt(e), content) from e


def _fmt(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()[:8])


ANALYSIS_MIN_TOKENS = 12000
NO_THINKING = {"chat_template_kwargs": {"enable_thinking": False}}


def plan(provider: LLMProvider, *, request: str, context: str, forced_executor: str | None = None,
         allow_delegate: bool = True, validate_edits=None, answer_only: bool = False,
         history: str = "") -> PlanOutcome:
    mode = planning_mode()
    sys = SYSTEM
    if forced_executor:
        sys += f"\n\nThe user asked for this to be done by the external tool '{forced_executor}': use action \"delegate\"."
    if answer_only:
        sys += ("\n\nThis is an analysis request: use action \"answer\" ONLY. Never edit files, never delegate. "
                "Quote file and line for every point you make, and do not invent citations or facts about the paper.")
    elif not allow_delegate:
        sys += "\n\nDelegation is not available for this request: use \"answer\" or \"edit\" only."
    if mode != "tools":
        sys += ("\n\nRespond with ONLY a JSON object matching this JSON Schema (no prose, no code fences):\n"
                + json.dumps(PLAN_JSON_SCHEMA))
    earlier = (f"# Earlier conversation in this project (oldest first; the request below may refer to it, e.g. "
               f"\"apply the points you raised\")\n{history}\n\n") if history else ""
    messages = [{"role": "system", "content": sys},
                {"role": "user", "content": f"# Project context\n{context}\n\n{earlier}# Request\n{request}"}]
    kwargs: dict = {}
    if mode == "tools":
        tools = [t for t in _TOOLS if (not answer_only or t["function"]["name"] == "answer")
                 and (allow_delegate or t["function"]["name"] != "delegate")]
        kwargs = {"tools": tools, "tool_choice": "auto"}
    elif mode == "json":
        caps = (load_capabilities() or {}).get("capabilities", {})
        if caps.get("json_schema"):
            kwargs = {"response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": PLAN_JSON_SCHEMA}}}
        else:
            kwargs = {"response_format": {"type": "json_object"}}
    last_err = ""
    raw = ""
    # Reasoning models spend hidden tokens before answering. An analysis (whole-project review) can use
    # more than the default budget, so it starts bigger. A request that comes back EMPTY because the budget
    # ran out is retried once with the model's reasoning switched off (a bigger budget would only be
    # thought away again, minutes later) instead of being reported as invalid JSON.
    max_tokens = get_settings().llm_max_output_tokens
    if answer_only:
        max_tokens = max(max_tokens, ANALYSIS_MIN_TOKENS)
    # Plain answers and edits without external tools are planned with the model's reasoning off first: 2-4x faster
    # with the same result in comparison runs. Analyses, delegation and tool choice keep it on. If the quick
    # attempt gives something invalid, the repair attempt runs with reasoning on.
    pref = get_settings().llm_planning_thinking
    quick = pref == "off" or (pref == "auto" and not answer_only and not allow_delegate and not forced_executor)
    thinking_off = quick
    attempt = 0
    while attempt < 2:
        try:
            res = provider.chat(messages, temperature=0.1, max_tokens=max_tokens, **kwargs,
                                **({"extra_body": NO_THINKING} if thinking_off else {}))
        except LLMError:
            raise
        if res.finish_reason == "length" and not res.content.strip() and not res.tool_calls:
            if not thinking_off:
                thinking_off = True
                continue  # same request without hidden reasoning; does not use up the repair attempt
            raise PlanError(f"the model used its whole output budget ({max_tokens} tokens, finish_reason=length) on hidden "
                            "reasoning and produced no answer, also with reasoning switched off; raise LLM_MAX_OUTPUT_TOKENS or use a shorter request", "")
        try:
            p, raw = _parse(mode, res.content, res.tool_calls)
            if answer_only and p.action != "answer":
                raise PlanError("this is an analysis request: the action must be \"answer\"", raw)
            if p.action == "delegate" and not allow_delegate:
                raise PlanError("delegation is not allowed for this request", raw)
            if p.action == "delegate" and p.delegation is None:
                raise PlanError("action 'delegate' requires a 'delegation' object", raw)
            if p.action == "edit" and not p.edits:
                raise PlanError("action 'edit' requires at least one edit", raw)
            if p.action == "edit" and validate_edits is not None:
                problem = validate_edits(p.edits)
                if problem:
                    raise PlanError(problem, raw)
            if p.delegation and p.delegation.executor not in ("auto", *EXECUTORS):
                raise PlanError("invalid executor", raw)
            return PlanOutcome(plan=p, mode=mode, raw=raw, repaired=attempt > 0)
        except PlanError as e:
            attempt += 1
            if quick and pref == "auto":
                thinking_off = False
            last_err = str(e)
            raw = e.raw or res.content
            messages.append({"role": "assistant", "content": (res.content or raw)[:6000]})
            messages.append({"role": "user", "content": f"Your previous output was rejected: {last_err}\n"
                             "Return a corrected decision in the required format."})
    raise PlanError(f"orchestrator output failed validation twice: {last_err}", raw)


def review(provider: LLMProvider, *, request: str, summary: str, report_text: str) -> str:
    """Ask the orchestrator to explain the verified result to the user."""
    msgs = [
        {"role": "system", "content": (
            "You explain the result of an automated manuscript edit to a researcher. Be concise and factual. "
            "Use only the facts given. Mention failed or unevaluated checks explicitly. You have NOT seen the PDF; "
            "never claim the layout or rendering looks correct. Reply in the user's language.")},
        {"role": "user", "content": f"Request:\n{request}\n\nChange summary:\n{summary}\n\nVerification report:\n{report_text}"},
    ]
    return provider.chat(msgs, temperature=0.2, max_tokens=800).content.strip()
