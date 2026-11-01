"""Orchestrator end to end with a scripted LLM (FakeLLM) and a fake executor
CLI (fake_cli.py) — both test fakes. Real CLI checks live in test_real_cli.py."""

import json
import os
import threading
import time

import pytest

from app.adapters.registry import register_override
from app.db import SessionLocal
from app.models import Task, ToolRun
from conftest import drain, new_sample_project, read_text, set_env
from fake_adapter import FakeAdapter
from fake_llm import FakeLLM


@pytest.fixture
def llm():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="tools")
    yield f
    f.close()


def tool(name, **args):
    return {"tool_calls": [{"name": name, "arguments": json.dumps(args)}]}


def _task(c, pid, request, mode="auto", **ctx):
    r = c.post(f"/api/projects/{pid}/tasks", json={"request": request, "mode": mode, "context": ctx or {"scope": "project"}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _get(c, tid):
    return c.get(f"/api/tasks/{tid}").json()


def test_answer_directly(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("answer", message="The paper has four sections.")]
    tid = _task(alice, pid, "How many sections?")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed" and t["plan_source"] == "llm-tools"
    msgs = alice.get(f"/api/projects/{pid}/messages").json()
    assert msgs[-1]["content"] == "The paper has four sections."
    # project context reached the model
    sent = json.dumps(llm.requests[0]["messages"])
    assert "sections/method.tex" in sent and "barzilai1988" in sent


def test_llm_edit_creates_verified_proposal(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [
        tool("edit", message="Tightened the conclusion.", summary="Tighten conclusion",
             edits=[{"path": "sections/conclusion.tex", "find": "is future work.", "replace": "is left for future work."}]),
        {"content": "I changed one sentence; the build passed and no citations changed."},
    ]
    tid = _task(alice, pid, "Polish the last sentence of the conclusion")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t
    prop = alice.get(f"/api/proposals/{t['proposal_id']}").json()
    assert prop["status"] == "ready"
    assert [f["path"] for f in prop["files"]] == ["sections/conclusion.tex"]
    checks = {c["check"]: c["status"] for c in prop["checks"]}
    assert checks["compiles"] == "passed"
    assert prop["report"]["build"]["candidate_isolation"] == "bwrap"
    assert "No model inspected the rendered PDF" in " ".join(prop["report"]["limitations"])
    # live manuscript untouched until apply
    assert "left for future work" not in read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert alice.post(f"/api/proposals/{prop['id']}/apply").status_code == 200
    drain()
    assert alice.get(f"/api/proposals/{prop['id']}").json()["status"] == "applied"
    assert "left for future work" in read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert _get(alice, tid)["status"] == "completed"


def test_invalid_llm_output_twice_fails_cleanly(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("edit", message="x", edits=[]), {"tool_calls": [{"name": "delegate", "arguments": "{not json"}]}]
    tid = _task(alice, pid, "Do something")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed"
    assert "invalid plan" in t["error"]
    assert any(e["type"] == "llm_invalid" for e in t["events"])


def test_invalid_output_repaired_on_second_try(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [
        tool("edit", message="x", edits=[{"path": "main.tex", "find": "does-not-exist", "replace": "y"}]),
        tool("answer", message="Recovered."),
    ]
    tid = _task(alice, pid, "Do something")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed"
    assert "occurs 0 times" in llm.requests[1]["messages"][-1]["content"]


def test_shell_commands_in_plan_are_rejected(alice, llm):
    pid = new_sample_project(alice)["id"]
    bad = {"goal": "fix", "write_scope": ["**/*.tex"], "command": "rm -rf /"}
    llm.script = [tool("delegate", message="x", delegation=bad), tool("delegate", message="x", delegation=bad)]
    tid = _task(alice, pid, "fix it")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed" and "command" in t["error"]


def test_auto_delegation_to_tool_then_apply(alice, llm):
    register_override("codex", FakeAdapter("codex", "edit-scope"))
    pid = new_sample_project(alice)["id"]
    llm.script = [
        tool("delegate", message="Delegating.", delegation={
            "goal": "Explain why the step size is clipped", "task_kind": "edit_text",
            "write_scope": ["sections/method.tex"], "acceptance_checks": ["compiles", "only_write_scope", "preserve_citations"]}),
        {"content": "The tool edited method.tex; checks passed."},
    ]
    tid = _task(alice, pid, "Explain the clipping in the method section")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t["error"]
    assert t["executor_used"] == "codex"
    c = t["contract"]
    for k in ("task_id", "project_id", "base_revision", "goal", "executor", "read_scope", "write_scope",
              "constraints", "acceptance_checks", "timeout_seconds", "max_attempts"):
        assert k in c
    assert c["timeout_seconds"] <= 60 and c["max_attempts"] == 2  # server-decided budgets
    run = t["tool_runs"][0]
    assert run["status"] == "succeeded" and run["isolation"] == "bwrap"
    prop = alice.get(f"/api/proposals/{t['proposal_id']}").json()
    assert {c["check"]: c["status"] for c in prop["checks"]}["preserve_citations"] == "passed"


def test_out_of_scope_changes_are_rejected(alice, llm):
    register_override("codex", FakeAdapter("codex", "edit-outside"))
    register_override("claude", FakeAdapter("claude", "edit-outside"))
    set_env(AGENT_DEFAULT_ORDER="codex,claude")
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("delegate", message="x", delegation={"goal": "edit method", "write_scope": ["sections/method.tex"]})]
    tid = _task(alice, pid, "edit method")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed"
    runs = t["tool_runs"]
    assert [r["executor"] for r in runs] == ["codex", "claude"]  # max 2 attempts, second tool tried
    assert all(r["status"] == "rejected" for r in runs)
    paths = {v["path"] for v in runs[0]["violations"]}
    assert {"main.tex", "CLAUDE.md"} <= paths
    assert "% sneaky" not in read_text(alice, pid, "main.tex")["text"]
    assert t["proposal_id"] is None


def test_explicit_tool_without_llm_uses_server_contract(alice):
    register_override("codex", FakeAdapter("codex", "edit-scope"))
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "clarify clipping", mode="codex", scope="file", path="sections/method.tex")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t["error"]
    assert t["plan_source"] == "" or t["contract"]["write_scope"] == ["sections/method.tex"]


def test_auto_without_llm_is_refused(alice):
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "anything")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed" and "LLM_BASE_URL" in t["error"]


def test_tool_needs_auth(alice):
    register_override("codex", FakeAdapter("codex", "auth-fail"))
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "x", mode="codex", scope="file", path="sections/method.tex")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed"
    assert t["tool_runs"][0]["status"] == "needs_auth"
    assert len(t["tool_runs"]) == 1  # no pointless retry on auth failure


