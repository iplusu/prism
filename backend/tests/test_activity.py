"""Pushes through the API are recorded so the project page can show that they arrived and whether they worked.
A browser's autosaves are not. Real HTTP calls against the app; no tool or LLM."""

from conftest import new_sample_project

BROWSER = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"}


def _up(c, pid, name, text="x", headers=None, **form):
    return c.post(f"/api/projects/{pid}/files/upload", files={"file": (name, text.encode(), "text/plain")}, data=form, headers=headers or {})


def _act(c, pid):
    r = c.get(f"/api/projects/{pid}/activity")
    assert r.status_code == 200, r.text
    return r.json()


def test_writes_by_the_same_api_client_are_one_push(alice):
    pid = new_sample_project(alice)["id"]
    assert _act(alice, pid) == []
    for n in ("a.tex", "b.tex", "c.tex"):
        assert _up(alice, pid, n, directory="pushed").status_code == 200
    a = _act(alice, pid)
    assert len(a) == 1 and a[0]["ok"] is True and a[0]["count"] == 3
    assert a[0]["paths"] == ["pushed/a.tex", "pushed/b.tex", "pushed/c.tex"] and a[0]["client"] == "testclient"


def test_the_browser_is_not_recorded(alice):
    pid = new_sample_project(alice)["id"]
    assert _up(alice, pid, "x.tex", headers=BROWSER).status_code == 200
    r = alice.put(f"/api/projects/{pid}/files/content", json={"path": "main.tex", "text": "% hi\n"}, headers=BROWSER)
    assert r.status_code == 200
    assert _act(alice, pid) == []


def test_a_failed_push_is_recorded_with_the_reason_and_the_client_can_name_itself(alice):
    pid = new_sample_project(alice)["id"]
    H = {"X-Prism-Client": "hermes-sync"}
    assert _up(alice, pid, "new.tex", headers=H).status_code == 200
    r = _up(alice, pid, "new.tex", headers=H, overwrite="false")
    assert r.status_code == 409
    r = alice.put(f"/api/projects/{pid}/files/content", json={"path": "main.tex", "text": "x" * 10, "base_sha": "0" * 64}, headers=H)
    assert r.status_code == 409
    a = _act(alice, pid)
    assert [x["ok"] for x in a] == [False, False, True] and {x["client"] for x in a} == {"hermes-sync"}
    assert "already exists" in a[1]["error"] and a[2]["count"] == 1


def test_create_rename_and_delete_count_as_writes(alice):
    pid = new_sample_project(alice)["id"]
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "n.tex", "text": "a"}).status_code == 200
    assert alice.post(f"/api/projects/{pid}/files/rename", json={"src": "n.tex", "dst": "m.tex"}).status_code == 200
    assert alice.delete(f"/api/projects/{pid}/files", params={"path": "m.tex"}).status_code == 200
    a = _act(alice, pid)
    assert len(a) == 1 and a[0]["count"] == 3 and "n.tex -> m.tex" in a[0]["paths"] and "deleted m.tex" in a[0]["paths"]


def test_other_users_cannot_read_a_projects_activity(alice, bob):
    pid = new_sample_project(alice)["id"]
    _up(alice, pid, "a.tex")
    assert bob.get(f"/api/projects/{pid}/activity").status_code in (403, 404)
