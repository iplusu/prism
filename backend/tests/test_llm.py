"""Orchestrator LLM client, connection test and planner validation.
Uses the scripted FakeLLM test server (not a real model)."""

import json
import socket
import time

import pytest

from app.llm.diagnostics import run_connection_test
from app.llm.provider import LLMError, OpenAICompatibleProvider
from conftest import set_env
from fake_llm import FakeLLM


@pytest.fixture
def fake():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="auto")
    yield f
    f.close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_connection_test_all_steps(fake):
    rep = run_connection_test()
    steps = {s["name"]: s for s in rep["steps"]}
    assert rep["ok"] is True
    for name in ("reachability", "models", "chat", "streaming", "tool_calling", "json_output", "timeout_handling"):
        assert steps[name]["status"] == "passed", (name, steps[name])
    assert rep["models"] == ["test-model"]
    assert rep["context_reported"] == {"test-model": {"max_model_len": 32768}}
    assert rep["capabilities"]["planning_mode"] == "tools"


def test_connection_test_wrong_model_id(fake):
    set_env(LLM_MODEL="qwen-guess")
    rep = run_connection_test()
    steps = {s["name"]: s for s in rep["steps"]}
    assert steps["reachability"]["status"] == "passed"
    assert steps["models"]["status"] == "failed" and "test-model" in steps["models"]["detail"]
    assert rep["ok"] is False


def test_connection_test_without_tool_calling_falls_back_to_json():
    f = FakeLLM(tool_calling=False)
    try:
        set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model")
        rep = run_connection_test()
        steps = {s["name"]: s for s in rep["steps"]}
        assert steps["tool_calling"]["status"] == "failed"
        assert rep["capabilities"]["planning_mode"] == "json"
    finally:
        f.close()


def test_connection_refused_is_reported():
    set_env(LLM_BASE_URL=f"http://127.0.0.1:{_free_port()}/v1", LLM_MODEL="m")
    rep = run_connection_test()
    st = rep["steps"][0]
    assert st["name"] == "reachability" and st["status"] == "failed"
    assert rep["error_kind"] == "connect"


def test_dns_failure_is_reported():
    set_env(LLM_BASE_URL="http://no-such-host.invalid:8000/v1", LLM_MODEL="m")
    rep = run_connection_test()
    assert rep["steps"][0]["status"] == "failed"
    assert rep["error_kind"] in ("dns", "connect")


def test_not_configured():
    set_env(LLM_BASE_URL="", LLM_MODEL="")
    rep = run_connection_test()
    assert rep["steps"][0]["status"] == "failed" and "LLM_BASE_URL" in rep["steps"][0]["detail"]


def test_auth_error(fake):
    fake.status_override = 401
    with pytest.raises(LLMError) as e:
        OpenAICompatibleProvider(fake.url, "test-model").list_models()
    assert e.value.kind == "auth"


def test_read_timeout(fake):
    fake.script = [("sleep", 2)]
    p = OpenAICompatibleProvider(fake.url, "test-model", timeout=0.5)
    t0 = time.monotonic()
    with pytest.raises(LLMError) as e:
        p.chat([{"role": "user", "content": "hi"}])
    assert e.value.kind == "timeout"
    assert time.monotonic() - t0 < 2


def test_streaming(fake):
    p = OpenAICompatibleProvider(fake.url, "test-model")
    assert "".join(p.chat_stream([{"role": "user", "content": "x"}])) == "pong"


def test_api_key_never_in_errors(fake):
    set_env(LLM_API_KEY="sk-supersecretvalue1234567890")
    fake.status_override = 500
    p = OpenAICompatibleProvider(fake.url, "test-model", api_key="sk-supersecretvalue1234567890")
    with pytest.raises(LLMError) as e:
        p.chat([{"role": "user", "content": "x"}])
    assert "supersecret" not in str(e.value)
    assert fake.requests  # the key was sent as a header only