def test_tool_timeout_kills_process_tree(alice):
    set_env(AGENT_RUN_TIMEOUT_SECONDS="3", AGENT_MAX_ATTEMPTS="1")
    register_override("codex", FakeAdapter("codex", "sleep"))
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "x", mode="codex", scope="project")
    t0 = time.monotonic()
    drain()
    assert time.monotonic() - t0 < 30
    t = _get(alice, tid)
    assert t["tool_runs"][0]["status"] == "timeout"
    assert t["status"] == "failed"
    _assert_no_leftover(t)


def _assert_no_leftover(t):
    """The fake CLI and the grandchild it spawned must both be gone."""
    import subprocess

    time.sleep(0.5)
    for pattern in ("fake_cli.py sleep", "grandchild.pid"):
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        assert r.stdout.strip() == "", f"leftover processes for {pattern!r}: {r.stdout}"


def test_cancel_running_task(alice):
    set_env(AGENT_MAX_ATTEMPTS="1")
    register_override("codex", FakeAdapter("codex", "sleep"))
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "x", mode="codex", scope="project")
    th = threading.Thread(target=drain)
    th.start()
    for _ in range(100):
        t = _get(alice, tid)
        if t["tool_runs"] and t["status"] == "running":
            break
        time.sleep(0.1)
    time.sleep(0.5)
    assert alice.post(f"/api/tasks/{tid}/cancel").status_code == 200
    th.join(timeout=30)
    assert not th.is_alive()
    t = _get(alice, tid)
    assert t["status"] == "cancelled"
    assert t["tool_runs"][0]["status"] == "cancelled"
    _assert_no_leftover(t)
    # retry creates a fresh task with the same request
    r = alice.post(f"/api/tasks/{tid}/retry").json()
    assert r["retry_of"] == tid and r["request"] == "x"


def test_unavailable_tool_reported(alice):
    set_env(CODEX_BIN="definitely-not-installed-xyz")
    pid = new_sample_project(alice)["id"]
    tid = _task(alice, pid, "x", mode="codex", scope="project")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed" and "unavailable" in t["error"]


