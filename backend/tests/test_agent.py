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


def test_reasoning_model_running_out_of_budget_is_retried_bigger_then_reported_honestly(alice, llm):
    """A reasoning model can spend the whole output budget on hidden thinking and return nothing (finish_reason=length).
    That is retried once with a larger budget; if it still fails the error names the real cause, not 'invalid JSON'."""
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": "", "finish_reason": "length"}, tool("answer", message="Fine after a bigger budget.")]
    tid = _task(alice, pid, "How many sections?")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed", t
    budgets = [r["max_tokens"] for r in llm.requests]
    assert budgets[1] == min(budgets[0] * 3, 16384) and budgets[1] > budgets[0]

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
