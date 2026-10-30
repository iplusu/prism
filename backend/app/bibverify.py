"""Check that bibliography entries refer to works that exist.

Each entry is looked up by its DOI (doi.org, which serves Crossref and DataCite), by its arXiv id (arXiv API),
or by title (Crossref, then OpenAlex). The registered title is compared with the entry's title:

* verified   the identifier resolves / a close title match was found, and the titles agree
* mismatch   the identifier resolves, but to a different title (a typo'd DOI or a made-up title on a real id)
* not_found  the identifier does not exist, or no close match exists in the databases
* unchecked  nothing to look up (no title or identifier) or a service failed: says nothing about the entry

"not_found" is a hint, not a verdict: books, theses, reports, standards and non-English works are poorly
covered by these databases. Only fixed public hosts are contacted; nothing from the entries becomes a URL host."""

from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

from . import references as refs

MAX_ENTRIES = 80
DEADLINE_SECONDS = 100.0
VERIFIED_AT = 0.80
_UA = {"User-Agent": "Prism research workspace (bibliography check)"}
_ATOM = {"a": "http://www.w3.org/2005/Atom"}
_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 3600
RETRY_AFTER_MAX = 8


class ServiceError(RuntimeError):
    pass


def _get_json(url: str, headers: dict | None = None) -> dict | None:
    """JSON from a fixed public service; None on 404."""
    for attempt in range(2):
        try:
            r = httpx.get(url, headers={**_UA, **(headers or {})}, timeout=20.0, follow_redirects=True)
        except httpx.HTTPError as e:
            raise ServiceError(f"{httpx.URL(url).host}: {type(e).__name__}") from e
        if r.status_code == 429 and attempt == 0:  # rate limited: wait as asked (briefly) and try once more
            ra = r.headers.get("Retry-After", "")
            time.sleep(min(int(ra), RETRY_AFTER_MAX) if ra.isdigit() else 2.0)
            continue
        break
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ServiceError(f"{httpx.URL(url).host}: HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError as e:
        raise ServiceError(f"{httpx.URL(url).host}: not JSON") from e


# ---------------------------------------------------------------- parsing ---

_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,")


def _value(text: str, i: int) -> tuple[str, int]:
    """Field value starting at text[i]: {balanced}, "quoted" or a bare token."""
    n = len(text)
    if i < n and text[i] == "{":
        depth, j = 0, i
        while j < n:
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    return text[i + 1: j], j + 1
            j += 1
        return text[i + 1:], n
    if i < n and text[i] == '"':
        j = i + 1
        while j < n and not (text[j] == '"' and text[j - 1] != "\\"):
            j += 1
        return text[i + 1: j], j + 1
    m = re.match(r"[^,}\s]+", text[i:])
    return (m.group(0), i + m.end()) if m else ("", i)


def parse_entries(files: dict[str, bytes]) -> list[dict]:
    out = []
    for path in sorted(files):
        if not path.endswith(".bib"):
            continue
        text = files[path].decode("utf-8", errors="replace")
        starts = [m for m in _ENTRY.finditer(text) if m.group(1).lower() not in ("comment", "string", "preamble")]
        for idx, m in enumerate(starts):
            body = text[m.end(): starts[idx + 1].start() if idx + 1 < len(starts) else len(text)]
            fields, i = {}, 0
            for fm in re.finditer(r"(?:^|[,\s])(\w[\w-]*)\s*=\s*", body):
                if fm.start() < i:
                    continue
                val, i = _value(body, fm.end())
                fields.setdefault(fm.group(1).lower(), val)
            out.append({"key": m.group(2), "type": m.group(1).lower(), "file": path, "fields": fields,
                        "line": text.count("\n", 0, m.start()) + 1})
    return out


def normalize_title(s: str) -> str:
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s or "")
    s = re.sub(r"[{}$\\]", "", s).lower()
    return " ".join(re.findall(r"[^\W_]+", s))


def similarity(a: str, b: str) -> float:
    a, b = normalize_title(a), normalize_title(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    return max(SequenceMatcher(None, a, b).ratio(), len(ta & tb) / max(1, len(ta | tb)))


def _clean_doi(d: str) -> str:
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d.strip(), flags=re.I)


def _arxiv_id(f: dict) -> str | None:
    e = (f.get("eprint") or "").strip()
    pref = (f.get("archiveprefix") or f.get("eprinttype") or "").strip().lower()
    if e and (pref == "arxiv" or refs.ARXIV_ID.match(e)):
        e = re.sub(r"^arxiv:", "", e, flags=re.I)
        e = re.sub(r"v\d+$", "", e)
        return e if refs.ARXIV_ID.match(e) else None
    return None


# ---------------------------------------------------------------- lookups ---

def lookup_doi(doi: str) -> dict | None:
    d = _get_json(f"https://doi.org/{quote(doi, safe='/')}", {"Accept": "application/vnd.citationstyles.csl+json"})
    if not d:
        return None
    title = d.get("title")
    title = title[0] if isinstance(title, list) and title else (title or "")
    return {"title": re.sub(r"<[^>]+>", "", title), "id": f"doi:{doi}", "source": "doi.org"}


