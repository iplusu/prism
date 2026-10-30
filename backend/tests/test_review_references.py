"""Manuscript checks (real, deterministic) and BibTeX import.

The arXiv / doi.org responses below are TEST FAKES (monkeypatched
`references._get`); the live services are covered by the network test at the
bottom, which is skipped when they are unreachable."""

import httpx
import pytest

from app import references as refs
from app.review import check
from conftest import new_sample_project, read_text, save_text

BIB = """@misc{vaswani2023attention,
      title={Attention Is All You Need},
      author={Ashish Vaswani},
      year={2023},
      eprint={1706.03762},
      archivePrefix={arXiv},
}"""


def _files(**kw) -> dict[str, bytes]:
    return {k.replace("__", "/").replace("_tex", ".tex").replace("_bib", ".bib"): v.encode() for k, v in kw.items()}


def test_check_finds_injected_problems():
    files = _files(
        main_tex="\\input{sec}\\input{gone}\n% \\cite{commented}\n\\bibliography{refs}",
        sec_tex="\\section{A}\\label{sec:a}\\label{sec:a} \\ref{sec:nope} \\cite{k1,missing}\n"
                "\\includegraphics{fig/none} TODO tidy",
        refs_bib="@article{k1, title={T}}\n@article{k1, title={T2}}\n@article{unused, title={U}}",
    )
    got = {(i["kind"]) for i in check(files)["issues"]}
    assert {"duplicate_label", "undefined_ref", "undefined_citation", "missing_graphic", "missing_input",
            "todo", "duplicate_bib_key", "unused_bib_entry", "unreferenced_label"} <= got
    assert not any(i["message"].find("commented") >= 0 for i in check(files)["issues"])  # comments ignored


def test_check_clean_project_and_api(alice, bob):
    pid = new_sample_project(alice)["id"]
    r = alice.get(f"/api/projects/{pid}/review").json()
    assert r["counts"]["error"] == 0 and r["counts"]["warning"] == 0
    f = read_text(alice, pid, "sections/results.tex")
    save_text(alice, pid, "sections/results.tex", f["text"] + "\nSee \\ref{fig:nope}.\n", f["sha"])
    r = alice.get(f"/api/projects/{pid}/review").json()
    bad = [i for i in r["issues"] if i["kind"] == "undefined_ref"]
    assert bad and bad[0]["path"] == "sections/results.tex" and bad[0]["line"]
    assert bob.get(f"/api/projects/{pid}/review").status_code in (403, 404)  # not a member


@pytest.fixture
def fake_services(monkeypatch):
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        if "arxiv.org/bibtex" in url:
            return BIB
        if "doi.org" in url:
            return "@article{LeCun_2015, title={Deep learning}, DOI={10.1038/nature14539}}"
        raise AssertionError(url)

    monkeypatch.setattr(refs, "_get", fake_get)
    return calls


def test_import_adds_dedupes_and_resolves_key_clash(alice, fake_services):
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/references", json={"source": "arxiv", "id": "1706.03762"})
    assert r.status_code == 200 and r.json()["added"] is True
    key, bibfile = r.json()["key"], r.json()["file"]
    assert key in read_text(alice, pid, bibfile)["text"]
    again = alice.post(f"/api/projects/{pid}/references", json={"source": "arxiv", "id": "https://arxiv.org/abs/1706.03762v5"})
    assert again.json() == {"key": key, "file": bibfile, "added": False}  # same eprint: no duplicate
    assert len(fake_services) == 1  # the duplicate never hit the network
    f = read_text(alice, pid, bibfile)
    save_text(alice, pid, bibfile, f["text"] + "\n@article{LeCun_2015, title={Other}}\n", f["sha"])
    r = alice.post(f"/api/projects/{pid}/references", json={"source": "doi", "id": "10.1038/nature14539"})
    assert r.json()["added"] and r.json()["key"] == "LeCun_2015b"  # clash with an unrelated entry
    assert alice.get(f"/api/projects/{pid}/review").json()["counts"]["error"] == 0


def test_import_rejects_bad_ids_and_viewers(alice, bob, fake_services):
    pid = new_sample_project(alice)["id"]
    for bad in ({"source": "arxiv", "id": "../../etc/passwd"}, {"source": "doi", "id": "not-a-doi"},
                {"source": "arxiv", "id": "http://evil.example/x"}):
        assert alice.post(f"/api/projects/{pid}/references", json=bad).status_code == 400
    assert fake_services == []
    assert bob.post(f"/api/projects/{pid}/references", json={"source": "arxiv", "id": "1706.03762"}).status_code in (403, 404)


