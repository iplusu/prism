"""Zotero import. FAKE responses test merging, roles and validation; the live tests read a real PUBLIC Zotero group
(no key needed) and are skipped when api.zotero.org is unreachable."""

import logging

import httpx
import pytest

from app import zotero
from conftest import new_sample_project, read_text, save_text

EXPORT = """@article{wood_art_2007,
	title = {The art of doing (geographies of) music},
	doi = {10.1068/d416t},
	author = {Wood, Nichola},
	year = {2007},
}

@article{barzilai_two-point_1988,
	title = {Two-Point Step Size Gradient Methods},
	author = {Barzilai, Jonathan},
	year = {1988},
}

@article{new_one_2020,
	title = {A completely new paper},
	doi = {10.1/new},
	year = {2020},
}

@article{new_one_2020,
	title = {A second paper with a clashing key},
	year = {2021},
}
"""


def test_merge_skips_known_works_and_renames_clashes():
    existing = {"r.bib": b"@article{a,\n title={The Art of Doing (Geographies of) Music},\n}\n@article{new_one_2020, title={Other}}"}
    m = zotero.merge(existing, EXPORT, {"a", "new_one_2020"})
    keys = [e.split("{", 1)[1].split(",", 1)[0] for e in m["entries"]]
    assert m["skipped"] == 1  # the same title as 'a'
    assert keys == ["barzilai_two-point_1988", "new_one_2020b", "new_one_2020c"]
    assert m["renamed"] == [("new_one_2020", "new_one_2020b"), ("new_one_2020", "new_one_2020c")]


def test_validation_before_any_network(monkeypatch):
    monkeypatch.setattr(zotero, "_request", lambda *a, **k: pytest.fail("no request expected"))
    for lib in ("user", "group:abc", "x", "group:1/../2"):
        with pytest.raises(zotero.ZoteroError) as e:
            zotero.fetch_bibtex(lib, None, None)
        assert e.value.status == 400
    with pytest.raises(zotero.ZoteroError):
        zotero.collections("group:5", "bad key!")
    with pytest.raises(zotero.ZoteroError):
        zotero.fetch_bibtex("group:5", "../etc", None)


@pytest.fixture
def fake_zotero(monkeypatch):
    seen = []

    class R:
        def __init__(self, text="", js=None, total=0):
            self.text, self._js, self.headers = text, js, {"Total-Results": str(total)}
        def json(self):
            return self._js

    def req(path, params, key):  # TEST FAKE
        seen.append((path, dict(params or {}), key))
        if path.startswith("/keys/"):
            return R(js={"userID": 77})
        if path == "/users/77/groups":
            return R(js=[{"id": 5, "data": {"name": "Lab"}}])
        if path.endswith("/collections"):
            return R(js=[{"key": "ABCD1234", "data": {"name": "Thesis"}, "meta": {"numItems": 3}}])
        return R(text=EXPORT, total=4)

    monkeypatch.setattr(zotero, "_request", req)
    return seen


def test_api_flow_with_fake_zotero(alice, bob, fake_zotero):
    key = "k" * 24
    libs = alice.post("/api/zotero/libraries", json={"api_key": key}).json()
    assert libs == [{"id": "user", "name": "My library"}, {"id": "group:5", "name": "Lab"}]
    cols = alice.post("/api/zotero/collections", json={"api_key": key, "library": "user"}).json()
    assert cols == [{"key": "ABCD1234", "name": "Thesis", "parent": None, "items": 3}]
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/zotero/import", json={"api_key": key, "library": "user", "collection": "ABCD1234"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["added"] == 3 and d["skipped_existing"] == 1 and d["renamed"]
    assert any(c[0].endswith("/collections/ABCD1234/items/top") and c[1]["format"] == "bibtex" for c in fake_zotero)
    text = read_text(alice, pid, "references.bib")["text"]
    assert "A completely new paper" in text and "@article{new_one_2020b" in text
    again = alice.post(f"/api/projects/{pid}/zotero/import", json={"api_key": key, "library": "user"}).json()
    assert again["added"] == 0 and again["skipped_existing"] == 4  # nothing is imported twice
    assert bob.post(f"/api/projects/{pid}/zotero/import", json={"api_key": key, "library": "user"}).status_code in (403, 404)


def test_errors_are_reported_not_swallowed(alice, monkeypatch):
    def deny(path, params, key):
        raise zotero.ZoteroError("Zotero refused access", 403)
    monkeypatch.setattr(zotero, "_request", deny)
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/zotero/import", json={"library": "group:5"})
    assert r.status_code == 403 and "refused" in r.json()["detail"]
    assert alice.post(f"/api/projects/{pid}/zotero/import", json={"library": "user"}).status_code == 400  # needs a key


def _online():
    try:
        return httpx.get("https://api.zotero.org/groups/100/items/top?limit=1", timeout=10).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _online(), reason="api.zotero.org unreachable or public group 100 unavailable")
def test_live_public_group_import(alice):
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/zotero/import", json={"library": "group:100"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["added"] >= 20 and d["total_in_zotero"] >= 20
    text = read_text(alice, pid, "references.bib")["text"]
    assert "The art of doing (geographies of) music" in text and "@article{wood_art_2007" in text
    assert alice.post(f"/api/projects/{pid}/zotero/import", json={"library": "group:100"}).json()["added"] == 0
    # the sample paper's own entries are untouched
    assert "Barzilai" in text


@pytest.mark.skipif(not _online(), reason="api.zotero.org unreachable")
def test_live_wrong_key_is_a_clear_error_and_is_not_logged(alice, caplog):
    secret = "Zq9" + "x" * 21
    with caplog.at_level(logging.DEBUG):
        r = alice.post("/api/zotero/libraries", json={"api_key": secret})
    assert r.status_code in (403, 404), r.text
    assert secret not in caplog.text and secret not in r.text
