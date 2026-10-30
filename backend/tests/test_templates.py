"""Official templates: downloaded from the publishers at project creation,
imported through the hardened ZIP path, then compiled for real in bwrap.
Needs internet access to the publishers; skipped when they are unreachable."""

import httpx
import pytest

from app.compile.latex import tex_status
from app.projects.templates import TEMPLATES
from conftest import drain

pytestmark = pytest.mark.skipif(not tex_status()["available"], reason="TeX Live not installed")


def _online() -> bool:
    try:
        return httpx.head("https://codeload.github.com", timeout=5).status_code < 500
    except httpx.HTTPError:
        return False


def test_catalogue_lists_only_registry_entries(alice):
    ids = [t["id"] for t in alice.get("/api/projects/templates").json()]
    assert ids == [t.id for t in TEMPLATES]
    r = alice.post("/api/projects", json={"name": "x", "template": "https://evil.example/kit.zip"})
    assert r.status_code == 400  # arbitrary URLs are never fetched


@pytest.mark.skipif(not _online(), reason="publishers' servers unreachable")
@pytest.mark.parametrize("tid", [t.id for t in TEMPLATES])
def test_template_creates_compilable_project(alice, tid):
    r = alice.post("/api/projects", json={"name": f"tpl {tid}", "template": tid})
    assert r.status_code == 200, r.text
    p = r.json()
    files = {f["path"] for f in alice.get(f"/api/projects/{p['id']}/files").json()}
    assert p["main_file"] in files
    assert not any(f.startswith((".github/", "tests/")) for f in files)
    b = alice.post(f"/api/projects/{p['id']}/builds").json()
    drain()
    b = alice.get(f"/api/builds/{b['id']}").json()
    assert b["status"] == "succeeded", b["error"] + b["log"][-3000:]
    pdf = alice.get(f"/api/builds/{b['id']}/pdf")
    assert pdf.content.startswith(b"%PDF")
