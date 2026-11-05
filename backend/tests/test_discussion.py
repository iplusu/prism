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
    for bad in ({"tools": ["rm"]}, {"rounds": 0}, {"rounds": 6}, {"focus": "x"}, {"focus": "plan"}, {"persona": "x" * 2001}):
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
    llm.script = [{"content": REPORT + "## FINAL DESIGN\nExperiment A: ablation of the clip.\n" + AGREED}]
    tid = _discuss(alice, pid, rounds=1, focus="strategy")  # apply not given
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert not any(m["meta"].get("proposal_id") for m in msgs) and read_text(alice, pid, "main.tex")["text"] == before
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "## FINAL DESIGN" in mod and "IN FULL" in mod and "DECIDED" in mod and "AGREED CHANGES" not in mod
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert "Experiment A" in d["content"]


def test_a_strategy_discussion_can_still_ask_for_a_proposal(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## FINAL DESIGN\nx\n" + AGREED}]
    _discuss(alice, pid, rounds=1, focus="strategy", apply=True)
    drain()
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "AGREED CHANGES" in mod and "never text that reports results" in mod


def test_the_users_own_words_reach_every_tool_and_the_moderator(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## FINAL DESIGN\nx"}]
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


def test_experiment_design_asks_tools_and_moderator_for_full_protocols(alice, llm):
    from app.agent import discussion

    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## EXPERIMENT PROTOCOLS\nE1: arms A/B"}]
    tid = _discuss(alice, pid, rounds=1, focus="experiments")
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert not any(m["meta"].get("proposal_id") for m in msgs)
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    for needle in ("## EXPERIMENT PROTOCOLS", "sample size", "decision rule fixed in advance", "summary table of all experiments", "never invent results"):
        assert needle in mod, needle
    assert "## FINAL DESIGN" not in mod and "AGREED CHANGES" not in mod
    for prompt in (discussion.opening_prompt("experiments", "", "main.tex", "x"), discussion.reply_prompt("experiments", "", "main.tex", "x", {"tool": "codex", "text": "t"}, [], 2, True)):
        assert "protocols" in prompt and "sample size" in prompt
    assert "revised protocols" in discussion.reply_prompt("experiments", "", "main.tex", "x", {"tool": "codex", "text": "t"}, [], 2, True)


def test_a_review_discussion_ends_in_one_decided_conclusion(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + AGREED}]
    tid = _discuss(alice, pid, rounds=2)
    drain()
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    # the moderator must decide the contested points and give prioritised actions, not only list who said what
    for needle in ("## FINAL CONCLUSION", "DECIDED", "[moderator's recommendation]", "priority order", "how it was decided above"):
        assert needle in mod, needle
    # but the moderator's own recommendations never become the edit goal
    assert "never include contested points, points under for your decision, or the moderator's own recommendations" in mod.lower()
    # strategy and experiments keep their own full-design section instead of a second conclusion
    for focus in ("strategy", "experiments"):
        from app.agent import discussion
        m = discussion.moderator_messages(focus, "", [[{"tool": "codex", "label": "Codex", "status": "succeeded", "text": "x"}]], "main.tex", False)[-1]["content"]
        assert "## FINAL CONCLUSION" not in m


def test_a_korean_request_gets_a_korean_version_of_the_report(alice, llm):
    from app.agent import discussion

    stm = [[{"tool": "codex", "label": "Codex", "status": "succeeded", "text": "x"}]]
    ko = discussion.moderator_messages("direction", "4페이지에 맞게 전략 수립", stm, "main.tex", True)[-1]["content"]
    en = discussion.moderator_messages("direction", "fit it into four pages", stm, "main.tex", True)[-1]["content"]
    assert "## 한국어" in ko and "SAME report again in Korean" in ko and "## 한국어" not in en
    # the Korean copy comes before the agreed changes, which are split off and applied
    assert ko.index("## 한국어") < ko.index("AGREED CHANGES")
    assert "최종 설계" in discussion.moderator_messages("strategy", "전략", stm, "main.tex", False)[-1]["content"]
    assert "실험 프로토콜" in discussion.moderator_messages("experiments", "실험", stm, "main.tex", False)[-1]["content"]


def test_korean_can_be_asked_for_explicitly_and_switched_off(alice, llm):
    from app.agent import discussion

    stm = [[{"tool": "codex", "label": "Codex", "status": "succeeded", "text": "x"}]]
    # an English request (e.g. a preset) still gets the Korean version when the option is on
    assert "## 한국어" in discussion.moderator_messages("review", "review it", stm, "main.tex", True, True)[-1]["content"]
    # and an explicit "no" wins over Hangul in the request
    assert "## 한국어" not in discussion.moderator_messages("review", "검토", stm, "main.tex", True, False)[-1]["content"]


def test_reviewer_report_can_also_be_written_in_korean():
    from app.agent import reviewers

    rv = [{"tool": "codex", "status": "succeeded", "text": "x", "error": ""}]
    on = reviewers.synthesis_messages("Reviewer report", {}, rv, "main.tex", True)[-1]["content"]
    off = reviewers.synthesis_messages("검토해 줘", {}, rv, "main.tex", False)[-1]["content"]
    default_en = reviewers.synthesis_messages("Reviewer report", {}, rv, "main.tex")[-1]["content"]
    assert "한국어" in on and "한국어" not in off and "한국어" not in default_en


def test_the_role_is_kept_with_the_result_so_it_can_be_shown(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT}]
    tid = _discuss(alice, pid, apply=False, persona="an ACL area chair")
    drain()
    _, msgs = _msgs(alice, tid)
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert d["meta"]["persona"] == "an ACL area chair"