def lookup_arxiv_batch(ids: list[str]) -> dict[str, dict]:
    """One request for many ids (arXiv asks for at most one request per 3 s)."""
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 40):
        chunk = ids[i: i + 40]
        with refs._arxiv_lock:
            wait = refs.ARXIV_MIN_INTERVAL - (time.monotonic() - refs._arxiv_last)
            if wait > 0:
                time.sleep(wait)
            try:
                xml = refs._get(f"https://export.arxiv.org/api/query?id_list={','.join(quote(c) for c in chunk)}&max_results={len(chunk)}", timeout=45.0)
            except refs.ReferenceError as e:
                raise ServiceError(str(e)) from e
            finally:
                refs._arxiv_last = time.monotonic()
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as e:
            raise ServiceError("arXiv returned malformed XML") from e
        for e in root.findall("a:entry", _ATOM):
            m = re.search(r"arxiv\.org/abs/(.+?)(v\d+)?$", (e.findtext("a:id", namespaces=_ATOM) or "").strip())
            title = re.sub(r"\s+", " ", e.findtext("a:title", namespaces=_ATOM) or "").strip()
            if m and title.lower() != "error":
                out[m.group(1)] = {"title": title, "id": f"arXiv:{m.group(1)}", "source": "arXiv"}
    return out


def search_title(title: str, author: str) -> dict | None:
    """Best title match in Crossref, then OpenAlex (None when neither has a close one)."""
    best = None
    q = quote((title + " " + author)[:300])
    d = _get_json(f"https://api.crossref.org/works?query.bibliographic={q}&rows=5&select=title,DOI")
    for it in ((d or {}).get("message") or {}).get("items", []):
        t = (it.get("title") or [""])[0]
        s = similarity(title, t)
        if not best or s > best[0]:
            best = (s, {"title": t, "id": f"doi:{it.get('DOI', '')}", "source": "Crossref"})
    if best and best[0] >= VERIFIED_AT:
        return best[1]
    d = _get_json(f"https://api.openalex.org/works?search={quote(title[:300])}&per-page=5&select=title,doi")
    for it in (d or {}).get("results", []):
        t = it.get("title") or ""
        s = similarity(title, t)
        if not best or s > best[0]:
            best = (s, {"title": t, "id": (it.get("doi") or "").replace("https://doi.org/", "doi:"), "source": "OpenAlex"})
    return best[1] if best and best[0] >= VERIFIED_AT else None


# ----------------------------------------------------------------- verify ---

def _result(e: dict, status: str, detail: str, found: dict | None = None) -> dict:
    return {"key": e["key"], "file": e["file"], "line": e.get("line"), "status": status, "detail": detail,
            "found_title": (found or {}).get("title", ""), "found_id": (found or {}).get("id", ""),
            "source": (found or {}).get("source", "")}


def _judge(e: dict, found: dict | None, how: str) -> dict:
    title = e["fields"].get("title", "")
    if found is None:
        return _result(e, "not_found", f"{how} does not resolve in the database")
    if not title.strip():
        return _result(e, "verified", f"{how} resolves (the entry has no title to compare)", found)
    s = similarity(title, found["title"])
    if s >= VERIFIED_AT:
        return _result(e, "verified", f"{how} resolves to a matching title", found)
    return _result(e, "mismatch", f"{how} resolves to a different title (similarity {s:.0%})", found)


def _check_one(e: dict) -> dict:
    f = e["fields"]
    doi = _clean_doi(f.get("doi", ""))
    title = f.get("title", "")
    try:
        if doi and refs.DOI.match(doi):
            ck = ("doi", doi.lower())
            found = _cached(ck, lambda: lookup_doi(doi))
            return _judge(e, found, f"DOI {doi}")
        if not title.strip():
            return _result(e, "unchecked", "no DOI, arXiv id or title to look up")
        ck = ("title", normalize_title(title), normalize_title(f.get("author", ""))[:60])
        author = re.split(r"\s+and\s+", f.get("author", ""))[0] if f.get("author") else ""
        found = _cached(ck, lambda: search_title(title, author))
        if found is None:
            return _result(e, "not_found", "no close title match in Crossref or OpenAlex "
                           "(books, theses, reports and non-English works are often missing)")
        return _result(e, "verified", f"title found in {found['source']}", found)
    except ServiceError as ex:
        return _result(e, "unchecked", f"lookup failed: {ex}")


def _cached(key: tuple, fn):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]["v"]
    v = fn()
    with _cache_lock:
        _cache[key] = (now, {"v": v})
    return v


def verify(entries: list[dict]) -> dict:
    t0 = time.monotonic()
    total = len(entries)
    entries = entries[:MAX_ENTRIES]
    results: dict[int, dict] = {}
    # arXiv entries in one batched request
    arx = {i: _arxiv_id(e["fields"]) for i, e in enumerate(entries)
           if not _clean_doi(e["fields"].get("doi", "")) and _arxiv_id(e["fields"])}
    if arx:
        try:
            got = lookup_arxiv_batch(sorted(set(arx.values())))
            for i, aid in arx.items():
                results[i] = _judge(entries[i], got.get(aid), f"arXiv:{aid}")
        except ServiceError as ex:
            for i in arx:
                results[i] = _result(entries[i], "unchecked", f"lookup failed: {ex}")
    rest = [i for i in range(len(entries)) if i not in results]

    def work(i: int) -> tuple[int, dict]:
        if time.monotonic() - t0 > DEADLINE_SECONDS:
            return i, _result(entries[i], "unchecked", "time budget used up; run the check again")
        return i, _check_one(entries[i])

    with ThreadPoolExecutor(max_workers=4) as pool:
        for i, r in pool.map(work, rest):
            results[i] = r
    out = [results[i] for i in range(len(entries))]
    counts = {s: sum(1 for r in out if r["status"] == s) for s in ("verified", "mismatch", "not_found", "unchecked")}
    return {"results": out, "counts": counts, "total": total, "checked": len(out), "truncated": total > len(out)}