def test_model_suggested_tool_is_a_preference_not_a_pin(alice, llm):
    # Auto mode: the model names claude, claude fails, the next tool is still tried.
    register_override("codex", FakeAdapter("codex", "edit-scope"))
    register_override("claude", FakeAdapter("claude", "edit-outside"))
    set_env(AGENT_DEFAULT_ORDER="codex,claude")
    pid = new_sample_project(alice)["id"]
    llm.script = [
        tool("delegate", message="Delegating.", delegation={
            "goal": "edit method", "executor": "claude", "write_scope": ["sections/method.tex"]}),
        {"content": "Done."},
    ]
    tid = _task(alice, pid, "edit method")
    drain()
    t = _get(alice, tid)
    assert [r["executor"] for r in t["tool_runs"]] == ["claude", "codex"]
    assert t["tool_runs"][0]["status"] == "rejected"
    assert t["status"] == "needs_review" and t["executor_used"] == "codex", t["error"]


def test_answer_only_presets_can_never_edit_or_delegate(alice, llm):
    """Analysis presets (reviewer report, terminology, missing citations) are forced to answer: the tool list the
    model sees has only `answer`, and a plan that tries to edit anyway is rejected, not applied."""
    pid = new_sample_project(alice)["id"]
    before = read_text(alice, pid, "sections/conclusion.tex")["text"]
    llm.script = [tool("answer", message="Weakness: no ablation (sections/results.tex).")]
    tid = _task(alice, pid, "Review this manuscript.", mode="codex", scope="project", answer_only=True)  # a pinned tool is ignored
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed" and not t["proposal_id"] and not t.get("tool_runs")
    names = [x["function"]["name"] for x in llm.requests[0]["tools"]]
    assert names == ["answer"]
    assert "ONLY" in json.dumps(llm.requests[0]["messages"][0])

    # a model that ignores the instruction and proposes an edit: rejected twice -> task fails, file untouched
    llm.requests.clear()
    llm.script = [tool("edit", message="x", summary="s", edits=[{"path": "sections/conclusion.tex", "find": "is future work.", "replace": "CHANGED"}]),
                  tool("edit", message="x", summary="s", edits=[{"path": "sections/conclusion.tex", "find": "is future work.", "replace": "CHANGED"}])]
    tid = _task(alice, pid, "Review this manuscript.", mode="auto", scope="project", answer_only=True)
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed" and "analysis request" in t["error"] and not t["proposal_id"]
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == before


def test_reasoning_model_running_out_of_budget_is_retried_without_reasoning_then_reported_honestly(alice, llm):
    """A reasoning model can spend the whole output budget on hidden thinking and return nothing (finish_reason=length).
    That is retried once with the model's reasoning switched off; if it still fails the error names the real cause, not 'invalid JSON'."""
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": "", "finish_reason": "length"}, tool("answer", message="Fine without hidden reasoning.")]
    tid = _task(alice, pid, "How many sections?")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed", t
    assert "chat_template_kwargs" not in llm.requests[0]
    assert llm.requests[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert llm.requests[1]["max_tokens"] == llm.requests[0]["max_tokens"]

    llm.requests.clear()
    llm.script = [{"content": "", "finish_reason": "length"}, {"content": "", "finish_reason": "length"}]
    tid = _task(alice, pid, "How many sections?")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "failed" and "output budget" in t["error"] and "hidden reasoning" in t["error"], t["error"]
    assert "Invalid JSON" not in t["error"]


def test_analysis_requests_start_with_a_large_budget(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("answer", message="ok")]
    _task(alice, pid, "Review this manuscript.", scope="project", answer_only=True)
    drain()
    assert llm.requests[0]["max_tokens"] >= 12000


def _user_text(llm, i=-1):
    return next(m["content"] for m in reversed(llm.requests[i]["messages"]) if m["role"] == "user")


def test_planning_sees_the_earlier_conversation(alice, bob, llm):
    """A follow-up like 'check whether the points you raised were fixed' needs the earlier chat: the planner is given
    this user's previous requests and answers, but not the current message twice, not another user's chat, and not
    the standalone analysis presets."""
    pid = new_sample_project(alice)["id"]
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "editor"})
    llm.script = [tool("answer", message="Point A: the abstract is too long. Point B: Table 2 is unreferenced.")]
    _task(alice, pid, "Review the abstract and tables.")
    drain()
    first = _user_text(llm)
    assert "Earlier conversation" not in first  # nothing before the first request

    llm.script = [tool("answer", message="BOB-SECRET-REPLY")]
    r = bob.post(f"/api/projects/{pid}/tasks", json={"request": "BOB-SECRET-REQUEST", "mode": "auto", "context": {"scope": "project"}})
    assert r.status_code == 200, r.text
    drain()

    llm.script = [tool("answer", message="Checked.")]
    _task(alice, pid, "Were the points you raised fixed?")
    drain()
    text = _user_text(llm)
    assert "Earlier conversation" in text
    assert "Review the abstract and tables." in text and "Point B: Table 2 is unreferenced." in text
    assert text.count("Were the points you raised fixed?") == 1  # only as the request, not also as history
    assert "BOB-SECRET" not in text

    llm.script = [tool("answer", message="Terms are consistent.")]
    _task(alice, pid, "Check terminology.", scope="project", answer_only=True)
    drain()
    assert "Earlier conversation" not in _user_text(llm)


