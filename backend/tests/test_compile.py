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


def test_project_with_no_files_fails_cleanly_instead_of_hanging(alice):
    """Regression: a snapshot with no files made the worker crash and left the build 'running' forever."""
    pid = new_sample_project(alice)["id"]
    for f in alice.get(f"/api/projects/{pid}/files").json():
        assert alice.delete(f"/api/projects/{pid}/files", params={"path": f["path"]}).status_code in (200, 204)
    b = _compile(alice, pid)
    assert b["status"] == "failed"
    assert "not found" in b["error"]


def test_worker_crash_does_not_leave_the_build_running(alice, monkeypatch):
    import app.worker as worker

    def boom(*a, **k):
        raise RuntimeError("simulated crash before the build finished")

    pid = new_sample_project(alice)["id"]
    monkeypatch.setattr(worker, "run_build", boom)
    b = alice.post(f"/api/projects/{pid}/builds").json()
    drain()
    got = alice.get(f"/api/builds/{b['id']}").json()
    assert got["status"] == "failed" and "simulated crash" in got["error"]


STANDALONE = "\\documentclass{article}\n\\begin{document}\nStandalone %s file.\n\\end{document}\n"


def _compile_file(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/builds", json=body)
    if r.status_code != 200:
        return r
    drain()
    return c.get(f"/api/builds/{r.json()['id']}").json()


def test_any_tex_file_compiles_to_its_own_pdf(alice):
    pid = new_sample_project(alice)["id"]
    for name in ("second.tex", "notes/third.tex"):
        assert alice.post(f"/api/projects/{pid}/files", json={"path": name, "text": STANDALONE % name.split("/")[-1]}).status_code == 200
    for name, stem in (("second.tex", "second"), ("notes/third.tex", "third")):
        b = _compile_file(alice, pid, main_file=name)
        assert b["status"] == "succeeded", b["error"] + b["log"][-1500:]
        assert b["main_file"] == name
        pdf = alice.get(f"/api/builds/{b['id']}/pdf").content
        text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages).replace(" ", "")
        assert f"Standalone{stem}.texfile." in text or f"Standalone{stem}.tex" in text  # its own text, not the main paper's
        assert "AdaptiveStep-SizeControl" not in text
        r = alice.get(f"/api/projects/{pid}/export.pdf")  # the download is named after the compiled file
        assert f'filename="{stem}.pdf"' in r.headers["content-disposition"]
    # the project's main file is unchanged and still compiles to its own PDF
    assert alice.get(f"/api/projects/{pid}").json()["main_file"] != "second.tex"
    assert _compile_file(alice, pid)["main_file"] == alice.get(f"/api/projects/{pid}").json()["main_file"]


def test_compile_target_validation(alice):
    pid = new_sample_project(alice)["id"]
    main = alice.get(f"/api/projects/{pid}").json()["main_file"]
    # a section file has no \documentclass: refused, or compiled as the main file when the client asks for the fallback
    section = "sections/results.tex"
    r = _compile_file(alice, pid, main_file=section)
    assert r.status_code == 400 and "documentclass" in r.json()["detail"]
    b = alice.post(f"/api/projects/{pid}/builds", json={"main_file": section, "fallback_to_main": True}).json()
    assert b["main_file"] == main and b["fell_back_from"] == section
    drain()
    for bad in ("missing.tex", "references.bib", "../etc/passwd.tex", "/abs.tex"):
        assert alice.post(f"/api/projects/{pid}/builds", json={"main_file": bad}).status_code == 400, bad
    assert alice.post(f"/api/projects/{pid}/builds").status_code == 200  # no body: the main file, as before
    drain()
