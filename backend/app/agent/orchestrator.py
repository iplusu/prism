"""Runs one user request end to end (inside the worker).

  snapshot -> context -> plan (LLM) -> answer | own edits | delegation
  delegation: contract -> executor selection -> isolated work copy -> tool run
              -> collect real changes -> scope check -> proposal -> verify
              -> LLM review -> needs_review
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from sqlalchemy.orm import Session

from .. import jobs
from ..adapters.base import AdapterError, RunSpec
from ..adapters.registry import adapters, get_adapter
from ..compile.latex import compile_project
from ..config import get_settings
from ..llm.provider import LLMError, get_provider
from ..models import Build, Message, Project, Task, ToolRun, User, now
from ..projects import documents, store
from ..proposals import service as proposals
from ..security.paths import glob_match, is_protected
from ..security.redact import redact
from . import pages as pagelimit
from . import planner, reviewers
from .context import build_context
from .contract import DEFAULT_CHECKS, EXECUTORS, DelegationRequest, Edit, TaskContract
from .policy import effective_policy, select_executor

HISTORY_MAX_MESSAGES = 10
HISTORY_ENTRY_CHARS = 2500
HISTORY_TOTAL_CHARS = 9000


class TaskCancelled(Exception):
    pass


class Runner:
    def __init__(self, db: Session, task: Task):
        self.db = db
        self.task = task
        self.project = db.get(Project, task.project_id)
        user = db.get(User, task.user_id) if task.user_id else None
        self.author = (user.display_name or user.username) if user else "Prism"
        self.settings = get_settings()
        self.started = time.monotonic()
        self.policy = effective_policy(self.project)
        self.deadline = self.started + self.policy["task_timeout_seconds"]

    # ------------------------------------------------------------ helpers ---
    def emit(self, type_: str, message: str = "", **data) -> None:
        jobs.emit(self.db, self.task.id, type_, message, data)

    def set_status(self, status: str) -> None:
        self.task.status = status
        self.task.updated_at = now()
        self.db.commit()
        self.emit("status", status)

    def cancelled(self) -> bool:
        self.db.refresh(self.task, attribute_names=["cancel_requested"])
        return bool(self.task.cancel_requested)

    def check_cancel(self) -> None:
        if self.cancelled():
            raise TaskCancelled()
        if time.monotonic() > self.deadline:
            raise TimeoutError(f"task exceeded its overall limit of {self.policy['task_timeout_seconds']}s")

    def say(self, content: str, **meta) -> None:
        self.db.add(Message(project_id=self.project.id, user_id=self.task.user_id, role="assistant",
                            content=content, task_id=self.task.id, meta=meta))
        self.db.commit()

    def _history(self) -> str:
        """The user's recent chat in this project (their requests and the answers), so that a follow-up such as
        'check whether the points you raised were fixed' has something to refer to. Standalone analysis presets
        and the current task's own message are left out; each entry and the total are capped."""
        rows = (self.db.query(Message)
                .filter(Message.project_id == self.project.id, Message.user_id == self.task.user_id,
                        Message.role.in_(("user", "assistant")))
                .order_by(Message.id.desc()).limit(40).all())
        keep, used = [], 0
        for m in rows:  # newest first
            if m.task_id == self.task.id or (m.meta or {}).get("kind") in ("error", "cancelled"):
                continue
            text = redact(m.content.strip())
            if len(text) > HISTORY_ENTRY_CHARS:
                text = text[:HISTORY_ENTRY_CHARS] + " …[shortened]"
            if used + len(text) > HISTORY_TOTAL_CHARS or len(keep) >= HISTORY_MAX_MESSAGES:
                break
            keep.append(f"{'User' if m.role == 'user' else 'Assistant'}: {text}")
            used += len(text)
        return "\n\n".join(reversed(keep))

    def _page_note(self, tree_hash: str) -> str:
        """What the model may say about the PDF length: the page count of the latest successful compile, and
        whether the text has changed since. It cannot see or compile the PDF itself."""
        b = self.db.get(Build, self.project.last_success_build_id) if self.project.last_success_build_id else None
        if b is None or not b.has_pdf or b.pages is None:
            return ("unknown: this project has no successful compile with a recorded page count. Do not guess the length; "
                    "tell the user to compile first if the request depends on it.")
        fresh = "matches the current text" if b.source_hash == tree_hash else "from an OLDER version of the text (it changed since that compile)"
        from . import pages as pg
        extra = pg.note(b.page_lines, pg.target_pages(self.task.request))
        return f"{b.pages} pages (latest successful compile of {b.main_file}, {fresh})." + (f"\n{extra}" if extra else "")

    # --------------------------------------------------------------- main ---
    def run(self) -> None:
        t = self.task
        try:
            self.set_status("planning")
            snap = documents.snapshot(self.project.id, author=self.author, message=f"Snapshot for AI task {t.id[:8]}")
            t.base_revision = snap.revision
            self.db.commit()
            self.emit("snapshot", f"base revision {snap.revision[:10]}", revision=snap.revision)
            self.files = snap.files
            last = self.db.get(Build, self.project.last_build_id) if self.project.last_build_id else None
            self.page_note = self._page_note(snap.tree_hash)
            ctx = build_context(snap.files, request=t.request, spec=t.context_spec or {}, main_file=self.project.main_file,
                                budget=self.settings.llm_context_chars, diagnostics=last.diagnostics if last else None,
                                pages=self.page_note)
            self.emit("context", f"context: {len(ctx.text)} chars{' (truncated)' if ctx.truncated else ''}",
                      included=ctx.included[:50], truncated=ctx.truncated)
            self.check_cancel()
            self._dispatch(ctx.text)
        except TaskCancelled:
            t.status = "cancelled"
            t.updated_at = now()
            self.db.commit()
            self.emit("status", "cancelled")
            self.say("작업이 취소되었습니다. (Task cancelled.)", kind="cancelled")
        except Exception as e:  # report every failure to the user
            msg = redact(str(e))[:2000]
            t.status = "failed"
            t.error = msg
            t.updated_at = now()
            self.db.commit()
            self.emit("error", msg)
            self.say(f"작업 실패 (task failed): {msg}", kind="error")

    def _dispatch(self, context: str) -> None:
        t = self.task
        s = self.settings
        mode = t.mode
        if (t.context_spec or {}).get("review") is not None:
            return self._review(t.context_spec["review"])
        answer_only = bool((t.context_spec or {}).get("answer_only"))  # analysis presets: the server forbids edits
        explicit_tool = (mode in EXECUTORS or mode == "demo") and not answer_only
        llm_ok = s.llm_configured
        if not llm_ok and not explicit_tool and not s.demo_mode:
            raise RuntimeError("the orchestrator LLM is not configured (set LLM_BASE_URL and LLM_MODEL), "
                               "so Auto/LLM modes cannot run. You can still pick a specific tool.")
        if llm_ok:
            provider = get_provider()
            self.emit("llm", f"planning with {s.llm_model}")
            try:
                outcome = planner.plan(provider, request=t.request, context=context,
                                       forced_executor=mode if explicit_tool else None,
                                       allow_delegate=mode != "llm" and self.policy["allow_external"] and not answer_only,
                                       validate_edits=self._validate_edits, answer_only=answer_only,
                                       history="" if answer_only else self._history())
            except LLMError as e:
                if explicit_tool:
                    self.emit("llm", f"orchestrator unavailable ({e.kind}); using a server-built contract")
                    return self._delegate(self._server_delegation(), plan_source="server", message="")
                raise RuntimeError(f"orchestrator LLM error: {e}") from e
            except planner.PlanError as e:
                self.emit("llm_invalid", str(e), raw=e.raw[:4000])
                if explicit_tool:
                    return self._delegate(self._server_delegation(), plan_source="server", message="")
                raise RuntimeError(f"the orchestrator returned an invalid plan: {e}") from e
            p = outcome.plan
            t.plan_source = f"llm-{outcome.mode}"
            self.db.commit()
            self.emit("plan", f"action={p.action} via {outcome.mode}{' (after repair)' if outcome.repaired else ''}",
                      plan=json.loads(p.model_dump_json()))
            if explicit_tool and p.action != "delegate":
                d = self._server_delegation()
                return self._delegate(d, plan_source="server", message=p.message)
            if p.action == "answer":
                self.say(p.message or "(empty answer)", kind="answer")
                return self._finish("completed")
            if p.action == "edit":
                return self._own_edits(p.edits, p.summary or p.message[:200], p.message)
            return self._delegate(p.delegation, plan_source=t.plan_source, message=p.message)
        if explicit_tool:
            return self._delegate(self._server_delegation(), plan_source="server", message="")
        # DEMO_MODE without an LLM: deterministic, labelled placeholder planning
        t.demo = True
        t.plan_source = "demo"
        self.db.commit()
        self.emit("plan", "DEMO MODE: rule-based plan (no language model connected)")
        if t.request.strip().endswith("?"):
            self.say("[DEMO MODE] No language model is connected, so questions cannot be answered. "
                     "Configure LLM_BASE_URL / LLM_MODEL for real answers.", kind="answer", demo=True)
            return self._finish("completed")
        return self._delegate(self._server_delegation(), plan_source="demo", message="")

    def _review(self, spec: dict) -> None:
        """Reviewer report: the chosen tools review in parallel (read-only), then the orchestrator merges the reports."""
        import threading

        t, s = self.task, self.settings
        if not self.policy["allow_external"]:
            raise RuntimeError("external tools are disabled by the project policy")
        self.deadline = time.monotonic() + s.review_task_timeout_seconds  # reviews are slow: a longer limit than edits
        reg = adapters()

        def available(name: str) -> tuple[bool, str]:
            a = reg.get(name)
            if a is None:
                return False, "no adapter"
            h = a.health()
            return h["status"] == "available", f"{h['status']}: {h.get('detail', '')}"

        use, skipped = reviewers.tool_candidates(spec.get("tools"), self.policy["allowed_executors"], available)
        for sk in skipped:
            self.emit("review_skip", f"{reviewers.LABELS.get(sk['tool'], sk['tool'])} not used: {sk['reason']}", **sk)
        if not use:
            raise RuntimeError("no reviewer tool is available: " + "; ".join(f"{x['tool']} ({x['reason']})" for x in skipped)
                               + " (see Settings → Tools)")
        shared = (spec.get("persona") or "").strip() or reviewers.DEFAULT_PERSONA
        personas = {x: ((spec.get("personas") or {}).get(x) or "").strip() or shared for x in use}
        self.set_status("running")
        self.emit("review", f"{len(use)} reviewer(s) in parallel: " + ", ".join(reviewers.LABELS.get(x, x) for x in use),
                  tools=use, personas=personas)
        cancel = threading.Event()
        timeout = max(60, min(s.review_tool_timeout_seconds, int(self.deadline - time.monotonic())))
        results: dict[str, dict] = {}

        def work(tool: str) -> None:
            try:
                results[tool] = reviewers.run_review_tool(
                    task_id=t.id, project_id=self.project.id, ex=tool, files=self.files, timeout=timeout,
                    prompt=reviewers.reviewer_prompt(personas[tool], self.project.main_file, t.request), should_cancel=cancel.is_set)
            except Exception as e:  # noqa: BLE001  one reviewer failing must not stop the others
                results[tool] = {"tool": tool, "status": "failed", "text": "", "error": redact(f"{type(e).__name__}: {e}")[:500]}

        threads = [threading.Thread(target=work, args=(x,), daemon=True) for x in use]
        for th in threads:
            th.start()
        while any(th.is_alive() for th in threads):
            if not cancel.is_set() and (self.cancelled() or time.monotonic() > self.deadline):
                cancel.set()
            time.sleep(1)
        if self.cancelled():
            raise TaskCancelled()
        reviews = [results[x] for x in use if x in results]
        if not any(r["status"] == "succeeded" for r in reviews):
            raise RuntimeError("no reviewer delivered a review: " + "; ".join(
                f"{reviewers.LABELS.get(r['tool'], r['tool'])}: {r['status']} {r['error'][:150]}" for r in reviews))
        merged, note = None, ""
        if s.llm_configured:
            self.emit("llm", f"consolidating {sum(r['status'] == 'succeeded' for r in reviews)} review(s) with {s.llm_model}")
            try:
                merged = reviewers.synthesize(get_provider(), reviewers.synthesis_messages(t.request, personas, reviews, self.project.main_file))
            except LLMError as e:
                note = f"The orchestrator could not consolidate the reviews ({e.message}); the individual reviews follow unmerged."
        else:
            note = "No orchestrator LLM is configured, so the reviews are shown unmerged."
        body = merged if merged else reviewers.raw_reviews_text(reviews)
        if note:
            self.emit("llm_invalid", note)
            body = note + "\n\n" + body
        self.say(body, kind="review_report", merged=merged is not None, skipped=skipped,
                 reviews=[{"tool": r["tool"], "label": reviewers.LABELS.get(r["tool"], r["tool"]), "status": r["status"],
                           "persona": personas.get(r["tool"], ""), "text": r["text"], "error": r["error"][:500]} for r in reviews])
        return self._finish("completed")

    def _finish(self, status: str) -> None:
        self.set_status(status)

    def _server_delegation(self) -> DelegationRequest:
        spec = self.task.context_spec or {}
        if spec.get("path") and spec.get("scope") in ("file", "selection"):
            scope = [spec["path"]]
        else:
            scope = ["**/*.tex", "**/*.bib"]
        return DelegationRequest(goal=self.task.request, task_kind="other", write_scope=scope,
                                 constraints=["Only change what the request asks for."],
                                 acceptance_checks=list(DEFAULT_CHECKS))

    # ------------------------------------------------------- own edits ---
    def _validate_edits(self, edits: list[Edit]) -> str | None:
        _, problem = self._apply_edits(edits)
        return problem

    def _apply_edits(self, edits: list[Edit]) -> tuple[dict[str, bytes], str | None]:
        out: dict[str, bytes] = {}
        for e in edits:
            cur = out.get(e.path, self.files.get(e.path))
            if cur is None:
                if e.find.strip():
                    return {}, f"{e.path} does not exist"
            text = (cur or b"").decode("utf-8", errors="replace")
            n = text.count(e.find)
            if n != 1:
                return {}, f"edit for {e.path}: `find` text occurs {n} times (must be exactly once): {e.find[:80]!r}"
            out[e.path] = text.replace(e.find, e.replace, 1).encode("utf-8")
        return out, None

    def _own_edits(self, edits: list[Edit], summary: str, message: str) -> None:
        changes, problem = self._apply_edits(edits)
        if problem:
            raise RuntimeError(problem)
        self.task.executor_used = "llm"
        self.db.commit()
        self._propose(changes, executor="llm", summary=summary, message=message,
                      checks=list(DEFAULT_CHECKS), scope_ok=True)

    # ------------------------------------------------------- delegation ---
    def _delegate(self, d: DelegationRequest, plan_source: str, message: str) -> None:
        t = self.task
        if not self.policy["allow_external"]:
            raise RuntimeError("external tools are disabled by the project policy")
        # Only the user's explicit choice pins the tool. A tool the model names in
        # Auto mode is a preference: it is tried first, but failure still moves on.
        requested = t.mode if t.mode in (*EXECUTORS, "demo") else "auto"
        suggested = d.executor if requested == "auto" and d.executor != "auto" else None
        tried: set[str] = set()
        attempts = self.policy["max_attempts"]
        last_error = ""
        for attempt in range(1, attempts + 1):
            self.check_cancel()
            ex, reason, considered = select_executor(self.db, self.project, requested=requested, task_kind=d.task_kind,
                                                     exclude=tried if requested == "auto" else set(), prefer=suggested)
            self.emit("executor", reason, considered=considered, attempt=attempt)
            if ex is None:
                raise RuntimeError(reason + (f" (last error: {last_error})" if last_error else ""))
            remaining = int(self.deadline - time.monotonic())
            contract = TaskContract(
                task_id=t.id, project_id=self.project.id, base_revision=t.base_revision, goal=d.goal,
                task_kind=d.task_kind, executor=ex,
                read_scope=d.read_scope, write_scope=d.write_scope, constraints=d.constraints,
                acceptance_checks=list(d.acceptance_checks), timeout_seconds=max(10, min(self.policy["run_timeout_seconds"], remaining)),
                max_attempts=attempts, task_deadline_seconds=self.policy["task_timeout_seconds"],
            )
            cdict = json.loads(contract.model_dump_json())
            t.contract = cdict
            t.executor_used = ex
            if ex == "demo":
                t.demo = True
            self.db.commit()
            self.set_status("running")
            self.emit("contract", f"contract for {ex}", contract=cdict)
            outcome = self._run_tool(ex, contract, attempt)
            if outcome["status"] == "changes":
                self.set_status("verifying")
                summary = outcome["summary"] or d.goal[:200]
                self._propose(outcome["changes"], executor=ex, summary=summary, message=message,
                              checks=list(d.acceptance_checks), scope_ok=True, tool_message=outcome["final"])
                return
            if outcome["status"] == "no_changes":
                self.say((outcome["final"] or "The tool finished without changing any file.")
                         + ("\n\n[DEMO MODE]" if ex == "demo" else ""), kind="answer", executor=ex)
                return self._finish("completed")
            last_error = outcome["error"]
            tried.add(ex)
            if outcome["status"] == "cancelled":
                raise TaskCancelled()
            if requested != "auto" and outcome["status"] in ("needs_auth", "unavailable"):
                break
            self.emit("retry" if attempt < attempts else "error", f"attempt {attempt} with {ex} failed: {last_error[:300]}")
        raise RuntimeError(f"all attempts failed: {last_error}")

    def _prompt(self, contract: TaskContract, ex: str) -> str:
        if ex == "demo":
            return json.dumps({"goal": contract.goal, "write_scope": contract.write_scope})
        lines = [
            "You are editing a LaTeX research manuscript. The current directory is a private working copy.",
            f"Goal: {contract.goal}",
            "",
            "You may modify ONLY files matching these project-relative patterns:",
            *[f"  - {p}" for p in contract.write_scope],
            "Changes to any other file will cause the whole result to be rejected.",
        ]
        if contract.constraints:
            lines += ["", "Constraints:", *[f"  - {c}" for c in contract.constraints]]
        lines += [
            "", "Rules:",
            "  - Keep \\cite keys, \\label names, numbers and equations unchanged unless the goal requires otherwise.",
            "  - Do not invent references or data.",
            "  - Do not create instruction or configuration files (AGENTS.md, CLAUDE.md, .claude/, latexmkrc, ...).",
            "  - Do not run LaTeX; the server compiles and verifies your result.",
            "", "Acceptance checks the server will run: " + ", ".join(contract.acceptance_checks),
            "", f"Main file: {self.project.main_file}",
            *([f"Current PDF length: {self.page_note}"] if getattr(self, "page_note", "") else []),
            "When finished, reply with a brief summary of the changes.",
        ]
        return "\n".join(lines)

    def _run_tool(self, ex: str, contract: TaskContract, attempt: int) -> dict:
        adapter = get_adapter(ex)
        run_root = self.settings.data_dir / "tasks" / self.task.id / f"attempt-{attempt}"
        if run_root.exists():
            shutil.rmtree(run_root)
        work = run_root / "work"
        work.mkdir(parents=True)
        visible = {}
        for rel, data in self.files.items():
            if is_protected(rel):
                continue
            if not (glob_match(rel, contract.read_scope) or glob_match(rel, contract.write_scope)):
                continue
            p = work / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            visible[rel] = data
        run = ToolRun(task_id=self.task.id, project_id=self.project.id, executor=ex, attempt=attempt,
                      log_path=str(run_root / "run" / "tool.log"))
        self.db.add(run)
        self.db.commit()
        spec = RunSpec(run_id=run.id, workdir=work, run_dir=run_root / "run", prompt=self._prompt(contract, ex),
                       timeout_seconds=contract.timeout_seconds)
        run.session_id = spec.session_id
        self.db.commit()
        try:
            handle = adapter.start(spec)
        except AdapterError as e:
            run.status = "unavailable"
            run.summary = str(e)
            run.finished_at = now()
            self.db.commit()
            self.emit("tool_error", str(e), executor=ex)
            return {"status": "unavailable", "error": str(e)}
        run.isolation = handle.isolation
        self.db.commit()
        self.emit("tool_start", f"{adapter.label} started (isolation: {handle.isolation}, limit {contract.timeout_seconds}s)",
                  executor=ex, session_id=spec.session_id, isolation=handle.isolation)
        last_check = 0.0
        cancel_flag = {"v": False}

        def should_cancel() -> bool:
            nonlocal last_check
            if time.monotonic() - last_check > 1.0:
                last_check = time.monotonic()
                cancel_flag["v"] = self.cancelled() or time.monotonic() > self.deadline
            return cancel_flag["v"]

        n = 0
        for ev in adapter.stream_events(handle, should_cancel=should_cancel):
            n += 1
            if n <= 400:
                self.emit("tool_event", ev.get("text", "")[:2000], executor=ex, kind=ev.get("type"))
        result = adapter.collect_result(handle)
        run.exit_code = result.exit_code
        run.session_id = result.session_id or run.session_id
        run.finished_at = now()
        if result.status != "succeeded":
            run.status = result.status
            run.summary = result.error[:2000]
            self.db.commit()
            self.emit("tool_end", f"{adapter.label} {result.status}: {result.error[:300]}", executor=ex,
                      status=result.status, exit_code=result.exit_code)
            return {"status": result.status, "error": result.error}
        changes, violations = self._collect(work, visible, contract.write_scope)
        run.changed_files = sorted(changes)
        run.violations = violations
        if violations:
            run.status = "rejected"
            run.summary = "changes outside the allowed scope: " + "; ".join(v["path"] for v in violations)[:1000]
            self.db.commit()
            self.emit("scope_violation", run.summary, executor=ex, violations=violations)
            return {"status": "rejected", "error": run.summary}
        run.status = "succeeded"
        run.summary = result.final_message[:4000]
        self.db.commit()
        self.emit("tool_end", f"{adapter.label} finished: {len(changes)} file(s) changed", executor=ex,
                  status="succeeded", exit_code=result.exit_code, changed=sorted(changes))
        if not changes:
            return {"status": "no_changes", "final": result.final_message, "error": ""}
        return {"status": "changes", "changes": changes, "summary": result.final_message.strip().split("\n")[0][:300],
                "final": result.final_message, "error": ""}

    def _collect(self, work: Path, original: dict[str, bytes], write_scope: list[str]) -> tuple[dict, list]:
        """Compare the work copy with what the tool was given. Every change is
        checked against the write scope and the protected-file list; any
        violation rejects the whole result."""
        changes: dict[str, bytes | None] = {}
        violations = []
        seen = set()
        max_bytes = self.settings.max_text_file_mb * 1024 * 1024 * 4
        for root, dirs, fnames in os.walk(work):
            for d in list(dirs):
                if os.path.islink(os.path.join(root, d)):
                    violations.append({"path": os.path.relpath(os.path.join(root, d), work), "reason": "symbolic link created"})
                    dirs.remove(d)
            for fn in fnames:
                full = Path(root) / fn
                rel = full.relative_to(work).as_posix()
                seen.add(rel)
                if full.is_symlink():
                    violations.append({"path": rel, "reason": "symbolic link created"})
                    continue
                data = full.read_bytes()
                if original.get(rel) == data:
                    continue
                if is_protected(rel):
                    violations.append({"path": rel, "reason": "protected instruction/configuration file"})
                elif not glob_match(rel, write_scope):
                    violations.append({"path": rel, "reason": "outside write scope"})
                elif len(data) > max_bytes:
                    violations.append({"path": rel, "reason": "file too large"})
                else:
                    changes[rel] = data
        for rel in original:
            if rel not in seen:
                if glob_match(rel, write_scope) and not is_protected(rel):
                    changes[rel] = None
                else:
                    violations.append({"path": rel, "reason": "deleted outside write scope"})
        return changes, violations

    # ------------------------------------------------------- page limit ---
    def _page_check(self, files: dict[str, bytes], tag: str):
        """Compile `files` for their page count only (nothing is stored as a build)."""
        d = self.settings.data_dir / "tasks" / self.task.id / f"pagecheck-{tag}"
        try:
            return compile_project(files, d, self.project.main_file, self.project.engine, should_cancel=self.cancelled)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _reach_page_limit(self, changes: dict, executor: str, message: str) -> tuple[dict, str]:
        """When the request names a page limit and the candidate still compiles to more pages, ask again (same
        executor) with the measured overflow, up to PAGE_LIMIT_RETRIES times. Returns the (possibly further
        shortened) changes and a plain-language note saying what happened; the note never claims more than measured."""
        t = self.task
        target = pagelimit.target_pages(t.request)
        retries = self.settings.page_limit_retries
        if target is None or retries <= 0 or t.demo or executor == "demo" or (t.context_spec or {}).get("answer_only"):
            return changes, ""
        orig = self.files
        cur = dict(changes)
        history: list[str] = []
        last_pages: int | None = None
        try:
            for rnd in range(retries + 1):
                self.check_cancel()
                cand = dict(orig)
                for k, v in cur.items():
                    if v is None:
                        cand.pop(k, None)
                    else:
                        cand[k] = v
                self.emit("page_check", f"compiling the candidate to count pages (limit {target})", round=rnd)
                res = self._page_check(cand, str(rnd))
                if res.status != "succeeded" or res.pages is None:
                    why = f"the candidate did not compile to a countable PDF ({res.status}{': ' + res.error[:120] if res.error else ''})"
                    self.emit("page_check", why, round=rnd, pages=None)
                    history.append(f"round {rnd + 1}: {why}")
                    last_pages = None
                    break
                self.emit("page_check", f"candidate is {res.pages} pages (limit {target})", round=rnd, pages=res.pages,
                          page_lines=res.page_lines)
                history.append(f"round {rnd + 1}: {res.pages} pages")
                last_pages = res.pages
                if res.pages <= target:
                    break
                if rnd == retries:
                    break
                if int(self.deadline - time.monotonic()) < 90:
                    history.append("stopped retrying: the task time limit is nearly used up")
                    break
                feedback = (f"{t.request}\n\nFOLLOW-UP: the previous attempt compiles to {res.pages} pages but the limit is {target}. "
                            f"{pagelimit.note(res.page_lines, target)}\nShorten further, cutting the least important text first, "
                            f"until it fits {target} pages. Change nothing unrelated.")
                self.emit("page_retry", f"still {res.pages} pages (limit {target}); asking {executor} to shorten further (retry {rnd + 1}/{retries})",
                          round=rnd + 1, pages=res.pages)
                self.files = cand  # the next round works on what the last one produced
                try:
                    more = self._shorten_round(executor, feedback, cand, rnd + 1)
                finally:
                    self.files = orig
                if not more:
                    history.append(f"round {rnd + 2}: {executor} produced no further change")
                    break
                for k, v in more.items():
                    cur[k] = v
                for k in [k for k, v in cur.items() if (v is None and k not in orig) or (v is not None and orig.get(k) == v)]:
                    del cur[k]
        finally:
            self.files = orig
        met = last_pages is not None and last_pages <= target
        note = f"[page limit {target}] " + "; ".join(history) + (". Limit met by this candidate." if met else
               ". The limit was NOT confirmed met; check the candidate PDF.")
        self.emit("page_limit", note, target=target, met=met)
        return cur, note

    def _shorten_round(self, executor: str, feedback: str, cand: dict[str, bytes], rnd: int) -> dict | None:
        """One more shortening pass on `cand` (self.files is already `cand`). Returns changes relative to `cand`."""
        if executor == "llm":
            if not self.settings.llm_configured:
                return None
            ctx = build_context(cand, request=feedback, spec={"scope": "project"}, main_file=self.project.main_file,
                                budget=self.settings.llm_context_chars, pages="")
            try:
                out = planner.plan(get_provider(), request=feedback, context=ctx.text, allow_delegate=False,
                                   validate_edits=self._validate_edits)
            except (LLMError, planner.PlanError) as e:
                self.emit("llm_invalid", f"page-limit retry: {e}")
                return None
            if out.plan.action != "edit":
                return None
            changes, problem = self._apply_edits(out.plan.edits)
            return None if problem else changes
        base = self.task.contract or {}
        if not base:
            return None
        contract = TaskContract(**{**base, "goal": feedback,
                                   "timeout_seconds": max(10, min(base.get("timeout_seconds", 600), int(self.deadline - time.monotonic())))})
        outcome = self._run_tool(executor, contract, 100 + rnd)
        return outcome.get("changes") if outcome["status"] == "changes" else None

    # -------------------------------------------------------- proposal ---
    def _propose(self, changes: dict, *, executor: str, summary: str, message: str, checks: list[str],
                 scope_ok: bool, tool_message: str = "") -> None:
        changes, page_msg = self._reach_page_limit(changes, executor, message or tool_message)
        if page_msg:
            message = (message + "\n\n" if message else "") + page_msg
        self.set_status("verifying")
        prop = proposals.create(
            self.db, self.project, base_revision=self.task.base_revision, base_files=self.files, changes=changes,
            origin="agent", title=summary or self.task.request[:120], summary=message or tool_message,
            executor=executor, task_id=self.task.id, created_by=self.task.user_id, demo=self.task.demo,
            checks=checks, scope_ok=scope_ok, extra_report={"tool_message": tool_message[:4000]},
        )
        # create() queued a verify job; run verification here instead so the
        # task reports a single, ordered progress stream.
        from ..models import Job
        self.db.query(Job).filter(Job.kind == "verify_proposal", Job.ref_id == prop.id, Job.status == "queued").delete()
        self.db.commit()
        self.task.proposal_id = prop.id
        self.db.commit()
        if prop.status == "failed":
            raise RuntimeError(prop.error)
        self.emit("verify", "compiling candidate and running checks", proposal_id=prop.id)
        proposals.verify(self.db, prop, should_cancel=self.cancelled)
        rep = prop.report or {}
        lines = [f"- {c['check']}: {c['status']}" + (f" ({c['detail']})" if c.get("detail") else "") for c in rep.get("checks", [])]
        files = ", ".join(f"{f['path']} ({f['change']})" for f in prop.files)
        report_text = (f"Files: {files}\nCandidate build: {rep.get('build', {}).get('candidate')} "
                       f"(base build: {rep.get('build', {}).get('base')})\nPDF pages: base {rep.get('build', {}).get('pages_base', 'unknown')} -> candidate {rep.get('build', {}).get('pages_candidate', 'unknown')} (None = not recorded); text lines on the last page: base {rep.get('build', {}).get('last_page_lines_base')} -> candidate {rep.get('build', {}).get('last_page_lines_candidate')}\nChecks:\n" + "\n".join(lines)
                       + f"\nAnalysis: {json.dumps(rep.get('analysis', {}))[:3000]}")
        explanation = ""
        if self.settings.llm_configured:
            try:
                explanation = planner.review(get_provider(), request=self.task.request, summary=summary + "\n" + (message or tool_message)[:2000],
                                             report_text=report_text)
                self.emit("llm", "orchestrator reviewed the verification report")
            except LLMError as e:
                self.emit("llm", f"review step unavailable ({e.kind}); showing the raw report")
        if not explanation:
            explanation = (f"{'[DEMO MODE] ' if self.task.demo else ''}Proposed changes: {files}\n\n"
                           f"Verification:\n{report_text.split('Analysis:')[0]}\n"
                           "Compilation success does not mean the content or layout is correct; review the diff and the candidate PDF.")
        self.say(explanation, kind="proposal", proposal_id=prop.id, executor=executor, demo=self.task.demo)
        self.set_status("needs_review")
