"""Literature search and BibTeX import.

Sources are fixed, public metadata services; nothing user-supplied is used as a
URL. arXiv: search via the export API, BibTeX from arxiv.org/bibtex/<id>.
DOI: BibTeX via doi.org content negotiation (Crossref, DataCite, ...).
Entries are imported exactly as the service returns them; nothing is invented.
"""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx

ARXIV_ID = re.compile(r"^(\d{4}\.\d{4,5}|[a-z][a-z.-]+/\d{7})(v\d+)?$", re.I)
DOI = re.compile(r"^10\.\d{4,9}/[^\s]+$")
_ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_UA = {"User-Agent": "Prism research workspace (BibTeX import)"}
MAX_BYTES = 2 * 1024 * 1024


class ReferenceError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _get(url: str, *, headers: dict | None = None, timeout: float = 20.0) -> str:
    try:
        r = httpx.get(url, headers={**_UA, **(headers or {})}, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ReferenceError(f"could not reach {httpx.URL(url).host}: {type(e).__name__}") from e
    if r.status_code == 404:
        raise ReferenceError("not found", 404)
    if r.status_code in (429, 503):
        raise ReferenceError(f"{httpx.URL(url).host} is rate limiting requests; try again in a minute", 503)
    if r.status_code != 200:
        raise ReferenceError(f"{httpx.URL(url).host} answered HTTP {r.status_code}")
    if len(r.content) > MAX_BYTES:
        raise ReferenceError("response too large")
    return r.text


# arXiv asks for at most one API request every 3 seconds; searches are also
# cached briefly so repeated or identical queries do not hit the service.
_arxiv_lock = threading.Lock()
_arxiv_last = 0.0
_search_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
SEARCH_CACHE_SECONDS = 600
ARXIV_MIN_INTERVAL = 3.0


def _clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "be", "of", "in", "on", "for", "to", "and", "or", "with",
              "by", "at", "as", "from", "all", "you", "we", "it", "this", "that", "via", "using"}
_WORD = re.compile(r"[\w.\-]+", re.UNICODE)


def _arxiv_query(search_query: str, limit: int) -> list[dict]:
    """One request to the arXiv API, spaced to arXiv's 1-request-per-3-seconds guideline."""
    global _arxiv_last
    with _arxiv_lock:
        wait = ARXIV_MIN_INTERVAL - (time.monotonic() - _arxiv_last)
        if wait > 0:
            time.sleep(wait)
        try:
            xml = _get(f"https://export.arxiv.org/api/query?search_query={search_query}&max_results={limit}", timeout=45.0)
        finally:
            _arxiv_last = time.monotonic()
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise ReferenceError("arXiv returned malformed XML") from e
    out = []
    for e in root.findall("a:entry", _ATOM):
        abs_url = _clean(e.findtext("a:id", namespaces=_ATOM))
        m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", abs_url)
        if not m:
            continue
        pc = e.find("arxiv:primary_category", _ATOM)
        out.append({
            "source": "arxiv", "id": m.group(1), "url": f"https://arxiv.org/abs/{m.group(1)}",
            "title": _clean(e.findtext("a:title", namespaces=_ATOM)),
            "authors": [_clean(a.findtext("a:name", namespaces=_ATOM)) for a in e.findall("a:author", _ATOM)],
            "year": _clean(e.findtext("a:published", namespaces=_ATOM))[:4],
            "summary": _clean(e.findtext("a:summary", namespaces=_ATOM))[:600],
            "category": pc.get("term", "") if pc is not None else "",
        })
    return out


def search_arxiv(query: str, limit: int = 10) -> list[dict]:
    words = _WORD.findall(_clean(query))[:12]  # also drops quotes and operators typed by the user
    if not words:
        raise ReferenceError("empty query", 400)
    limit = max(1, min(limit, 25))
    ckey = (" ".join(words).lower(), limit)
    hit = _search_cache.get(ckey)
    if hit and time.monotonic() - hit[0] < SEARCH_CACHE_SECONDS:
        return hit[1]
    # 1) the exact phrase: best for pasted titles. arXiv's index returns nothing when
    #    stopwords such as "is" / "all" are ANDed in, so 2) falls back to the content words.
    out = _arxiv_query("all:%22" + "+".join(quote(w) for w in words) + "%22", limit)
    if not out and len(words) > 1:
        content = [w for w in words if w.lower() not in _STOPWORDS] or words
        out = _arxiv_query("+AND+".join(f"all:{quote(w)}" for w in content), limit)
    _search_cache[ckey] = (time.monotonic(), out)
    return out


def fetch_bibtex(source: str, ident: str) -> str:
    ident = ident.strip()
    if source == "arxiv":
        ident = re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:)", "", ident, flags=re.I).removesuffix(".pdf")
        if not ARXIV_ID.match(ident):
            raise ReferenceError("not an arXiv identifier (e.g. 1706.03762)", 400)
        bib = _get(f"https://arxiv.org/bibtex/{ident}")
    elif source == "doi":
        ident = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", ident, flags=re.I)
        if not DOI.match(ident):
            raise ReferenceError("not a DOI (e.g. 10.1038/nature14539)", 400)
        bib = _get(f"https://doi.org/{quote(ident, safe='/')}", headers={"Accept": "application/x-bibtex; charset=utf-8"})
    else:
        raise ReferenceError(f"unknown source '{source}'", 400)
    bib = bib.strip()
    if not re.match(r"^@\w+\s*\{", bib):
        raise ReferenceError("the service did not return a BibTeX entry")
    return bib


_KEY = re.compile(r"^(@\w+\s*\{\s*)([^,\s]+)\s*,")


def entry_key(bib: str) -> str:
    m = _KEY.match(bib)
    if not m:
        raise ReferenceError("BibTeX entry has no citation key")
    return m.group(2)


def with_unique_key(bib: str, taken: set[str]) -> tuple[str, str]:
    """Keep the service's key unless the project already uses it for another work."""
    key = entry_key(bib)
    if key not in taken:
        return bib, key
    for suffix in "bcdefghijklmnopqrstuvwxyz":
        cand = f"{key}{suffix}"
        if cand not in taken:
            return _KEY.sub(lambda m: f"{m.group(1)}{cand},", bib, count=1), cand
    raise ReferenceError("could not find a free citation key", 409)


def identifies(bib_text: str, source: str, ident: str) -> bool:
    """Whether an existing .bib already contains this work (same eprint / DOI)."""
    ident = ident.strip().lower()
    if source == "arxiv":
        ident = re.sub(r"v\d+$", "", re.sub(r"^(https?://arxiv\.org/(abs|pdf)/|arxiv:)", "", ident))
        return bool(re.search(r"eprint\s*=\s*[{\"]" + re.escape(ident) + r"(v\d+)?[}\"]", bib_text, re.I))
    ident = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", ident)
    return bool(re.search(r"doi\s*=\s*[{\"]" + re.escape(ident) + r"[}\"]", bib_text, re.I))