def test_history_is_capped(alice, llm):
    from app.agent import orchestrator

    pid = new_sample_project(alice)["id"]
    for i in range(14):
        llm.script = [tool("answer", message=f"reply {i} " + "x" * 5000)]
        _task(alice, pid, f"question {i}")
        drain()
    llm.script = [tool("answer", message="ok")]
    _task(alice, pid, "latest question")
    drain()
    text = _user_text(llm)
    hist = text.split("# Earlier conversation", 1)[1].split("# Request", 1)[0]
    assert len(hist) < orchestrator.HISTORY_TOTAL_CHARS + 600
    assert "question 13" in hist and "question 0" not in hist  # newest kept, oldest dropped
    assert "[shortened]" in hist


def _thinking_off(req):
    return (req.get("chat_template_kwargs") or {}).get("enable_thinking") is False


def test_hidden_reasoning_is_off_first_only_for_plain_work_without_external_tools(alice, llm):
    """llm mode (answer or edit, never an external tool) is planned with reasoning off first; Auto (which may delegate and
    choose tools), an explicitly chosen tool, and analyses keep it on."""
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("answer", message="Four.")]
    _task(alice, pid, "How many sections?", mode="llm")
    drain()
    assert _thinking_off(llm.requests[0])

    for mode, ctx in (("auto", {}), ("claude", {}), ("llm", {"scope": "project", "answer_only": True})):
        llm.requests.clear()
        llm.script = [tool("answer", message="ok")] if mode != "claude" else [tool("delegate", message="", delegation={"goal": "x", "executor": "claude"})]
        _task(alice, pid, "How many sections?", mode=mode, **ctx)
        drain()
        assert llm.requests and not _thinking_off(llm.requests[0]), (mode, ctx)


def test_an_invalid_quick_plan_is_repaired_with_reasoning_on(alice, llm):
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("edit", message="x", edits=[]), tool("answer", message="Fine after repair.")]
    tid = _task(alice, pid, "How many sections?", mode="llm")
    drain()
    assert _get(alice, tid)["status"] == "completed"
    assert _thinking_off(llm.requests[0]) and not _thinking_off(llm.requests[1])


def test_planning_thinking_can_be_forced(alice, llm):
    pid = new_sample_project(alice)["id"]
    set_env(LLM_PLANNING_THINKING="on")
    llm.script = [tool("answer", message="a")]
    _task(alice, pid, "How many sections?", mode="llm")
    drain()
    assert not _thinking_off(llm.requests[0])
    llm.requests.clear()
    set_env(LLM_PLANNING_THINKING="off")
    llm.script = [tool("answer", message="a")]
    _task(alice, pid, "How many sections?", mode="auto")
    drain()
    assert _thinking_off(llm.requests[0])


def test_page_count_is_read_from_the_tex_log_and_never_guessed(tmp_path):
    from app.compile.latex import count_pages
    log = "stuff\nOutput written on main.pdf (12 pages, 345678 bytes).\nTranscript"
    assert count_pages(log, tmp_path / "none.pdf") == 12
    assert count_pages("Output written on main.pdf (1 page, 999 bytes).", tmp_path / "none.pdf") == 1
    assert count_pages("no such line", tmp_path / "missing.pdf") is None  # pdfinfo cannot read it either


def test_context_carries_the_page_note():
    from app.agent.context import build_context
    c = build_context({"main.tex": b"\\section{A}\nx\n"}, request="fit in 8 pages", spec={}, main_file="main.tex",
                      budget=5000, pages="9 pages (latest successful compile, matches the current text).")
    assert "## PDF page count\n9 pages" in c.text


def test_page_limit_is_read_from_the_request():
    from app.agent.pages import target_pages
    assert target_pages("8페이지 이내로 줄여줘") == 8
    assert target_pages("please fit this in 6 pages") == 6
    assert target_pages("shorten the abstract") is None
    assert target_pages("make it 0 pages") is None


def test_overflow_estimate_counts_the_text_beyond_the_limit():
    from app.agent.pages import note, overflow
    lines = [50, 52, 51, 49, 12]  # 5 pages, last one short
    assert overflow(lines, 4) == {"pages": 5, "lines": 12, "full_page_lines": 51, "spare_lines": None}
    assert overflow(lines, 3)["lines"] == 61
    n = note(lines, 4)
    assert "over by 1 page" in n and "12 text lines" in n and "ESTIMATE" in n
    assert "already met" in note(lines, 5) and "39 lines of room" in note(lines, 5)
    assert note(lines, None) == "" and note(None, 4) == ""


