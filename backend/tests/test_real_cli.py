"""REAL CLI checks (not fakes). They use isolated, empty tool homes, so no
credentials are present: they verify installation, version, that every flag
the adapter uses exists in the installed `--help`, that the real binary starts
inside the bwrap sandbox with our argv/stdin, and that a missing login is
reported as `needs_auth` rather than as success."""

import shutil
import uuid

import pytest

from app.adapters.antigravity import AntigravityAdapter
from app.adapters.base import RunSpec
from app.adapters.claude_code import ClaudeCodeAdapter
from app.adapters.codex import CodexAdapter
from conftest import set_env


def _run(adapter, tmp_path, timeout=90):
    work = tmp_path / "work"
    work.mkdir()
    (work / "main.tex").write_text("\\documentclass{article}\\begin{document}Hi\\end{document}\n")
    spec = RunSpec(run_id=uuid.uuid4().hex, workdir=work, run_dir=tmp_path / "run",
                   prompt="Fix the typo in main.tex if any.", timeout_seconds=timeout)
    h = adapter.start(spec)
    events = list(adapter.stream_events(h))
    return adapter.collect_result(h), events, h


@pytest.mark.skipif(not shutil.which("codex"), reason="codex not installed")
def test_codex_real_unauthenticated(tmp_path):
    set_env(CODEX_HOME_DIR=str(tmp_path / "codex-home"), CODEX_OPENAI_API_KEY="")
    a = CodexAdapter()
    h = a.health(force=True)
    assert h["installed"] and h["version"].startswith("codex-cli")
    assert h["status"] == "needs_auth", h
    res, events, handle = _run(a, tmp_path)
    assert handle.isolation == "bwrap"
    assert res.status == "needs_auth", (res, handle.stderr_tail)
    assert not any("execvp" in ln for ln in handle.stderr_tail)  # the real binary actually started


@pytest.mark.skipif(not shutil.which("claude"), reason="claude not installed")
def test_claude_real_unauthenticated(tmp_path):
    set_env(CLAUDE_CONFIG_DIR_FOR_TOOL=str(tmp_path / "claude-config"), CLAUDE_ANTHROPIC_API_KEY="")
    a = ClaudeCodeAdapter()
    h = a.health(force=True)
    assert h["installed"] and "Claude Code" in h["version"]
    assert h["status"] == "needs_auth", h
    res, events, handle = _run(a, tmp_path)
    assert handle.isolation == "bwrap"
    assert res.status == "needs_auth", (res, handle.stderr_tail)


def test_antigravity_not_installed_is_unavailable():
    set_env(AGY_BIN="agy")
    a = AntigravityAdapter()
    if shutil.which("agy"):
        pytest.skip("agy is installed here")
    h = a.health(force=True)
    assert h["status"] == "unavailable" and not h["installed"]
