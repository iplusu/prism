"""Real LaTeX -> PDF builds (TeX Live + latexmk inside bwrap)."""

import os
from pathlib import Path

import pytest
from pypdf import PdfReader
import io

from app.compile.latex import compile_project, sandbox_status, tex_status
from conftest import drain, new_sample_project, read_text, save_text

pytestmark = pytest.mark.skipif(not tex_status()["available"], reason="TeX Live not installed")


def _compile(c, pid):
    b = c.post(f"/api/projects/{pid}/builds").json()
    drain()
    return c.get(f"/api/builds/{b['id']}").json()


def test_real_build_produces_pdf(alice):
    pid = new_sample_project(alice)["id"]
    b = _compile(alice, pid)
    assert b["status"] == "succeeded", b["error"] + b["log"][-2000:]
    assert b["isolation"] == "bwrap"
    pdf = alice.get(f"/api/builds/{b['id']}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf.content))
    text = "".join(p.extract_text() for p in reader.pages).replace(" ", "")  # pypdf splits kerned words
    assert "AdaptiveStep-SizeControl" in text
    assert "Tibshirani" in text  # bibliography was processed by BibTeX
    assert "[?]" not in text  # citations resolved
    st = alice.get(f"/api/projects/{pid}/status").json()
    assert st["pdf_stale"] is False
    assert alice.get(f"/api/projects/{pid}/export.pdf").content == pdf.content


def test_failed_build_keeps_last_success_and_marks_stale(alice):
    pid = new_sample_project(alice)["id"]
    ok = _compile(alice, pid)
    assert ok["status"] == "succeeded"
    f = read_text(alice, pid, "sections/results.tex")
    save_text(alice, pid, "sections/results.tex", f["text"] + "\n\\undefinedmacro{oops}\n", f["sha"])
    st = alice.get(f"/api/projects/{pid}/status").json()
    assert st["pdf_stale"] is True
    bad = _compile(alice, pid)
    assert bad["status"] == "failed"
    errs = [d for d in bad["diagnostics"] if d["severity"] == "error"]
    assert errs and errs[0]["file"] == "sections/results.tex" and errs[0]["line"]
    st = alice.get(f"/api/projects/{pid}/status").json()
    assert st["last_build"]["status"] == "failed"
    assert st["last_success_build"]["id"] == ok["id"]
    assert alice.get(f"/api/builds/{ok['id']}/pdf").status_code == 200
    assert alice.get(f"/api/projects/{pid}/export.pdf").status_code == 200


def _probe(tmp_path, body: str):
    files = {"main.tex": ("\\documentclass{article}\\begin{document}\n" + body + "\n\\end{document}\n").encode()}
    return compile_project(files, tmp_path / "b", "main.tex", "pdflatex")


@pytest.mark.skipif(sandbox_status()["mode"] != "bwrap", reason="bwrap unavailable")
def test_shell_escape_disabled(tmp_path):
    marker = tmp_path / "pwned"
    r = _probe(tmp_path, f"\\immediate\\write18{{touch {marker}}}hello")
    assert not marker.exists()
    assert "runsystem" not in r.log or "disabled" in r.log


@pytest.mark.skipif(sandbox_status()["mode"] != "bwrap", reason="bwrap unavailable")
def test_cannot_read_host_files(tmp_path):
    secret = Path.home() / ".prism-test-secret.tex"
    secret.write_text("TOPSECRETVALUE")
    try:
        r = _probe(tmp_path, f"\\input{{{secret}}}")
        assert "TOPSECRETVALUE" not in r.log
        assert r.status == "failed"
        r2 = _probe(tmp_path, "\\input{/etc/hostname}")
        assert r2.status == "failed"
    finally:
        secret.unlink()


@pytest.mark.skipif(sandbox_status()["mode"] != "bwrap", reason="bwrap unavailable")
def test_project_latexmkrc_is_not_executed(tmp_path):
    marker = tmp_path / "rc-ran"
    files = {
        "main.tex": b"\\documentclass{article}\\begin{document}x\\end{document}\n",
        "latexmkrc": f"system('touch {marker}');\n".encode(),
        ".latexmkrc": f"system('touch {marker}');\n".encode(),
    }
    r = compile_project(files, tmp_path / "b", "main.tex", "pdflatex")
    assert r.status == "succeeded"
    assert not marker.exists()


def test_blocked_without_sandbox(tmp_path, monkeypatch):
    from conftest import set_env

    set_env(COMPILE_SANDBOX="none", ALLOW_UNSANDBOXED_COMPILE="false")
    r = _probe(tmp_path, "x")
    assert r.status == "blocked"
    assert "ALLOW_UNSANDBOXED_COMPILE" in r.error
