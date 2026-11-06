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


def test_marked_pdf_is_built_from_a_proposal_and_cached(alice, bob):
    import shutil
    import subprocess
    from app.compile.latex import tex_bin_dir
    d = tex_bin_dir()
    if not d or not shutil.which("latexdiff", path=d):
        pytest.skip("latexdiff is not installed")
    pid = new_sample_project(alice)["id"]
    conclusion = read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert "is future work." in conclusion
    prop_id = _propose(pid, {"sections/conclusion.tex": conclusion.replace("is future work.", "is deferred to a ZEBRAFISH study.", 1)})
    drain()
    assert alice.get(f"/api/proposals/{prop_id}/marked-pdf").status_code == 404  # not built yet
    r = alice.post(f"/api/proposals/{prop_id}/marked-pdf")
    assert r.status_code == 200 and r.json()["ok"], r.json()
    assert r.json()["cached"] is False
    pdf = alice.get(f"/api/proposals/{prop_id}/marked-pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf.content); f.flush()
        text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True).stdout.decode()
    assert "ZEBRAFISH" in text and "future work" in text  # the addition and the deleted words are both on the page
    assert alice.post(f"/api/proposals/{prop_id}/marked-pdf").json()["cached"] is True
    assert bob.get(f"/api/proposals/{prop_id}/marked-pdf").status_code in (403, 404)  # not a member of this project


def test_tidy_creates_a_proposal_and_never_writes_the_file(alice):
    import shutil
    from app.compile.latex import tex_bin_dir
    d = tex_bin_dir()
    if not d or not shutil.which("latexindent", path=d):
        pytest.skip("latexindent is not installed")
    pid = new_sample_project(alice)["id"]
    f = read_text(alice, pid, "sections/conclusion.tex")
    messy = "\\begin{itemize}\n\\item one\n\\item two\n\\end{itemize}\n"
    assert save_text(alice, pid, "sections/conclusion.tex", messy, f["sha"]).status_code == 200
    r = alice.post(f"/api/projects/{pid}/tidy", json={"path": "sections/conclusion.tex"})
    assert r.status_code == 200, r.text
    prop = r.json()
    assert prop["origin"] == "tidy" and [x["path"] for x in prop["files"]] == ["sections/conclusion.tex"]
    assert read_text(alice, pid, "sections/conclusion.tex")["text"] == messy  # nothing written before apply
    drain()
    assert _get(alice, prop["id"])["status"] == "ready"
    assert alice.post(f"/api/proposals/{prop['id']}/apply").status_code == 200
    drain()
    tidied = read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert "\\item one" in tidied and "\\item two" in tidied and tidied != messy and tidied.startswith("\\begin{itemize}")
    assert alice.post(f"/api/projects/{pid}/tidy", json={"path": "sections/conclusion.tex"}).status_code == 400  # already tidy
    assert alice.post(f"/api/projects/{pid}/tidy", json={"path": "refs.bib"}).status_code == 400  # not a .tex file


def test_redo_on_the_current_manuscript_queues_the_same_request(alice):
    pid = new_sample_project(alice)["id"]
    base = read_text(alice, pid, "sections/conclusion.tex")["text"]
    prop_id = _propose(pid, {"sections/conclusion.tex": base.replace("is future work.", "is left for future work.")})
    drain()
    # a proposal that is not in conflict cannot be redone
    assert alice.post(f"/api/proposals/{prop_id}/redo").status_code == 409
    _replace(alice, pid, "sections/conclusion.tex", "is future work.", "is the subject of our next paper.")
    alice.post(f"/api/proposals/{prop_id}/apply")
    drain()
    assert _get(alice, prop_id)["status"] == "conflict"
    r = alice.post(f"/api/proposals/{prop_id}/redo")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["project_id"] == pid and t["context_spec"]["scope"] == "project" and t["status"] == "queued"
    assert "REDO ON THE CURRENT MANUSCRIPT" in t["request"] and "Keep every number and equation" in t["request"]
    # nothing was changed: the proposal is still in conflict until the user rejects it or the new proposal replaces it
    assert _get(alice, prop_id)["status"] == "conflict"
    live = read_text(alice, pid, "sections/conclusion.tex")["text"]
    assert "subject of our next paper" in live
    alice.post(f"/api/tasks/{t['id']}/cancel")
    drain()


