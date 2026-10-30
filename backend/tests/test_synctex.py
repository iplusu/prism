"""SyncTeX: real compile in the sandbox, then forward and inverse lookups through the API."""

import pytest

from app.compile.latex import tex_status
from app import synctex
from conftest import drain, new_sample_project, read_text, save_text

pytestmark = pytest.mark.skipif(not tex_status()["available"], reason="LaTeX not installed")


def _built(alice):
    pid = new_sample_project(alice)["id"]
    assert alice.post(f"/api/projects/{pid}/builds").status_code == 200
    drain()
    b = alice.get(f"/api/projects/{pid}/builds").json()[0]
    assert b["status"] == "succeeded", b
    return pid, b["id"]


def test_forward_and_inverse_round_trip(alice):
    pid, bid = _built(alice)
    text = read_text(alice, pid, "main.tex")["text"].splitlines()
    line = next(i + 1 for i, l in enumerate(text) if l.startswith("We study"))
    r = alice.get(f"/api/builds/{bid}/synctex/forward", params={"file": "main.tex", "line": line}).json()
    assert r["boxes"], r
    b = r["boxes"][0]
    assert b["page"] == 1 and 0 < b["x"] < 612 and 0 < b["y"] < 792 and b["w"] > 50
    # clicking inside that rectangle leads back to the same file and a nearby line
    inv = alice.get(f"/api/builds/{bid}/synctex/inverse",
                    params={"page": b["page"], "x": b["x"] + b["w"] / 2, "y": b["y"] + b["h"] / 2}).json()["source"]
    assert inv and inv["path"] == "main.tex" and abs(inv["line"] - line) <= 3, inv


def test_inverse_into_an_included_file_and_blank_area(alice):
    pid, bid = _built(alice)
    f = read_text(alice, pid, "sections/introduction.tex")["text"].splitlines()
    line = next(i + 1 for i, l in enumerate(f) if len(l) > 60)
    r = alice.get(f"/api/builds/{bid}/synctex/forward", params={"file": "sections/introduction.tex", "line": line}).json()
    assert r["boxes"]
    b = r["boxes"][0]
    inv = alice.get(f"/api/builds/{bid}/synctex/inverse",
                    params={"page": b["page"], "x": b["x"] + 5, "y": b["y"] + b["h"] / 2}).json()["source"]
    assert inv and inv["path"] == "sections/introduction.tex"
    blank = alice.get(f"/api/builds/{bid}/synctex/inverse", params={"page": 1, "x": 2, "y": 2}).json()["source"]
    assert blank is None or blank["path"]  # margin: nothing, or a real project file


def test_bad_input_is_rejected_and_members_only(alice, bob):
    pid, bid = _built(alice)
    for bad in ("../x.tex", "/etc/passwd", "a;b.tex", "a b$.tex"):
        r = alice.get(f"/api/builds/{bid}/synctex/forward", params={"file": bad, "line": 1})
        assert r.status_code == 400, (bad, r.status_code)
    assert alice.get(f"/api/builds/{bid}/synctex/inverse", params={"page": 0, "x": 1, "y": 1}).status_code == 400
    assert bob.get(f"/api/builds/{bid}/synctex/forward", params={"file": "main.tex", "line": 1}).status_code in (403, 404)


def test_build_without_synctex_data_is_a_clear_404(alice):
    pid, bid = _built(alice)
    (synctex.build_dir(bid) / "output.synctex.gz").unlink()
    r = alice.get(f"/api/builds/{bid}/synctex/forward", params={"file": "main.tex", "line": 1})
    assert r.status_code == 404 and "compile again" in r.json()["detail"]
