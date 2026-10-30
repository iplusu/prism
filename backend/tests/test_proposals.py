"""Proposal apply: conditional writes, stale base revisions, 3-way merge,
conflicts, resolution and selective (hunk-level) application."""

from app.db import SessionLocal
from app.models import Project
from app.projects import documents
from app.proposals import service
from conftest import drain, new_sample_project, read_text, save_text


def _propose(pid: str, changes: dict[str, str]) -> str:
    db = SessionLocal()
    try:
        proj = db.get(Project, pid)
        snap = documents.snapshot(pid, message="test base")
        prop = service.create(db, proj, base_revision=snap.revision, base_files=snap.files,
                              changes={k: v.encode() for k, v in changes.items()}, origin="agent", title="test change")
        return prop.id
    finally:
        db.close()


def _replace(c, pid, path, old, new):
    f = read_text(c, pid, path)
    assert old in f["text"]
    r = save_text(c, pid, path, f["text"].replace(old, new, 1), f["sha"])
    assert r.status_code == 200


def _get(c, prop_id):
    return c.get(f"/api/proposals/{prop_id}").json()


def test_apply_on_unchanged_manuscript(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/conclusion.tex")["text"]
    prop_id = _propose(pid, {"sections/conclusion.tex": base.replace("future work", "ongoing work")})
    drain()
    assert _get(alice, prop_id)["status"] == "ready"
    alice.post(f"/api/proposals/{prop_id}/apply")
    drain()
    p = _get(alice, prop_id)
    assert p["status"] == "applied" and p["applied_revision"]
    assert "ongoing work" in read_text(alice, pid, "sections/conclusion.tex")["text"]


def test_cannot_apply_unverified_or_twice(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "main.tex")["text"]
    prop_id = _propose(pid, {"main.tex": base.replace("Research Group Example", "Lab")})
    assert alice.post(f"/api/proposals/{prop_id}/apply").status_code == 409  # still verifying
    drain()
    assert alice.post(f"/api/proposals/{prop_id}/apply").status_code == 200
    drain()
    assert alice.post(f"/api/proposals/{prop_id}/apply").status_code == 409


def test_stale_base_with_concurrent_edit_elsewhere_merges_and_reverifies(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/introduction.tex")["text"]
    prop_id = _propose(pid, {"sections/introduction.tex": base.replace("are a standard tool", "are a widely used tool")})
    drain()
    # a collaborator edits a different paragraph of the same file after the proposal's base revision
    _replace(alice, pid, "sections/introduction.tex", "Our contributions are", "Our two contributions are")
    alice.post(f"/api/proposals/{prop_id}/apply")
    drain()
    p = _get(alice, prop_id)
    assert p["status"] == "superseded" and "merged cleanly" in p["error"]
    child = [x for x in alice.get(f"/api/projects/{pid}/proposals", params={"status": "all"}).json() if x["parent_id"] == prop_id][0]
    assert child["origin"] == "merge"
    assert child["status"] == "applied", child  # re-verified (compiled) then applied
    text = read_text(alice, pid, "sections/introduction.tex")["text"]
    assert "widely used tool" in text and "Our two contributions" in text  # both edits kept


def test_conflicting_edit_is_never_overwritten(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/conclusion.tex")["text"]
    prop_id = _propose(pid, {"sections/conclusion.tex": base.replace("is future work.", "is left for future work.")})
    drain()
    _replace(alice, pid, "sections/conclusion.tex", "is future work.", "is the subject of our next paper.")
    alice.post(f"/api/proposals/{prop_id}/apply")
    drain()
    p = _get(alice, prop_id)
    assert p["status"] == "conflict"
    conf = p["conflicts"][0]
    assert conf["path"] == "sections/conclusion.tex" and "<<<<<<<" in conf["merged"]
    live = read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert "subject of our next paper" in live and "left for future work" not in live
    # resolution with leftover markers is refused
    r = alice.post(f"/api/proposals/{prop_id}/resolve", json={"resolutions": {"sections/conclusion.tex": conf["merged"]}})
    assert r.status_code == 400
    resolved = live.replace("is the subject of our next paper.", "is left for our next paper.")
    r = alice.post(f"/api/proposals/{prop_id}/resolve", json={"resolutions": {"sections/conclusion.tex": resolved}})
    assert r.status_code == 200
    child = r.json()
    assert child["origin"] == "resolution" and child["status"] == "verifying"
    drain()
    assert _get(alice, child["id"])["status"] == "ready"
    alice.post(f"/api/proposals/{child['id']}/apply")
    drain()
    assert _get(alice, child["id"])["status"] == "applied"
    assert "left for our next paper" in read_text(alice, pid, "sections/conclusion.tex")["text"]


def test_selective_hunks_create_reverified_proposal(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/results.tex")["text"]
    new = base.replace("Leukemia  & 1840", "Leukemia  & 1841").replace("News20    & 4105", "News20    & 4106")
    prop_id = _propose(pid, {"sections/results.tex": new})
    drain()
    p = _get(alice, prop_id)
    hunks = p["file_diffs"][0]["hunks"]
    assert len(hunks) == 2
    numbers = p["report"]["analysis"]["numbers"]["sections/results.tex"]
    assert "1841" in numbers["added"] and "1840" in numbers["removed"]
    r = alice.post(f"/api/proposals/{prop_id}/select", json={"selections": {"sections/results.tex": [hunks[1]["id"]]}})
    assert r.status_code == 200
    child = r.json()
    assert child["status"] == "verifying" and child["origin"] == "selection"
    assert _get(alice, prop_id)["status"] == "superseded"
    drain()
    c = _get(alice, child["id"])
    assert c["status"] == "ready" and c["candidate_build_id"]
    alice.post(f"/api/proposals/{child['id']}/apply")
    drain()
    text = read_text(alice, pid, "sections/results.tex")["text"]
    assert "4106" in text and "1840" in text and "1841" not in text


def test_reject(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "main.tex")["text"]
    prop_id = _propose(pid, {"main.tex": base + "% x\n"})
    drain()
    assert alice.post(f"/api/proposals/{prop_id}/reject").status_code == 200
    assert _get(alice, prop_id)["status"] == "rejected"
    assert alice.post(f"/api/proposals/{prop_id}/apply").status_code == 409


def test_failed_candidate_build_is_reported(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/method.tex")["text"]
    prop_id = _propose(pid, {"sections/method.tex": base + "\n\\begin{itemize}\n"})
    drain()
    p = _get(alice, prop_id)
    checks = {c["check"]: c["status"] for c in p["checks"]}
    assert checks["compiles"] == "failed"
    assert p["report"]["build"]["base"] == "succeeded"


def test_proposal_access_is_project_scoped(alice, bob):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "main.tex")["text"]
    prop_id = _propose(pid, {"main.tex": base + "% x\n"})
    assert bob.get(f"/api/proposals/{prop_id}").status_code == 404
    assert bob.post(f"/api/proposals/{prop_id}/apply").status_code == 404


def test_delete_vs_edit_conflict_resolved_by_choice(alice):
    pid = new_sample_project(alice)["id"]
    db = SessionLocal()
    try:
        snap = documents.snapshot(pid, message="base")
        prop_id = service.create(db, db.get(Project, pid), base_revision=snap.revision, base_files=snap.files,
                                 changes={"sections/conclusion.tex": None}, origin="agent", title="drop conclusion").id
    finally:
        db.close()
    drain()
    _replace(alice, pid, "sections/conclusion.tex", "future work", "future research")  # concurrent edit
    alice.post(f"/api/proposals/{prop_id}/apply")
    drain()
    p = _get(alice, prop_id)
    assert p["status"] == "conflict" and p["conflicts"][0]["merged"] is None
    assert alice.post(f"/api/proposals/{prop_id}/resolve", json={}).status_code == 400  # undecided
    r = alice.post(f"/api/proposals/{prop_id}/resolve", json={"choices": {"sections/conclusion.tex": "current"}})
    assert r.status_code == 400 and "identical" in r.json()["detail"]
    # main.tex \input{sections/conclusion} would break; the proposal still gets verified, not blindly applied
    r = alice.post(f"/api/proposals/{prop_id}/resolve", json={"choices": {"sections/conclusion.tex": "proposal"}})
    assert r.status_code == 200
    child = r.json()
    drain()
    c = _get(alice, child["id"])
    assert c["files"][0]["change"] == "deleted"
    assert {x["check"]: x["status"] for x in c["checks"]}["compiles"] == "failed"


def test_moving_equations_between_files_is_not_a_change():
    # splitting a file moves its equations and numbers; only real differences are reported
    from app.proposals.analysis import analyze

    eq = b"x $a+b$ \\begin{equation} t = 1/2 \\label{e} \\end{equation} 3.14"
    base = {"method.tex": b"intro " + eq}
    cand = {"method_a.tex": b"intro ", "method_b.tex": eq}
    rep = analyze(base, cand, ["method.tex", "method_a.tex", "method_b.tex"])
    assert rep["equations"] == {} and rep["numbers"] == {}

    changed = {"method_a.tex": b"intro ", "method_b.tex": eq.replace(b"1/2", b"1/3")}  # one equation really changed
    rep = analyze(base, changed, ["method.tex", "method_a.tex", "method_b.tex"])
    assert rep["equations"]["method.tex"]["removed"] == ["t = 1/2 \\label{e}"]
    assert rep["equations"]["method_b.tex"]["added"] == ["t = 1/3 \\label{e}"]
    assert rep["numbers"]  # 2 -> 3 is reported too