def test_service_returning_garbage_is_an_error(alice, monkeypatch):
    monkeypatch.setattr(refs, "_get", lambda url, **kw: "<html>rate limited</html>")
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/references", json={"source": "arxiv", "id": "1706.03762"})
    assert r.status_code == 502


def _online() -> bool:
    try:
        return httpx.head("https://export.arxiv.org", timeout=5).status_code < 500
    except httpx.HTTPError:
        return False


def test_rate_limited_service_is_reported_as_503(alice, monkeypatch):
    class R:
        status_code, content = 429, b""

    monkeypatch.setattr(refs.httpx, "get", lambda *a, **k: R())  # FAKE response
    refs._search_cache.clear()
    r = alice.get("/api/references/search", params={"q": "rate limited query"})
    assert r.status_code == 503 and "rate limiting" in r.json()["detail"]


def _live_or_skip(r, what):
    if r.status_code in (502, 503):
        pytest.skip(f"{what} unavailable from this host: {r.json()['detail']}")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.skipif(not _online(), reason="arXiv unreachable")
def test_live_arxiv_search(alice):
    hits = _live_or_skip(alice.get("/api/references/search", params={"q": "attention is all you need"}), "arXiv search")
    assert hits and all(h["source"] == "arxiv" and h["title"] and h["id"] for h in hits)


@pytest.mark.skipif(not _online(), reason="arXiv unreachable")
def test_live_arxiv_bibtex(alice):
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/references", json={"source": "arxiv", "id": "1706.03762"})
    assert "Attention Is All You Need" in _live_or_skip(r, "arXiv BibTeX")["entry"]


@pytest.mark.skipif(not _online(), reason="network unreachable")
def test_live_doi_bibtex(alice):
    pid = new_sample_project(alice)["id"]
    r = alice.post(f"/api/projects/{pid}/references", json={"source": "doi", "id": "10.1038/nature14539"})
    assert "Deep learning" in _live_or_skip(r, "doi.org")["entry"]


ATOM = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2209.15001v3</id>
    <title>Dilated  Neighborhood
      Attention Transformer</title>
    <summary>Transformers are quickly becoming popular.</summary>
    <published>2022-09-29T17:57:08Z</published>
    <arxiv:primary_category term="cs.CV"/>
    <author><name>Ali Hassani</name></author>
    <author><name>Humphrey Shi</name></author>
  </entry>
</feed>"""  # FAKE: same structure as a real export.arxiv.org response


def test_arxiv_atom_parsing_and_cache(alice, monkeypatch):
    calls = []
    # FAKE arXiv: like the real index, nothing matches when stopwords are ANDed in
    monkeypatch.setattr(refs, "_get", lambda url, **kw: calls.append(url) or (
        ATOM if "%22" in url or ("all:is" not in url and "all:all" not in url) else "<feed xmlns='http://www.w3.org/2005/Atom'/>"))
    monkeypatch.setattr(refs, "ARXIV_MIN_INTERVAL", 0)
    refs._search_cache.clear()
    r = alice.get("/api/references/search", params={"q": "neighborhood attention"})
    assert r.status_code == 200
    h = r.json()[0]
    assert h["id"] == "2209.15001" and h["title"] == "Dilated Neighborhood Attention Transformer"
    assert h["authors"] == ["Ali Hassani", "Humphrey Shi"] and h["year"] == "2022" and h["category"] == "cs.CV"
    alice.get("/api/references/search", params={"q": "Neighborhood Attention"})
    assert len(calls) == 1  # second (case-insensitive) identical query served from the cache


def test_search_falls_back_to_content_words_and_sanitizes(alice, monkeypatch):
    urls = []

    def fake(url, **kw):  # FAKE: the exact phrase finds nothing, the content words do
        urls.append(url)
        return ATOM if "%22" not in url else "<feed xmlns='http://www.w3.org/2005/Atom'/>"

    monkeypatch.setattr(refs, "_get", fake)
    monkeypatch.setattr(refs, "ARXIV_MIN_INTERVAL", 0)
    refs._search_cache.clear()
    r = alice.get("/api/references/search", params={"q": 'attention is "all" you need" OR (ti:x)'})
    assert r.status_code == 200 and r.json()
    assert "%22" in urls[0] and "AND" not in urls[0]  # phrase first
    assert "all:attention" in urls[1] and "all:is" not in urls[1] and "all:you" not in urls[1]  # stopwords dropped
    assert not any(c in u for u in urls for c in ('(', ')', ' OR '))  # user-typed operators removed