def test_mentions_are_forwarded_to_the_addressed_tool_and_custom_goal_is_the_request():
    from app.agent import discussion as d
    others = [{"tool": "claude", "text": "Sec 3 is thin.\n@Codex: does Table 2 really use 5 seeds?\nOther line"},
              {"tool": "antigravity", "text": "@Claude Code why drop section 4?"}]
    assert d.addressed_to("codex", others) == ["Claude Code: @Codex: does Table 2 really use 5 seeds?"]
    assert d.addressed_to("claude", others) == ["Antigravity: @Claude Code why drop section 4?"]
    assert d.addressed_to("antigravity", others) == []
    own = {"tool": "codex", "text": "mine"}
    p = d.reply_prompt("custom", "", "main.tex", "Is the ablation convincing?", own, others, 2, True, "codex")
    assert "# Addressed to you" in p and "5 seeds" in p and "@Claude Code" in p.split("# Addressed to you")[0] + "x"
    assert "Is the ablation convincing?" in p and "work on exactly what the user's request" in p
    assert "Addressed to you" not in d.reply_prompt("custom", "", "main.tex", "q", own, others, 2, True, "antigravity")
    assert "@Codex, @Claude Code or @Antigravity" in d.opening_prompt("custom", "", "main.tex", "q")


def test_custom_goal_runs_with_the_users_prompt_and_makes_no_proposal_by_default(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    before = read_text(alice, pid, "main.tex")["text"]
    llm.script = [{"content": REPORT + "## FINAL CONCLUSION\nYes.\n" + AGREED}]
    tid = _discuss(alice, pid, rounds=1, focus="custom")
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert not any(m["meta"].get("proposal_id") for m in msgs) and read_text(alice, pid, "main.tex")["text"] == before
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "work on exactly what the user's request" in mod and "AGREED CHANGES" not in mod


def test_agreement_must_be_earned_and_echoes_are_flagged():
    from app.agent import discussion as d
    base = " ".join(f"word{i}" for i in range(60))
    assert d.echoes(base + " extra", [{"tool": "claude", "text": base}]) == "claude"
    assert d.echoes("completely different " * 40, [{"tool": "claude", "text": base}]) is None
    assert d.echoes("short", [{"tool": "claude", "text": "short"}]) is None
    p = d.reply_prompt("review", "", "main.tex", "", {"tool": "codex", "text": "x"}, [], 2, True, "codex")
    assert "Agreement must be earned" in p and "no addition" in p


def test_the_moderator_gets_a_for_your_decision_section_and_the_flags():
    from app.agent import discussion as d
    st = [{"tool": "codex", "label": "Codex", "text": "a", "round": 1}, {"tool": "claude", "label": "Claude Code", "text": "b", "round": 2, "echo": "codex"}]
    m = d.moderator_messages("review", "q", [st], "main.tex", True, None, "claude")[-1]["content"]
    assert "## FOR YOUR DECISION" in m and "points under FOR YOUR DECISION" in m
    assert "devil's advocate" in m and "Claude Code (round 2) largely repeats Codex" in m


def test_the_devils_advocate_role_reaches_only_the_last_tool(alice, llm, monkeypatch):
    from app.agent import discussion as d
    seen = {}
    orig = d.opening_prompt
    monkeypatch.setattr(d, "opening_prompt", lambda focus, persona, *a: seen.setdefault(len(seen), persona) and orig(focus, persona, *a))
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## FINAL CONCLUSION\nx"}]
    tid = _discuss(alice, pid, tools=["codex", "claude"], focus="custom", rounds=1, adversary=True, persona="a referee")
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    roles = list(seen.values())
    assert roles[0] == "a referee" and "devil's advocate" in roles[1] and roles[1].startswith("a referee")
    assert next(m for m in msgs if m["meta"].get("kind") == "discussion")["meta"]["adversary"] == "claude"
    mod = next(r["messages"][-1]["content"] for r in llm.requests if "### Round 1" in r["messages"][-1]["content"])
    assert "Claude Code was assigned to be the devil's advocate" in mod


def test_up_to_five_rounds_are_accepted_and_three_is_the_default(alice, llm):
    from app.routers.tasks import DiscussIn
    assert DiscussIn().rounds == 3 and DiscussIn(rounds=5).rounds == 5
    with pytest.raises(Exception):
        DiscussIn(rounds=6)
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## FINAL CONCLUSION\nx"}]
    tid = _discuss(alice, pid, rounds=5, focus="custom")
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert 2 <= len(next(m for m in msgs if m["meta"].get("kind") == "discussion")["meta"]["rounds"]) <= 5


def test_a_tool_can_lead_and_write_the_report_instead_of_the_orchestrator_llm(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT}]
    tid = _discuss(alice, pid, tools=["codex", "claude"], lead="antigravity", rounds=3, focus="custom", apply=False)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert d["meta"]["lead"] == "antigravity" and "STATEMENT by Agy" in d["content"] and "forbids_edits=True" in d["content"]
    assert not any("### Round 1" in r["messages"][-1]["content"] for r in llm.requests)  # the LLM did not write the report


def test_when_the_leading_tool_fails_the_orchestrator_llm_writes_the_report(alice, llm):
    _tools()
    register_override("antigravity", FakeAdapter("antigravity", "auth-fail"))
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT + "## FINAL CONCLUSION\nx"}]
    tid = _discuss(alice, pid, tools=["codex", "claude"], lead="antigravity", rounds=3, focus="custom", apply=False)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed", t["error"]
    d = next(m for m in msgs if m["meta"].get("kind") == "discussion")
    assert d["meta"]["lead"] is None and "They agree." in d["content"] and "could not write the report" in d["content"]
    assert any("### Round 1" in r["messages"][-1]["content"] for r in llm.requests)


def test_the_default_leader_is_the_orchestrator_llm(alice, llm):
    _tools()
    pid = new_sample_project(alice)["id"]
    llm.script = [{"content": REPORT}]
    tid = _discuss(alice, pid, tools=["codex", "claude"], rounds=3, focus="custom", apply=False)
    drain()
    t, msgs = _msgs(alice, tid)
    assert t["status"] == "completed" and next(m for m in msgs if m["meta"].get("kind") == "discussion")["meta"]["lead"] is None