CV = "\\documentclass{article}\n\\begin{document}\nCurriculum vitae of ZEBRA-CV\n\nApplication of Large Language Models, ongoing.\n\\end{document}\n"


def _with_cv(alice):
    pid = new_sample_project(alice)["id"]
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "cv.tex", "text": CV}).status_code == 200
    return pid


def _candidate_build(c, prop_id):
    return _get(c, prop_id)["candidate_build"]


def test_a_change_to_a_separate_document_is_compiled_and_previewed_as_that_document(alice):
    import subprocess
    import tempfile
    pid = _with_cv(alice)
    prop_id = _propose(pid, {"cv.tex": CV.replace("ongoing.", "in preparation.")})
    drain()
    b = _candidate_build(alice, prop_id)
    assert b["main_file"] == "cv.tex" and b["status"] == "succeeded", b  # not the project's main file
    pdf = alice.get(f"/api/proposals/{prop_id}/pdf")
    assert pdf.status_code == 200
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf.content); f.flush()
        text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True).stdout.decode()
    assert "ZEBRA-CV" in text and "in preparation" in text and "step-size" not in text.lower()


def test_a_broken_separate_document_is_caught_by_the_verification(alice):
    pid = _with_cv(alice)
    prop_id = _propose(pid, {"cv.tex": CV.replace("\\end{document}", "\\undefinedcommandxyz\n\\end{document}")})
    drain()
    b = _candidate_build(alice, prop_id)
    assert b["main_file"] == "cv.tex" and b["status"] == "failed", b  # before, the main file was compiled and the break went unnoticed


def test_changes_that_reach_the_main_file_are_still_compiled_as_the_main_file(alice):
    pid = _with_cv(alice)
    conclusion = read_text(alice, pid, "sections/conclusion.tex")["text"]
    a = _propose(pid, {"sections/conclusion.tex": conclusion.replace("future work", "ongoing work")})
    b = _propose(pid, {"cv.tex": CV.replace("ongoing.", "x."), "sections/conclusion.tex": conclusion.replace("future work", "later work")})
    c = _propose(pid, {"references.bib": read_text(alice, pid, "references.bib")["text"] + "\n"})
    drain()
    for prop_id in (a, b, c):
        assert _candidate_build(alice, prop_id)["main_file"] == "main.tex", prop_id


def test_the_marked_pdf_follows_the_changed_document_and_an_old_cache_is_not_reused(alice):
    import shutil
    import subprocess
    import tempfile
    from app.compile.latex import tex_bin_dir
    d = tex_bin_dir()
    if not d or not shutil.which("latexdiff", path=d):
        pytest.skip("latexdiff is not installed")
    pid = _with_cv(alice)
    prop_id = _propose(pid, {"cv.tex": CV.replace("ongoing.", "in PREPARATION.")})
    drain()
    # a marked PDF cached before the target was recorded is a PDF of the main file: it must not be served for this proposal
    from app.proposals import markup
    from app.models import Proposal
    db = SessionLocal()
    try:
        old = markup.marked_pdf_path(db.get(Proposal, prop_id))
    finally:
        db.close()
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"%PDF-1.4 stale main-file pdf")
    r = alice.post(f"/api/proposals/{prop_id}/marked-pdf")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["cached"] is False and r.json()["target"] == "cv.tex", r.json()
    pdf = alice.get(f"/api/proposals/{prop_id}/marked-pdf").content
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(pdf); f.flush()
        text = subprocess.run(["pdftotext", f.name, "-"], capture_output=True).stdout.decode()
    assert "PREPARATION" in text and "ZEBRA-CV" in text and "step-size" not in text.lower()
    assert alice.post(f"/api/proposals/{prop_id}/marked-pdf").json()["cached"] is True


LETTER = "\\documentclass{article}\n\\begin{document}\nCover letter of QUOKKA-LETTER\n\\end{document}\n"


