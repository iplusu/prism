"""Bibliography verification. FAKE service responses test the judging rules; the live tests at the bottom call
the real doi.org / arXiv / Crossref / OpenAlex and are skipped when those are unreachable."""

import httpx
import pytest

from app import bibverify as bv
from conftest import new_sample_project, read_text, save_text

BIB = r"""@article{good,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam},
  doi = {10.48550/arXiv.1706.03762},
}
@article{wrongdoi,
  title={Attention Is All You Need},
  doi="10.1038/nature14539",
}
@misc{arx,
  title={Adam: A Method for Stochastic Optimization},
  eprint={1412.6980}, archivePrefix={arXiv},
}
@misc{ghostarx,
  title={Nonexistent},
  eprint={2999.99999}, archivePrefix={arXiv},
}
@book{invented,
  title={A Totally Invented Method for {Quantum} Gradient Telepathy},
  author={Nobody, N.},
}
@article{bare, author={X}, year=2020}
@string{foo = "bar"}
"""


def _files(text=BIB):
    return {"refs.bib": text.encode()}


def test_parser_reads_fields_with_braces_quotes_and_skips_strings():
    es = bv.parse_entries(_files())
    assert [e["key"] for e in es] == ["good", "wrongdoi", "arx", "ghostarx", "invented", "bare"]
    f = {e["key"]: e["fields"] for e in es}
    assert f["good"]["doi"] == "10.48550/arXiv.1706.03762" and f["wrongdoi"]["doi"] == "10.1038/nature14539"
    assert f["invented"]["title"].startswith("A Totally Invented Method for {Quantum}")
    assert f["bare"]["year"] == "2020" and "title" not in f["bare"]


def test_similarity_ignores_latex_and_case():
    assert bv.similarity("A {Quantum} Method", "a quantum method") == 1.0
    assert bv.similarity("Attention Is All You Need", "Deep learning") < 0.4


@pytest.fixture
def fake(monkeypatch):  # TEST FAKES for every network lookup
    bv._cache.clear()
    calls = {"doi": [], "arxiv": [], "search": []}
    titles = {"10.48550/arxiv.1706.03762": "Attention Is All You Need", "10.1038/nature14539": "Deep learning"}

    def doi(d):
        calls["doi"].append(d)
        t = titles.get(d.lower())
        return {"title": t, "id": f"doi:{d}", "source": "doi.org"} if t else None

    def arxiv(ids):
        calls["arxiv"].append(list(ids))
        return {"1412.6980": {"title": "Adam: A Method for Stochastic Optimization", "id": "arXiv:1412.6980", "source": "arXiv"}}

    monkeypatch.setattr(bv, "lookup_doi", doi)
    monkeypatch.setattr(bv, "lookup_arxiv_batch", arxiv)
    monkeypatch.setattr(bv, "search_title", lambda t, a: calls["search"].append(t) or None)
    return calls


def test_verdicts(fake):
    r = bv.verify(bv.parse_entries(_files()))
    st = {x["key"]: x for x in r["results"]}
    assert st["good"]["status"] == "verified"
    assert st["wrongdoi"]["status"] == "mismatch" and st["wrongdoi"]["found_title"] == "Deep learning"
    assert st["arx"]["status"] == "verified"
    assert st["ghostarx"]["status"] == "not_found"
    assert st["invented"]["status"] == "not_found" and "often missing" in st["invented"]["detail"]
    assert st["bare"]["status"] == "unchecked"
    assert r["counts"] == {"verified": 2, "mismatch": 1, "not_found": 2, "unchecked": 1}
    assert fake["arxiv"] == [["1412.6980", "2999.99999"]]  # ONE batched arXiv request


def test_service_failure_is_unchecked_not_not_found(monkeypatch):
    bv._cache.clear()
    def boom(*a):
        raise bv.ServiceError("doi.org: HTTP 500")
    monkeypatch.setattr(bv, "lookup_doi", boom)
    r = bv.verify(bv.parse_entries(_files()))
    g = next(x for x in r["results"] if x["key"] == "good")
    assert g["status"] == "unchecked" and "HTTP 500" in g["detail"]


def test_results_are_cached(fake):
    bv.verify(bv.parse_entries(_files()))
    n = len(fake["doi"])
    bv.verify(bv.parse_entries(_files()))
    assert len(fake["doi"]) == n


def test_entry_limit_is_reported(fake, monkeypatch):
    monkeypatch.setattr(bv, "MAX_ENTRIES", 2)
    r = bv.verify(bv.parse_entries(_files()))
    assert r["total"] == 6 and r["checked"] == 2 and r["truncated"]


def test_api_roles(alice, bob, fake):
    pid = new_sample_project(alice)["id"]
    f = read_text(alice, pid, "references.bib")
    save_text(alice, pid, "references.bib", f["text"] + "\n" + BIB, f["sha"])
    r = alice.post(f"/api/projects/{pid}/references/verify")
    assert r.status_code == 200 and r.json()["counts"]["mismatch"] == 1
    assert bob.post(f"/api/projects/{pid}/references/verify").status_code in (403, 404)


def _online():
    try:
        return httpx.get("https://api.crossref.org/works?rows=0", timeout=10).status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _online(), reason="Crossref unreachable")
def test_live_verification():
    bv._cache.clear()
    live = BIB + r"""
@article{lecun,
  title={Deep learning}, author={LeCun, Yann and Bengio, Yoshua and Hinton, Geoffrey}, year={2015},
  journal={Nature}}
"""
    r = bv.verify(bv.parse_entries(_files(live)))
    st = {x["key"]: x for x in r["results"]}
    skipped = [k for k, x in st.items() if x["status"] == "unchecked" and "lookup failed" in x["detail"]]
    if skipped:
        pytest.skip(f"a live service failed: {[st[k]['detail'] for k in skipped]}")
    print({k: (x["status"], x["found_title"][:40]) for k, x in st.items()})
    assert st["good"]["status"] == "verified"
    assert st["wrongdoi"]["status"] == "mismatch"
    assert st["arx"]["status"] == "verified"
    assert st["ghostarx"]["status"] == "not_found"
    assert st["invented"]["status"] == "not_found"
    assert st["lecun"]["status"] == "verified"  # found by title alone


def test_a_rate_limited_service_is_retried_once_then_reported_as_unchecked(monkeypatch):
    calls = []

    class R:
        def __init__(self, code, js=None, headers=None):
            self.status_code, self._js, self.headers = code, js, headers or {}
        def json(self):
            return self._js

    seq = [R(429, headers={"Retry-After": "0"}), R(200, js={"ok": 1})]
    monkeypatch.setattr(bv.httpx, "get", lambda *a, **k: calls.append(1) or seq.pop(0))  # FAKE responses
    monkeypatch.setattr(bv.time, "sleep", lambda s: None)
    assert bv._get_json("https://api.crossref.org/x") == {"ok": 1} and len(calls) == 2
    seq[:] = [R(429), R(429)]
    with pytest.raises(bv.ServiceError, match="429"):
        bv._get_json("https://api.crossref.org/x")