def test_real_pdf_gives_lines_per_page(tmp_path):
    from app.compile.latex import compile_project, page_line_counts
    import shutil
    if not shutil.which("pdftotext"):
        import pytest; pytest.skip("pdftotext not installed")
    src = b"\\documentclass{article}\\begin{document}" + b"line\\\\\n" * 10 + b"\\newpage x\\\\ y\\end{document}"
    r = compile_project({"main.tex": src}, tmp_path, "main.tex", "pdflatex")
    if r.status != "succeeded":
        import pytest; pytest.skip("no working TeX here: " + r.error)
    assert r.pages == 2 and r.page_lines == [10, 2] == page_line_counts(r.pdf_path)


def _four_page_project(c):
    pid = c.post("/api/projects", json={"name": "pages", "template": "blank"}).json()["id"]
    src = "\\documentclass{article}\\begin{document}\nAAA\n\\newpage\nBBB\n\\newpage\nCCC\n\\newpage\nDDD\n\\end{document}\n"
    assert c.put(f"/api/projects/{pid}/files/content", json={"path": "main.tex", "text": src}).status_code == 200
    return pid


def _cut(a, b):
    return tool("edit", message="Removed a page break.", summary="Fewer pages",
                edits=[{"path": "main.tex", "find": f"{a}\n\\newpage\n{b}", "replace": f"{a}\n{b}"}])


def _events(c, tid):
    return [e["type"] for e in c.get(f"/api/tasks/{tid}").json()["events"]]


def test_page_limit_is_retried_until_the_compiled_pdf_fits(alice, llm):
    pid = _four_page_project(alice)
    llm.script = [_cut("AAA", "BBB"), _cut("BBB", "CCC"), {"content": "Shortened to two pages."}]
    tid = _task(alice, pid, "2 pages 이내로 줄여줘")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t
    prop = alice.get(f"/api/proposals/{t['proposal_id']}").json()
    b = prop["report"]["build"]
    assert (b["pages_base"], b["pages_candidate"]) == (4, 2)
    kinds = _events(alice, tid)
    assert kinds.count("page_retry") == 1 and "page_limit" in kinds
    follow = next(json.dumps(r["messages"]) for r in llm.requests if "FOLLOW-UP" in json.dumps(r["messages"]))
    assert "compiles to 3 pages but the limit is 2" in follow and "ESTIMATE" in follow


def test_page_limit_not_met_is_reported_not_claimed(alice, llm):
    pid = _four_page_project(alice)
    # first edit helps a little; the retry round answers instead of editing, so nothing more can be done
    llm.script = [_cut("AAA", "BBB"), tool("answer", message="I cannot shorten it more."), {"content": "Still over."}]
    tid = _task(alice, pid, "fit it in 2 pages")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t
    prop = alice.get(f"/api/proposals/{t['proposal_id']}").json()
    assert prop["report"]["build"]["pages_candidate"] == 3
    done = next(e for e in alice.get(f"/api/tasks/{tid}").json()["events"] if e["type"] == "page_limit")
    assert "NOT confirmed met" in done["message"]


def test_page_limit_retries_can_be_turned_off(alice, llm):
    set_env(PAGE_LIMIT_RETRIES="0")
    pid = _four_page_project(alice)
    llm.script = [_cut("AAA", "BBB"), {"content": "One break removed."}]
    tid = _task(alice, pid, "2 pages")
    drain()
    assert "page_check" not in _events(alice, tid)


def test_page_limit_retry_with_an_external_tool_reuses_its_contract(alice, llm):
    register_override("codex", FakeAdapter("codex", "drop-pagebreak"))
    pid = _four_page_project(alice)
    llm.script = [
        tool("delegate", message="Delegating.", delegation={
            "goal": "Fit the paper in 2 pages", "task_kind": "edit_text", "write_scope": ["main.tex"],
            "acceptance_checks": ["compiles", "only_write_scope"]}),
        {"content": "The tool shortened it."},
    ]
    tid = _task(alice, pid, "2 pages 이내로 줄여줘")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "needs_review", t["error"]
    assert [r["attempt"] for r in t["tool_runs"]] == [1, 101]  # 4 -> 3 pages, then the retry 3 -> 2
    assert alice.get(f"/api/proposals/{t['proposal_id']}").json()["report"]["build"]["pages_candidate"] == 2
    assert _events(alice, tid).count("page_retry") == 1
