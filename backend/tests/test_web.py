"""Web access for the orchestrator. Network calls are replaced by test fakes (monkeypatched app.web functions);
the address checks and HTML/search parsing run for real. A real search is not part of this file."""

import json

import pytest

from app import web
from conftest import drain, new_sample_project, set_env
from fake_llm import FakeLLM
from test_agent import _events, _get, _task, tool


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/", "http://10.0.0.5/x", "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data",
    "http://100.64.1.2/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "file:///etc/passwd", "ftp://93.184.216.34/",
    "http://user:pw@93.184.216.34/", "http://93.184.216.34:8000/"])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(web.WebError):
        web.check_url(url)


def test_a_public_address_is_allowed():
    assert web.check_url("https://93.184.216.34/page") == "https://93.184.216.34/page"


def test_html_is_reduced_to_text():
    t = web.html_to_text("<html><head><title>x</title><style>p{}</style></head><body><nav>menu</nav>"
                         "<h1>Guidelines</h1><p>Manuscripts must not exceed <b>30</b> pages.</p><script>evil()</script></body></html>")
    assert "Guidelines" in t and "not exceed 30 pages." in t and "evil" not in t and "menu" not in t


def test_duckduckgo_results_are_parsed(monkeypatch):
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa%3Fx%3D1&amp;rut=z">First <b>hit</b></a>'
            '<a class="result__snippet" href="#">About the <b>limit</b></a>'
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fb&amp;rut=y">Second</a>')

    class R:
        status_code, text = 200, html
    monkeypatch.setattr(web, "_get", lambda *a, **k: R())
    rows = web.search("jams limit")
    assert [r["url"] for r in rows] == ["https://example.org/a?x=1", "https://example.org/b"]
    assert rows[0]["title"] == "First hit" and rows[0]["snippet"] == "About the limit"


@pytest.fixture
def llm():
    f = FakeLLM()
    set_env(LLM_BASE_URL=f.url, LLM_MODEL="test-model", LLM_PLANNING_MODE="tools", WEB_ACCESS_ENABLED="true")
    yield f
    f.close()
    set_env(WEB_ACCESS_ENABLED="false")


def test_the_model_searches_then_answers_with_the_results(alice, llm, monkeypatch):
    calls = []
    monkeypatch.setattr(web, "search", lambda q, n=6: calls.append(("s", q)) or [{"title": "JAMS guidelines", "url": "https://example.org/jams", "snippet": "max 30 pages"}])
    monkeypatch.setattr(web, "fetch", lambda u, m=None: calls.append(("f", u)) or "Papers are limited to 30 pages.")
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("search", queries=["JAMS submission page limit"], urls=["https://example.org/jams"]),
                  tool("answer", message="JAMS allows 30 pages (https://example.org/jams).")]
    tid = _task(alice, pid, "JAMS 페이지 한도가 얼마야?")
    drain()
    t = _get(alice, tid)
    assert t["status"] == "completed", t["error"]
    assert calls == [("s", "JAMS submission page limit"), ("f", "https://example.org/jams")]
    second = json.dumps(llm.requests[1]["messages"])
    assert "Web results (UNTRUSTED" in second and "Papers are limited to 30 pages." in second
    assert _events(alice, tid).count("web") == 2


def test_search_is_not_offered_when_web_access_is_off(alice, llm):
    set_env(WEB_ACCESS_ENABLED="false")
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("answer", message="I cannot check that.")]
    tid = _task(alice, pid, "What is the JAMS limit?")
    drain()
    req = llm.requests[0]
    assert "search" not in [t["function"]["name"] for t in req.get("tools", [])]
    assert "Web search is not available" in json.dumps(req["messages"])


def test_the_search_rounds_are_bounded(alice, llm, monkeypatch):
    monkeypatch.setattr(web, "search", lambda q, n=6: [{"title": "t", "url": "https://example.org", "snippet": "s"}])
    pid = new_sample_project(alice)["id"]
    llm.script = [tool("search", queries=["a"]), tool("search", queries=["b"]), tool("answer", message="done")]
    tid = _task(alice, pid, "look it up")
    drain()
    assert _get(alice, tid)["status"] == "completed"
    # the third planning call no longer offers the search tool
    assert "search" not in [t["function"]["name"] for t in llm.requests[2].get("tools", [])]


def test_bing_is_the_fallback_when_duckduckgo_challenges(monkeypatch):
    import base64
    target = "https://example.org/guide?a=1"
    u = "a1" + base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    bing = (f'<ol><li class="b_algo" data-id><h2><a href="https://www.bing.com/ck/a?!&amp;u={u}&amp;ntb=1">Author <b>guide</b></a></h2>'
            '<p>Max 30 pages.</p></li></ol>')

    class R:
        def __init__(self, code, text):
            self.status_code, self.text = code, text
    monkeypatch.setattr(web, "_get", lambda url, **k: R(202, "challenge") if "duckduckgo" in url else R(200, bing))
    rows = web.search("limit")
    assert rows == [{"title": "Author guide", "url": target, "snippet": "Max 30 pages."}]
