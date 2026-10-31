import io
import zipfile

from conftest import SAMPLE, login, new_sample_project, read_text, save_text


def test_import_sample_zip_and_detect_main(alice):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in SAMPLE.rglob("*"):
            if p.is_file():
                zf.write(p, "my-paper/" + p.relative_to(SAMPLE).as_posix())
    r = alice.post("/api/projects/import", files={"file": ("paper.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["main_file"] == "main.tex"
    assert d["import"]["stripped_prefix"] == "my-paper/"
    files = {f["path"] for f in alice.get(f"/api/projects/{d['id']}/files").json()}
    assert {"main.tex", "references.bib", "sections/method.tex"} <= files


def test_edit_save_and_reconnect(alice):
    p = new_sample_project(alice)
    pid = p["id"]
    f = read_text(alice, pid, "sections/conclusion.tex")
    new = f["text"] + "\nAdded sentence.\n"
    r = save_text(alice, pid, "sections/conclusion.tex", new, f["sha"])
    assert r.status_code == 200
    # a brand-new session (re-login) sees the saved content
    again = login("alice")
    assert read_text(again, pid, "sections/conclusion.tex")["text"] == new


def test_stale_save_is_rejected(alice):
    pid = new_sample_project(alice)["id"]
    f = read_text(alice, pid, "main.tex")
    assert save_text(alice, pid, "main.tex", f["text"] + "% one\n", f["sha"]).status_code == 200
    # second writer still holds the old sha
    r = save_text(alice, pid, "main.tex", f["text"] + "% two\n", f["sha"])
    assert r.status_code == 409
    assert "% one" in read_text(alice, pid, "main.tex")["text"]


def test_file_ops(alice):
    pid = new_sample_project(alice)["id"]
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "sections/extra.tex", "text": "x"}).status_code == 200
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "sections/extra.tex", "text": "y"}).status_code == 409
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "../x.tex", "text": "y"}).status_code == 400
    assert alice.post(f"/api/projects/{pid}/files/rename", json={"src": "sections/extra.tex", "dst": "sections/more.tex"}).status_code == 200
    r = alice.post(f"/api/projects/{pid}/files/upload", files={"file": ("fig.png", b"\x89PNG\r\n\x1a\nxxxx", "image/png")}, data={"directory": "figures"})
    assert r.status_code == 200 and r.json()["is_text"] is False
    files = {f["path"] for f in alice.get(f"/api/projects/{pid}/files").json()}
    assert "sections/more.tex" in files and "sections/extra.tex" not in files and "figures/fig.png" in files
    assert alice.delete(f"/api/projects/{pid}/files", params={"path": "sections/more.tex"}).status_code == 200
    out = alice.get(f"/api/projects/{pid}/outline").json()
    assert any(s["title"] == "Method" for s in out["sections"])
    assert {b["key"] for b in out["bib"]} == {"tibshirani1996", "beck2009", "barzilai1988"}
    hits = alice.get(f"/api/projects/{pid}/search", params={"q": "Barzilai"}).json()["results"]
    assert any(h["path"] == "sections/method.tex" for h in hits)


def test_project_isolation_between_users(alice, bob):
    pid = new_sample_project(alice, "Alice private")["id"]
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert bob.get(f"/api/projects/{pid}/files/content", params={"path": "main.tex"}).status_code == 404
    assert bob.put(f"/api/projects/{pid}/files/content", json={"path": "main.tex", "text": "x"}).status_code == 404
    assert bob.post(f"/api/projects/{pid}/builds").status_code == 404
    assert bob.get(f"/api/projects/{pid}/export.zip").status_code == 404
    assert all(p["id"] != pid for p in bob.get("/api/projects").json())
    # viewer can read but not write
    assert alice.post(f"/api/projects/{pid}/members", json={"username": "bob", "role": "viewer"}).status_code == 200
    assert bob.get(f"/api/projects/{pid}/files/content", params={"path": "main.tex"}).status_code == 200
    assert bob.put(f"/api/projects/{pid}/files/content", json={"path": "main.tex", "text": "x"}).status_code == 403
    assert bob.post(f"/api/projects/{pid}/tasks", json={"request": "hi"}).status_code == 403
    assert bob.put(f"/api/projects/{pid}/policy", json={}).status_code == 403


def test_csrf_header_required(alice):
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/files", json={"path": "a.tex", "text": "x"}, headers={"X-Prism-CSRF": "0"})
    assert r.status_code == 403


def test_unauthenticated():
    from fastapi.testclient import TestClient

    from app.main import create_app

    c = TestClient(create_app())
    assert c.get("/api/projects").status_code == 401
    assert c.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401


def test_export_zip_contains_files(alice):
    pid = new_sample_project(alice)["id"]
    r = alice.get(f"/api/projects/{pid}/export.zip")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "main.tex" in names and "sections/results.tex" in names and not any(n.startswith(".git") for n in names)


def test_versions_and_restore_creates_proposal(alice):
    pid = new_sample_project(alice)["id"]
    v = alice.post(f"/api/projects/{pid}/versions", json={"label": "submitted"}).json()
    f = read_text(alice, pid, "sections/conclusion.tex")
    save_text(alice, pid, "sections/conclusion.tex", "changed\n", f["sha"])
    vs = alice.get(f"/api/projects/{pid}/versions").json()
    assert any("submitted" in x["labels"] for x in vs)
    d = alice.get(f"/api/projects/{pid}/versions/{v['revision']}/diff").json()
    assert d["files"][0]["path"] == "sections/conclusion.tex"
    prop = alice.post(f"/api/projects/{pid}/versions/{v['revision']}/restore").json()
    assert prop["origin"] == "restore" and prop["status"] == "verifying"
    # the live manuscript is untouched until the restore proposal is applied
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == "changed\n"


def test_upload_refuses_to_replace_only_when_asked_to(alice):
    """The web UI uploads with overwrite=false and asks the user about the 409s; the API default still replaces."""
    from conftest import read_text

    pid = new_sample_project(alice)["id"]
    up = lambda body, **data: alice.post(f"/api/projects/{pid}/files/upload", files={"file": ("conclusion.tex", body, "text/plain")},
                                          data={"directory": "sections", **data})
    before = read_text(alice, pid, "sections/conclusion.tex")["text"]
    r = up(b"NEW A", overwrite="false")
    assert r.status_code == 409 and "already exists" in r.text
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == before  # untouched
    assert alice.post(f"/api/projects/{pid}/files/upload", files={"file": ("brand-new.tex", b"x", "text/plain")},
                      data={"directory": "sections", "overwrite": "false"}).status_code == 200  # a new name is fine
    assert up(b"NEW B", overwrite="true").status_code == 200
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == "NEW B"
    assert up(b"NEW C").status_code == 200  # no flag: replaces, as before
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == "NEW C"

    # the replaced content is in the history (writes alone are not committed), so it can be restored
    from app.projects import store

    log = alice.get(f"/api/projects/{pid}/versions").json()
    snaps = [c for c in log if c.get("message", "").startswith("Before upload replaced sections/conclusion.tex")]
    assert len(snaps) == 2, log  # before NEW B and before NEW C; the refused attempt left nothing
    olds = {store.files_at(pid, c["revision"])["sections/conclusion.tex"].decode() for c in snaps}
    assert olds == {before, "NEW B"}
    assert up(b"NEW C").status_code == 200 and len([c for c in alice.get(f"/api/projects/{pid}/versions").json()
                                                    if c.get("message", "").startswith("Before upload replaced")]) == 2  # same content: nothing to keep
