"""Files attached to an AI request: saved under attachments/, named in the request, and their text reaches the model."""

import pytest

from app.agent.context import build_context
from conftest import drain, new_sample_project, set_env
from fake_llm import FakeLLM


@pytest.fixture
def llm():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="tools")
    yield f
    f.close()


def _ctx(files, attachments, budget=20000):
    return build_context(files, request="use the attachment", spec={"scope": "project", "attachments": attachments},
                         main_file="main.tex", budget=budget)


def test_attached_text_is_shown_and_a_binary_is_named_not_dumped():
    files = {"main.tex": b"\\documentclass{article}", "attachments/notes.txt": b"reviewer says: cut the intro",
             "attachments/fig.pdf": b"%PDF-1.5\x00\x01\x02" + b"x" * 50}
    c = _ctx(files, ["attachments/notes.txt", "attachments/fig.pdf", "attachments/gone.txt"])
    assert "reviewer says: cut the intro" in c.text
    assert "attachments/fig.pdf" in c.text and "binary" in c.text and "%PDF" not in c.text
    assert "attachments/gone.txt" in c.text and "not found" in c.text
    assert "attachments/notes.txt" in c.included


def test_a_long_attachment_is_cut_and_says_so():
    files = {"main.tex": b"x", "attachments/big.txt": ("y" * 100_000).encode()}
    c = _ctx(files, ["attachments/big.txt"], budget=40000)
    assert "more characters not shown" in c.text and c.text.count("y") <= 10_100


def test_attachment_paths_are_validated(alice):
    pid = new_sample_project(alice)["id"]
    for bad in (["main.tex"], ["attachments/../main.tex"], ["/etc/passwd"], ["attachments/a\\b"], ["attachments/x"] * 11):
        r = alice.post(f"/api/projects/{pid}/tasks", json={"request": "hi", "mode": "llm", "context": {"scope": "project", "attachments": bad}})
        assert r.status_code == 422, bad


def test_the_attachment_text_reaches_the_orchestrator_llm(alice, llm):
    pid = new_sample_project(alice)["id"]
    up = alice.post(f"/api/projects/{pid}/files/upload", files={"file": ("brief.txt", b"MAGIC_ATTACH_TOKEN 42")},
                    data={"directory": "attachments", "overwrite": "false"})
    assert up.status_code == 200, up.text
    again = alice.post(f"/api/projects/{pid}/files/upload", files={"file": ("brief.txt", b"other")},
                       data={"directory": "attachments", "overwrite": "false"})
    assert again.status_code == 409  # never replaced silently
    llm.script = [{"content": "I read it."}]
    r = alice.post(f"/api/projects/{pid}/tasks", json={"request": "what does the attachment say?", "mode": "llm",
                   "context": {"scope": "project", "attachments": ["attachments/brief.txt"]}})
    assert r.status_code == 200, r.text
    drain()
    seen = " ".join(m["content"] for q in llm.requests for m in q["messages"] if isinstance(m.get("content"), str))
    assert "MAGIC_ATTACH_TOKEN 42" in seen
    assert alice.get(f"/api/tasks/{r.json()['id']}").json()["context_spec"]["attachments"] == ["attachments/brief.txt"]
