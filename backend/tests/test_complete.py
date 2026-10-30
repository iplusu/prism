"""Inline AI completion endpoint.

FAKE provider tests check the contract (roles, limits, cleaning of model output). The real-model test at the
bottom needs PRISM_TEST_LLM_URL and PRISM_TEST_LLM_MODEL (the orchestrator LLM) and is skipped without them."""

import os

import pytest

from app.llm.provider import ChatResult, LLMError, OpenAICompatibleProvider
from app.routers import complete as comp
from conftest import new_sample_project

PRE = "Gradient descent is widely used to train deep networks. However, its convergence depends on the step size, which"


class FakeProvider:  # TEST FAKE
    def __init__(self, content="is hard to tune.", error=None):
        self.content, self.error, self.calls = content, error, []

    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        if self.error:
            raise self.error
        return ChatResult(content=self.content, model="fake", latency_seconds=0.01)


def _post(c, pid, prefix=PRE, suffix="", path="main.tex"):
    return c.post(f"/api/projects/{pid}/complete", json={"path": path, "prefix": prefix, "suffix": suffix})


def test_clean_completion_strips_model_wrapping():
    assert comp.clean_completion("<think>hmm</think> is hard.", PRE) == " is hard."
    assert comp.clean_completion("```latex\nis hard.\n```", PRE) == "is hard."
    assert comp.clean_completion("step size, which is hard.", PRE) == " is hard."  # repeated prefix removed
    assert comp.clean_completion("   ", PRE) == ""


def test_complete_returns_text_and_sends_bounded_context(alice, monkeypatch):
    fp = FakeProvider()
    monkeypatch.setattr(comp, "get_provider", lambda: fp)
    pid = new_sample_project(alice)["id"]
    r = _post(alice, pid, prefix="x" * 5000 + PRE, suffix="y" * 2000)
    assert r.status_code == 200 and r.json()["completion"] == "is hard to tune."
    messages, kw = fp.calls[0]
    assert len(messages[1]["content"]) < comp.MAX_PREFIX + comp.MAX_SUFFIX + 200
    assert kw["max_tokens"] == comp.MAX_TOKENS and kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False


def test_complete_needs_editor_role_and_text(alice, bob, monkeypatch):
    fp = FakeProvider()
    monkeypatch.setattr(comp, "get_provider", lambda: fp)
    pid = new_sample_project(alice)["id"]
    assert _post(bob, pid).status_code in (403, 404)
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "viewer"})
    assert _post(bob, pid).status_code == 403  # viewers cannot edit, so no completion
    assert _post(alice, pid, prefix=" a").json()["completion"] == "" and not fp.calls  # nothing to continue


def test_complete_reports_llm_failures_honestly(alice, monkeypatch):
    pid = new_sample_project(alice)["id"]
    monkeypatch.setattr(comp, "get_provider", lambda: FakeProvider(error=LLMError("timeout", "too slow")))
    assert _post(alice, pid).status_code == 504
    monkeypatch.setattr(comp, "get_provider", lambda: FakeProvider(error=LLMError("connect", "refused")))
    assert _post(alice, pid).status_code == 502
    r = _post(alice, pid)  # the per-user slot is released after failures
    assert r.status_code == 502


def test_complete_without_configured_llm_is_503(alice):
    pid = new_sample_project(alice)["id"]
    r = _post(alice, pid)
    assert r.status_code == 503 and "unavailable" in r.json()["detail"]


def test_one_completion_at_a_time_per_user(alice, monkeypatch):
    monkeypatch.setattr(comp, "get_provider", lambda: FakeProvider())
    pid = new_sample_project(alice)["id"]
    uid = alice.get("/api/auth/me").json()["id"]
    comp._busy.add(uid)
    try:
        assert _post(alice, pid).status_code == 429
    finally:
        comp._busy.discard(uid)
    assert _post(alice, pid).status_code == 200


@pytest.mark.skipif(not (os.environ.get("PRISM_TEST_LLM_URL") and os.environ.get("PRISM_TEST_LLM_MODEL")),
                    reason="set PRISM_TEST_LLM_URL / PRISM_TEST_LLM_MODEL to test against the real orchestrator LLM")
def test_real_llm_completion(alice, monkeypatch):
    p = OpenAICompatibleProvider(os.environ["PRISM_TEST_LLM_URL"], os.environ["PRISM_TEST_LLM_MODEL"], timeout=60)
    monkeypatch.setattr(comp, "get_provider", lambda: p)
    pid = new_sample_project(alice)["id"]
    r = _post(alice, pid, suffix="\n\\end{document}")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["completion"].strip() and "<think>" not in out["completion"] and "```" not in out["completion"]
    print("real completion:", repr(out["completion"]), out["latency_seconds"], "s")
