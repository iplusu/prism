"""Missing-package help. The parsing and the API rules run against a FAKE tlmgr (monkeypatched); one
test at the end uses the real TinyTeX and the real TeX Live repository (skipped without network/TeX)."""

import pytest

from app import texpkg
from app.compile.latex import tex_status
from conftest import drain, new_sample_project

SEARCH_OUT = "tlmgr: package repository https://example.invalid (verified)\ntitlesec:\n\ttexmf-dist/tex/latex/titlesec/titlesec.sty\nother:\n\ttexmf-dist/doc/other/xtitlesec.sty\n"


def test_missing_files_from_a_log():
    log = "! LaTeX Error: File `titlesec.sty' not found.\n...\n! LaTeX Error: File `titlesec.sty' not found.\n! LaTeX Error: File `foo.cls' not found.\nFile `local.tex' not found"
    assert texpkg.missing_files(log) == ["titlesec.sty", "foo.cls"]


def test_search_output_matches_the_exact_file_only():
    assert texpkg.parse_search(SEARCH_OUT, "titlesec.sty") == ["titlesec"]  # not xtitlesec.sty


@pytest.mark.parametrize("bad", ["../x.sty", "a b.sty", "x.sty;rm", "x.exe", "/etc/passwd.sty", "x"])
def test_unsafe_file_names_are_refused(bad):
    assert not texpkg.is_safe_file(bad)


def _failing_project(c, pkg_file="zzfake.sty"):
    pid = new_sample_project(c)["id"]
    main = c.get(f"/api/projects/{pid}").json()["main_file"]
    t = c.get(f"/api/projects/{pid}/files/content", params={"path": main}).json()
    c.put(f"/api/projects/{pid}/files/content", json={"path": main, "text": t["text"].replace("\\begin{document}", "\\usepackage{%s}\n\\begin{document}" % pkg_file[:-4], 1), "base_sha": t["sha"]})
    b = c.post(f"/api/projects/{pid}/builds").json()
    drain()
    return pid, c.get(f"/api/builds/{b['id']}").json()


@pytest.mark.skipif(not tex_status()["available"], reason="TeX Live not installed")
def test_api_with_a_fake_tlmgr(alice, monkeypatch, caplog):
    pid, b = _failing_project(alice)
    assert b["status"] == "failed" and b["missing_files"] == ["zzfake.sty"]
    calls = []
    monkeypatch.setattr(texpkg, "install_for", lambda f: calls.append(f) or {"file": f, "package": "zzfake", "installed": True, "note": ""})
    ok = alice.post(f"/api/projects/{pid}/tex-packages", json={"file": "zzfake.sty"})
    assert ok.status_code == 200 and ok.json()["package"] == "zzfake" and calls == ["zzfake.sty"]
    assert any("TeX package install" in r.getMessage() and "zzfake" in r.getMessage() for r in caplog.records)  # audit line
    # a file no build of this project asked for is refused before tlmgr is touched
    assert alice.post(f"/api/projects/{pid}/tex-packages", json={"file": "other.sty"}).status_code == 400
    assert alice.post(f"/api/projects/{pid}/tex-packages", json={"file": "../x.sty"}).status_code == 400
    assert calls == ["zzfake.sty"]


@pytest.mark.skipif(not tex_status()["available"], reason="TeX Live not installed")
def test_viewer_cannot_install(alice, bob, monkeypatch):
    pid, _ = _failing_project(alice)
    alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "viewer"})
    monkeypatch.setattr(texpkg, "install_for", lambda f: pytest.fail("must not run"))
    assert bob.post(f"/api/projects/{pid}/tex-packages", json={"file": "zzfake.sty"}).status_code in (403, 404)


@pytest.mark.skipif(not tex_status()["available"], reason="TeX Live not installed")
def test_disabled_by_setting(alice, monkeypatch):
    from app.config import get_settings

    pid, _ = _failing_project(alice)
    monkeypatch.setattr(get_settings(), "allow_tex_package_install", False)
    assert alice.post(f"/api/projects/{pid}/tex-packages", json={"file": "zzfake.sty"}).status_code == 403
