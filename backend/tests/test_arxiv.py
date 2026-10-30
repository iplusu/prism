"""arXiv preflight (rules from arXiv's help pages) and the source package, including a real compile."""

import io
import zipfile

import pytest

from app import arxiv
from app.compile.latex import tex_status
from conftest import drain, new_sample_project, read_text, save_text

MAIN = r"""\documentclass{article}
\usepackage{graphicx}
\usepackage{minted}
\begin{document}
\includegraphics{/home/me/fig.pdf}
\includegraphics{plots/fig 1.png}
\includegraphics{fig.eps}
% \includegraphics{/commented/out.pdf}
\include{sections/intro}
\pdfoutput=1
\bibliography{refs}
\end{document}
"""


def _kinds(r):
    return {i["kind"] for i in r["issues"]}


def test_documented_pitfalls_are_found():
    files = {"main.tex": MAIN.encode(), "sections/intro.tex": b"x", ".hidden/a.tex": b"x", "main.aux": b"x",
             "my fig.png": b"x", "psfig.sty": b"x", "refs.bib": b"@a{k,}", "evil.pdf": b"%PDF /JavaScript (app.alert)"}
    r = arxiv.check(files, "main.tex", "pdflatex", "pdfTeX 3.14")
    assert {"absolute_path", "filename", "eps_figure", "include_subdir", "minted", "pdfoutput", "hidden_file",
            "aux_file", "psfig", "javascript", "bbl_missing", "tex_version"} <= _kinds(r)
    assert not any("commented" in i["message"] for i in r["issues"])  # comments ignored
    assert next(i for i in r["issues"] if i["kind"] == "absolute_path")["line"] == 5


def test_engine_and_bbl_rules():
    r = arxiv.check({"main.tex": b"\\bibliography{r}", "paper.bbl": b"x"}, "main.tex", "xelatex")
    assert {"engine", "bbl_name"} <= _kinds(r)
    ok = arxiv.check({"main.tex": b"\\bibliography{r}", "main.bbl": b"x"}, "main.tex", "pdflatex")
    assert not ({"bbl_name", "bbl_missing"} & _kinds(ok))
    old = arxiv.check({"main.tex": b"\\bibliography{r}", "main.bbl": b"% biblatex bbl format version 3.2 $"}, "main.tex", "pdflatex")
    assert "bbl_version" in _kinds(old)
    assert not {"eps_figure"} & _kinds(arxiv.check({"main.tex": b"\\includegraphics{a.eps}"}, "main.tex", "latex"))


def test_source_zip_content():
    files = {"main.tex": b"t", "main.aux": b"x", ".git/x": b"x", "main.pdf": b"x", "fig.pdf": b"f", "main.bbl": b"old"}
    z = zipfile.ZipFile(io.BytesIO(arxiv.source_zip(files, "main.tex", b"NEWBBL")))
    assert sorted(z.namelist()) == ["fig.pdf", "main.bbl", "main.tex"] and z.read("main.bbl") == b"NEWBBL"


def test_api_check_and_export_requires_a_matching_build(alice, bob):
    pid = new_sample_project(alice)["id"]
    r = alice.get(f"/api/projects/{pid}/arxiv-check").json()
    assert r["counts"]["error"] == 0 and "tex_version" in {i["kind"] for i in r["issues"]}
    assert bob.get(f"/api/projects/{pid}/arxiv-check").status_code in (403, 404)
    x = alice.get(f"/api/projects/{pid}/export-arxiv.zip")
    assert x.status_code == 409 and "Compile the paper first" in x.json()["detail"]


@pytest.mark.skipif(not tex_status()["available"], reason="LaTeX not installed")
def test_export_after_a_real_compile_contains_the_real_bbl(alice):
    pid = new_sample_project(alice)["id"]
    assert alice.post(f"/api/projects/{pid}/builds").status_code == 200
    drain()
    assert alice.get(f"/api/projects/{pid}/builds").json()[0]["status"] == "succeeded"
    x = alice.get(f"/api/projects/{pid}/export-arxiv.zip")
    assert x.status_code == 200, x.text
    z = zipfile.ZipFile(io.BytesIO(x.content))
    names = z.namelist()
    assert "main.bbl" in names and "main.tex" in names and not any(n.endswith((".aux", ".log")) for n in names)
    bbl = z.read("main.bbl").decode()
    assert "\\bibitem" in bbl and "Barzilai" in bbl  # produced by BibTeX from the sample's references.bib
    # editing makes the build stale: the export refuses instead of shipping a .bbl for other text
    f = read_text(alice, pid, "main.tex")
    save_text(alice, pid, "main.tex", f["text"] + "\n% edited\n", f["sha"])
    assert alice.get(f"/api/projects/{pid}/export-arxiv.zip").status_code == 409
    assert alice.get(f"/api/projects/{pid}/arxiv-check").json()["counts"]["error"] == 0
