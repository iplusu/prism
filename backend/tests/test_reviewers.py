"""Reviewer report: parallel read-only reviews by several tools, merged by the orchestrator.
Tools are FAKE (fake_cli.py behaviours) and the LLM is a FAKE scripted server; real-tool runs are checked by hand."""

import time

import pytest

from app.adapters.registry import register_override
from conftest import drain, new_sample_project, read_text, set_env
from fake_adapter import FakeAdapter
from fake_llm import FakeLLM


@pytest.fixture
def llm():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="tools")
    yield f
    f.close()


def _review(c, pid, **review):
    r = c.post(f"/api/projects/{pid}/tasks", json={"request": "Review the method section", "mode": "llm",
                                                   "context": {"scope": "project", "review": review}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _report(c, tid):
    t = c.get(f"/api/tasks/{tid}").json()
    msgs = [m for m in c.get(f"/api/projects/{t['project_id']}/messages").json() if m["task_id"] == tid and m["role"] == "assistant"]
    return t, (msgs[-1] if msgs else None)


def _three():
    for name, label in (("codex", "Codex"), ("claude", "Claude"), ("antigravity", "Agy")):
        register_override(name, FakeAdapter(name, f"review:{label}"))


def test_three_reviewers_run_read_only_and_are_merged(alice, llm):
    _three()
    pid = new_sample_project(alice)["id"]
    before = read_text(alice, pid, "main.tex")
    llm.script = [{"content": "CONSOLIDATED: all three flag sections/method.tex:3. Plan: 1. justify the step size."}]
    tid = _review(alice, pid, persona="PERSONA_X an ACL area chair")
    drain()
    t, m = _report(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert m["content"].startswith("CONSOLIDATED")
    reviews = {r["tool"]: r for r in m["meta"]["reviews"]}
    assert set(reviews) == {"codex", "claude", "antigravity"} and all(r["status"] == "succeeded" for r in reviews.values())
    # the persona reached every tool's prompt, and the prompt forbids edits
    for tool, label in (("codex", "Codex"), ("claude", "Claude"), ("antigravity", "Agy")):
        assert f"REVIEW by {label}" in reviews[tool]["text"]
        assert "PERSONA_X an ACL area chair" in reviews[tool]["text"] and "forbids_edits=True" in reviews[tool]["text"]
    # the synthesis prompt carries all three reviews with attribution
    prompt = llm.requests[-1]["messages"][-1]["content"]
    assert all(f"Review by {n}" in prompt for n in ("Codex", "Claude Code", "Antigravity")) and "persona: PERSONA_X" in prompt
    # the reviewers scribbled on main.tex in their private copies; the project is untouched, and no proposal exists
    assert read_text(alice, pid, "main.tex")["text"] == before["text"]
    assert alice.get(f"/api/projects/{pid}/proposals?status=all").json() == []
    assert len(t["tool_runs"]) == 3


def test_reviewers_run_in_parallel(alice, llm):
    for name in ("codex", "claude", "antigravity"):
        register_override(name, FakeAdapter(name, f"review:{name}"))
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": "merged"}]
    tid = _review(alice, pid)
    t0 = time.monotonic()
    drain()
    t, _ = _report(alice, tid)
    runs = t["tool_runs"]
    starts = sorted(r["started_at"] for r in runs)
    ends = sorted(r["finished_at"] for r in runs)
    assert t["status"] == "completed" and len(runs) == 3
    assert starts[-1] < ends[0], "the reviewers did not overlap in time"  # the last one started before the first finished


def test_one_failing_reviewer_is_reported_not_faked(alice, llm):
    register_override("codex", FakeAdapter("codex", "review:Codex"))
    register_override("claude", FakeAdapter("claude", "auth-fail"))
    register_override("antigravity", FakeAdapter("antigravity", "review:Agy"))
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": "merged of two"}]
    tid = _review(alice, pid)
    drain()
    t, m = _report(alice, tid)
    assert t["status"] == "completed"
    by = {r["tool"]: r for r in m["meta"]["reviews"]}
    assert by["claude"]["status"] == "needs_auth" and by["claude"]["text"] == ""
    assert by["codex"]["status"] == "succeeded" and by["antigravity"]["status"] == "succeeded"
    assert "NO REVIEW (needs_auth" in llm.requests[-1]["messages"][-1]["content"]  # told the model who is missing


def test_tool_choice_and_per_tool_persona(alice, llm):
    _three()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": "merged"}]
    tid = _review(alice, pid, tools=["codex", "antigravity"], persona="GENERIC", personas={"antigravity": "A related-work specialist"})
    drain()
    t, m = _report(alice, tid)
    by = {r["tool"]: r for r in m["meta"]["reviews"]}
    assert set(by) == {"codex", "antigravity"}
    assert "persona=GENERIC" in by["codex"]["text"] and "persona=A related-work specialist" in by["antigravity"]["text"]


def test_no_available_tool_fails_clearly(alice, llm):
    register_override("codex", FakeAdapter("codex", "review:Codex"))
    pid = new_sample_project(alice)["id"]
    # the project policy forbids every tool
    assert alice.put(f"/api/projects/{pid}/policy", json={"allowed_executors": []}).status_code == 200
    tid = _review(alice, pid)
    drain()
    t, _ = _report(alice, tid)
    assert t["status"] == "failed" and "no reviewer tool is available" in t["error"]


def test_without_an_llm_the_reviews_are_shown_unmerged(alice):
    _three()
    set_env(LLM_BASE_URL="", LLM_MODEL="")
    pid = new_sample_project(alice)["id"]
    tid = _review(alice, pid)
    drain()
    t, m = _report(alice, tid)
    assert t["status"] == "completed" and m["meta"]["merged"] is False
    assert "No orchestrator LLM is configured" in m["content"] and "REVIEW by Codex" in m["content"]


def test_review_spec_is_validated(alice):
    pid = new_sample_project(alice)["id"]
    for bad in ({"tools": ["rm"]}, {"persona": "x" * 2001}, {"personas": {"codex": "x" * 2001}}):
        r = alice.post(f"/api/projects/{pid}/tasks", json={"request": "r", "mode": "llm", "context": {"scope": "project", "review": bad}})
        assert r.status_code == 422, bad