def _pdf_text(c, url):
    import subprocess
    import tempfile
    r = c.get(url)
    assert r.status_code == 200 and r.content[:5] == b"%PDF-", (url, r.status_code)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(r.content); f.flush()
        return subprocess.run(["pdftotext", f.name, "-"], capture_output=True).stdout.decode()


def test_a_proposal_that_changes_several_documents_gets_one_pdf_for_each(alice):
    pid = _with_cv(alice)
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "letter.tex", "text": LETTER}).status_code == 200
    conclusion = read_text(alice, pid, "sections/conclusion.tex")["text"]
    prop_id = _propose(pid, {"cv.tex": CV.replace("ongoing.", "in preparation."), "letter.tex": LETTER.replace("Cover letter", "Revised cover letter"),
                             "sections/conclusion.tex": conclusion.replace("future work", "later work")})
    drain()
    d = _get(alice, prop_id)
    assert [b["main_file"] for b in d["candidate_builds"]] == ["main.tex", "cv.tex", "letter.tex"], d["candidate_builds"]  # main first: the primary
    assert all(b["status"] == "succeeded" for b in d["candidate_builds"]) and d["candidate_build"]["main_file"] == "main.tex"
    assert [x["file"] for x in d["report"]["build"]["documents"]] == ["main.tex", "cv.tex", "letter.tex"]
    assert "step-size" in _pdf_text(alice, f"/api/proposals/{prop_id}/pdf").lower()  # no ?doc= : the primary document
    assert "QUOKKA-LETTER" in _pdf_text(alice, f"/api/proposals/{prop_id}/pdf?doc=letter.tex")
    cv = _pdf_text(alice, f"/api/proposals/{prop_id}/pdf?doc=cv.tex")
    assert "ZEBRA-CV" in cv and "QUOKKA" not in cv
    assert alice.get(f"/api/proposals/{prop_id}/pdf?doc=nothing.tex").status_code == 404


def test_a_broken_second_document_fails_the_compile_check_of_the_proposal(alice):
    pid = _with_cv(alice)
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "letter.tex", "text": LETTER}).status_code == 200
    prop_id = _propose(pid, {"cv.tex": CV.replace("ongoing.", "x."),
                             "letter.tex": LETTER.replace("\\end{document}", "\\undefinedcommandxyz\n\\end{document}")})
    drain()
    d = _get(alice, prop_id)
    assert d["candidate_builds"][0]["main_file"] == "cv.tex" and d["candidate_builds"][0]["status"] == "succeeded"
    assert d["candidate_builds"][1]["main_file"] == "letter.tex" and d["candidate_builds"][1]["status"] == "failed"
    assert any(c["check"] == "compiles" and c["status"] == "failed" for c in d["report"]["checks"]), d["report"]["checks"]


def test_the_marked_pdf_can_be_built_for_each_document(alice):
    import shutil
    from app.compile.latex import tex_bin_dir
    d = tex_bin_dir()
    if not d or not shutil.which("latexdiff", path=d):
        pytest.skip("latexdiff is not installed")
    pid = _with_cv(alice)
    assert alice.post(f"/api/projects/{pid}/files", json={"path": "letter.tex", "text": LETTER}).status_code == 200
    prop_id = _propose(pid, {"cv.tex": CV.replace("ongoing.", "in PREPARATION."), "letter.tex": LETTER.replace("Cover letter", "REVISED cover letter")})
    drain()
    r = alice.post(f"/api/proposals/{prop_id}/marked-pdf?doc=letter.tex")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["target"] == "letter.tex", r.json()
    r2 = alice.post(f"/api/proposals/{prop_id}/marked-pdf")  # the primary document: cv.tex
    assert r2.json()["ok"] and r2.json()["target"] == "cv.tex"
    assert "REVISED" in _pdf_text(alice, f"/api/proposals/{prop_id}/marked-pdf?doc=letter.tex")
    assert "PREPARATION" in _pdf_text(alice, f"/api/proposals/{prop_id}/marked-pdf")
    assert alice.post(f"/api/proposals/{prop_id}/marked-pdf?doc=main.tex").status_code == 404  # not a document this proposal changes
