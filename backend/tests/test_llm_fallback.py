"""Backup model server: used only while the primary does not answer. Both servers are FAKE (fake_llm.py); a closed
local port stands for a primary that is down. Nothing here talks to a real model."""

import pytest

from app.llm import provider as prov
from conftest import new_sample_project, set_env
from fake_llm import FakeLLM

DEAD = "http://127.0.0.1:9/v1"  # nothing listens here


@pytest.fixture
def servers():
    primary, backup = FakeLLM(model="primary-model"), FakeLLM(model="backup-model")
    prov.reset_probe()
    yield primary, backup
    primary.close()
    backup.close()
    prov.reset_probe()


def _env(primary_url, backup_url, **kw):
    set_env(LLM_BASE_URL=primary_url, LLM_MODEL="primary-model", LLM_FALLBACK_BASE_URL=backup_url, LLM_FALLBACK_MODEL="backup-model",
            LLM_CONTEXT_CHARS=400000, LLM_FALLBACK_CONTEXT_CHARS=30000, **kw)
    prov.reset_probe()


def test_primary_is_used_while_it_answers(servers):
    primary, backup = servers
    _env(primary.url, backup.url)
    p = prov.get_provider()
    assert p.model == "primary-model" and p.base_url == primary.url.rstrip("/")
    assert not prov.using_fallback() and prov.active_model() == "primary-model" and prov.context_chars() == 400000
    p.chat([{"role": "user", "content": "hi"}])
    assert primary.requests and not backup.requests


def test_backup_takes_over_when_the_primary_is_down_and_has_its_own_budget(servers):
    _, backup = servers
    _env(DEAD, backup.url)
    p = prov.get_provider()
    assert p.model == "backup-model" and p.base_url == backup.url.rstrip("/")
    assert prov.using_fallback() and prov.active_model() == "backup-model" and prov.context_chars() == 30000
    assert p.chat([{"role": "user", "content": "hi"}]).model == "backup-model" and backup.requests
    from app.agent.planner import planning_mode
    assert planning_mode() == "json-prompt"  # the backup's tool-calling support was never tested


def test_primary_comes_back_after_the_probe_expires(servers, monkeypatch):
    primary, backup = servers
    _env(primary.url, backup.url)
    assert not prov.using_fallback()
    primary.status_override = 503  # the primary answers with an error: the probe treats it as down
    prov._probe["at"] -= 1000
    assert prov.using_fallback()
    primary.status_override = None
    assert prov.using_fallback()  # remembered: no probe on every call
    prov._probe["at"] -= 1000
    assert not prov.using_fallback() and prov.get_provider().model == "primary-model"


def test_without_a_backup_nothing_changes(servers):
    set_env(LLM_BASE_URL=DEAD, LLM_MODEL="primary-model", LLM_FALLBACK_BASE_URL="", LLM_FALLBACK_MODEL="")
    prov.reset_probe()
    p = prov.get_provider()
    assert p.model == "primary-model" and not prov.using_fallback()
    assert p.base_url == DEAD and prov.context_chars() == get_ctx()


def get_ctx():
    from app.config import get_settings
    return get_settings().llm_context_chars


def test_only_a_backup_configured_is_usable(servers):
    _, backup = servers
    set_env(LLM_BASE_URL="", LLM_MODEL="", LLM_FALLBACK_BASE_URL=backup.url, LLM_FALLBACK_MODEL="backup-model")
    prov.reset_probe()
    from app.config import get_settings
    assert get_settings().llm_usable and not get_settings().llm_configured
    assert prov.get_provider().model == "backup-model"


def test_completion_uses_the_backup_model_name(alice, servers):
    _, backup = servers
    _env(DEAD, backup.url, LLM_COMPLETION_MODEL="primary-completion")
    backup.script = [{"content": "is hard to tune."}]
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/complete", json={"path": "main.tex", "prefix": "Gradient descent is widely used to train networks, which", "suffix": ""})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "backup-model" and backup.requests[-1]["model"] == "backup-model"


def test_system_status_shows_the_backup_without_its_address(alice, servers):
    _, backup = servers
    _env(DEAD, backup.url)
    from app.routers.system import _llm_config
    c = _llm_config()
    assert c["fallback"] == {"configured": True, "model": "backup-model", "context_chars": 30000, "in_use": True}
    assert backup.url not in str(c["fallback"])
