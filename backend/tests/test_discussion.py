"""Tool discussion: tools read each other's statements over rounds, the orchestrator moderates, and the agreed
changes become a proposal. Tools are FAKE (fake_cli.py 'discuss:' behaviour); the LLM is a FAKE scripted server.
Real-tool runs are checked by hand."""

import pytest

from app.adapters.registry import register_override
from conftest import drain, new_sample_project, read_text, set_env
from fake_adapter import FakeAdapter
from fake_llm import FakeLLM

REPORT = "## SUMMARY\nThey agree.\n## CONSENSUS\nstep size [Codex][Claude Code]\n## DISAGREEMENTS\nnone\n## DROPPED\nnone\n"
AGREED = "## AGREED CHANGES\n1. JUSTIFY_STEP: sections/method.tex clarify the clipped step size"


@pytest.fixture
def llm():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="tools")
    yield f
    f.close()


def _tools(**names):
    for name, label in {"codex": "Codex", "claude": "Claude", "antigravity": "Agy", **names}.items():
        register_override(name, FakeAdapter(name, f"discuss:{label}"))


def _discuss(c, pid, **spec):
    r = c.post(f"/api/projects/{pid}/tasks", json={"request": "Discuss the method section", "mode": "llm",
                                                   "context": {"scope": "project", "discuss": spec}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _msgs(c, tid):
    t = c.get(f"/api/tasks/{tid}").json()
    return t, [m for m in c.get(f"/api/projects/{t['project_id']}/messages").json() if m["task_id"] == tid and m["role"] == "assistant"]


def test_tools_read_each_other_and_the_agreed_changes_become_a_proposal(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    before = read_text(alice, pid, "main.tex")["text"]
    llm.script = [{"content": REPORT + AGREED}]
    tid = _discuss(alice, pid, rounds=2)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "needs_review", t["error"]
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    r1, r2 = d["meta"]["rounds"]
    assert {s["tool"] for s in r1} == {"codex", "claude", "antigravity"} and all(s["status"] == "succeeded" for s in r1)
    # round 1 is independent: nobody was shown another statement; round 2 shows each one the OTHER two, untrusted
    assert all("saw=[]" in s["text"] for s in r1)
    codex2 = next(s for s in r2 if s["tool"] == "codex")["text"]
    assert "Claude Code" in codex2 and "Antigravity" in codex2 and "'Codex'" not in codex2 and "untrusted=True" in codex2
    assert all(s["agree"] is True for s in r2) and "forbids_edits=True" in codex2
    assert "everyone agreed in round 2" in d["content"] or "everyone agreed in round 2" in " ".join(d["meta"]["notes"])
    # the moderator saw both rounds, and only the AGREED section went on as the edit goal
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "### Round 1" in mod and "### Round 2" in mod and "AGREED CHANGES" in mod
    assert "JUSTIFY_STEP" in d["meta"]["agreed"] and "AGREED CHANGES" not in d["content"]
    # the discussants scribbled on main.tex in their private copies; the project is untouched until a proposal is applied
    assert read_text(alice, pid, "main.tex")["text"] == before
    props = alice.get(f"/api/projects/{pid}/proposals?status=all").json()
    assert len(props) == 1 and props[0]["id"] == t["proposal_id"]
    assert any(f["path"] == "sections/method.tex" for f in props[0]["files"])
    assert t["plan_source"] == "discussion" and "goal_has_item=True" in props[0]["summary"] + str(t["tool_runs"][-1]["summary"])


def test_the_discussion_stops_early_only_when_everyone_agrees(alice, llm):
    _tools(antigravity="Stubborn")
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## AGREED CHANGES\nNONE"}]
    tid = _discuss(alice, pid, rounds=3)
    drain()
    t, msgs = _msgs(alice, tid)
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert len(d["meta"]["rounds"]) == 3  # one tool kept disagreeing, so all three rounds ran
    assert d["meta"]["rounds"][2][0]["agree"] is True and next(s for s in d["meta"]["rounds"][2] if s["tool"] == "antigravity")["agree"] is False


def test_nothing_agreed_means_no_proposal(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## AGREED CHANGES\nNONE"}]
    tid = _discuss(alice, pid)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed" and t["proposal_id"] is None
    assert any("No change was agreed" in m["content"] for m in msgs)
    assert alice.get(f"/api/projects/{pid}/proposals?status=all").json() == []


def test_apply_false_never_edits(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT}]
    tid = _discuss(alice, pid, apply=False)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed" and t["proposal_id"] is None
    assert "AGREED CHANGES" not in next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])


def test_a_failing_tool_leaves_and_is_reported(alice, llm):
    _tools()
    register_override("claude", FakeAdapter("claude", "auth-fail"))
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## AGREED CHANGES\nNONE"}]
    tid = _discuss(alice, pid)
    drain()
    t, msgs = _msgs(alice, tid)
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert t["status"] == "completed"
    assert {s["tool"]: s["status"] for s in d["meta"]["rounds"][0]}["claude"] == "needs_auth"
    assert [s["tool"] for s in d["meta"]["rounds"][1]] == ["codex", "antigravity"]
    assert "Claude Code left the discussion in round 1" in d["content"]


def test_one_tool_is_not_a_discussion(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    tid = _discuss(alice, pid, tools=["codex"])
    drain()
    t, _ = _msgs(alice, tid)
    assert t["status"] == "failed" and "at least two" in t["error"]


def test_needs_the_moderator_llm(alice):
    _tools()
    set_env(LLM_BASE_URL="", LLM_MODEL="")
    pid = new_sample_project(alice)["id"]
    tid = _discuss(alice, pid)
    drain()
    t, _ = _msgs(alice, tid)
    assert t["status"] == "failed" and "moderator LLM is not configured" in t["error"]


def test_discuss_spec_is_validated(alice):
    pid = new_sample_project(alice)["id"]
    for bad in ({"tools": ["rm"]}, {"rounds": 0}, {"rounds": 4}, {"focus": "x"}, {"focus": "plan"}, {"persona": "x" * 2001}):
        r = alice.post(f"/api/projects/{pid}/tasks", json={"request": "r", "mode": "llm", "context": {"scope": "project", "discuss": bad}})
        assert r.status_code == 422, bad


def test_the_servers_work_paths_are_not_shown(tmp_path):
    from app.agent.reviewers import scrub_paths
    from app.config import get_settings

    work = get_settings().data_dir / "tasks" / "t1" / "discuss1-x" / "work"
    text = (f"see [main.tex](file://{work}/main.tex#L5) and {work}/sections/a.tex:3 and {get_settings().data_dir}/other")
    out = scrub_paths(text, work)
    assert out == "see [main.tex](main.tex#L5) and sections/a.tex:3 and <data>/other"
    assert str(get_settings().data_dir) not in out


def test_strategy_focus_sets_the_goal_for_tools_and_moderator(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## AGREED CHANGES\nNONE"}]
    tid = _discuss(alice, pid, rounds=1, focus="strategy")
    drain()
    t, msgs = _msgs(alice, tid)
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert d["meta"]["focus"] == "strategy" and t["status"] == "completed", t["error"]
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "overall strategy for this paper" in mod


def test_strategy_makes_no_proposal_by_default_and_asks_for_the_full_plan(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    before = read_text(alice, pid, "main.tex")["text"]
    llm.script = [{"content": REPORT + "## PLAN\nExperiment A: ablation of the clip.\n" + AGREED}]
    tid = _discuss(alice, pid, rounds=1, focus="strategy")  # apply not given
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert not any(m["meta"].get("proposal_id") for m in msgs) and read_text(alice, pid, "main.tex")["text"] == before
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "## PLAN" in mod and "IN FULL" in mod and "AGREED CHANGES" not in mod
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert "Experiment A" in d["content"]


def test_a_strategy_discussion_can_still_ask_for_a_proposal(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## PLAN\nx\n" + AGREED}]
    _discuss(alice, pid, rounds=1, focus="strategy", apply=True)
    drain()
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "AGREED CHANGES" in mod and "never text that reports results" in mod


def test_the_users_own_words_reach_every_tool_and_the_moderator(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## PLAN\nx"}]
    r = alice.post(f"/api/projects/{pid}/tasks", json={"request": "Tool discussion: plan the paper's strategy\n\nDESIGN-THE-EXTRA-EXPERIMENTS", "mode": "llm",
                                                       "context": {"scope": "project", "discuss": {"focus": "strategy", "rounds": 1}}})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    drain()
    t, msgs = _msgs(alice, tid)
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    from app.agent import discussion

    ask = "Tool discussion: plan the paper's strategy\n\nDESIGN-THE-EXTRA-EXPERIMENTS"
    assert "DESIGN-THE-EXTRA-EXPERIMENTS" in discussion.opening_prompt("strategy", "", "main.tex", ask)
    own = {"tool": "codex", "text": "t"}
    assert "DESIGN-THE-EXTRA-EXPERIMENTS" in discussion.reply_prompt("strategy", "", "main.tex", ask, own, [own], 2, True)
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "DESIGN-THE-EXTRA-EXPERIMENTS" in mod
