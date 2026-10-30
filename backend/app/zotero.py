"""Import BibTeX from a Zotero library through the Zotero Web API v3.

Only https://api.zotero.org is contacted. The API key arrives with each request, is sent to Zotero in a header
and is never stored or logged; public group libraries need no key. Entries are the export Zotero itself produces
(`format=bibtex`), merged into the project's .bib without duplicating works already there."""

from __future__ import annotations

import logging
import re
import time

import httpx

from . import bibverify
from . import references as refs



class _HideKeys(logging.Filter):
    """Zotero's key lookup puts the API key in the URL path (GET /keys/<key>); httpx logs request URLs."""

    def filter(self, record: logging.LogRecord) -> bool:
        pat = re.compile(r"(api\.zotero\.org/keys/)[^\s/?\"']+")
        if isinstance(record.msg, str):
            record.msg = pat.sub(r"\1[hidden]", record.msg)
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(pat.sub(r"\1[hidden]", str(a)) if "/keys/" in str(a) else a for a in args)
        return True


for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).addFilter(_HideKeys())

BASE = "https://api.zotero.org"
PAGE = 100
MAX_ENTRIES = 500
_KEY = re.compile(r"^[A-Za-z0-9]{16,64}$")
_COLLECTION = re.compile(r"^[A-Za-z0-9]{8}$")
_LIB = re.compile(r"^(user|group:\d{1,12})$")


class ZoteroError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _request(path: str, params: dict | None, api_key: str | None) -> httpx.Response:
    headers = {"Zotero-API-Version": "3", "User-Agent": "Prism research workspace (Zotero import)"}
    if api_key:
        headers["Zotero-API-Key"] = api_key
    try:
        r = httpx.get(BASE + path, params=params, headers=headers, timeout=30.0)
    except httpx.HTTPError as e:
        raise ZoteroError(f"could not reach api.zotero.org: {type(e).__name__}") from e
    if r.status_code in (429, 503):
        raise ZoteroError(f"Zotero is rate limiting requests; retry in {r.headers.get('Retry-After', 'a minute')} s", 503)
    if r.status_code == 403:
        raise ZoteroError("Zotero refused access: wrong API key, a key without read access to this library, or a private library", 403)
    if r.status_code == 404:
        raise ZoteroError("not found in Zotero (check the library and collection)", 404)
    if r.status_code != 200:
        raise ZoteroError(f"Zotero answered HTTP {r.status_code}")
    backoff = r.headers.get("Backoff")
    if backoff and backoff.isdigit():
        time.sleep(min(int(backoff), 10))  # the API asks clients to pause
    return r


def _check_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    k = api_key.strip()
    if not _KEY.match(k):
        raise ZoteroError("that does not look like a Zotero API key", 400)
    return k


def _prefix(library: str, api_key: str | None) -> str:
    if not _LIB.match(library):
        raise ZoteroError("library must be 'user' or 'group:<id>'", 400)
    if library == "user":
        if not api_key:
            raise ZoteroError("your own library needs an API key (zotero.org/settings/keys)", 400)
        return f"/users/{_user_id(api_key)}"
    return f"/groups/{library.split(':', 1)[1]}"


def _user_id(api_key: str) -> int:
    d = _request(f"/keys/{api_key}", None, api_key).json()
    uid = d.get("userID")
    if not isinstance(uid, int):
        raise ZoteroError("Zotero did not return a user for this key")
    return uid


def libraries(api_key: str) -> list[dict]:
    api_key = _check_key(api_key)
    if not api_key:
        raise ZoteroError("an API key is required to list your libraries", 400)
    uid = _user_id(api_key)
    out = [{"id": "user", "name": "My library"}]
    for g in _request(f"/users/{uid}/groups", {"limit": 100}, api_key).json():
        gid = g.get("id")
        if isinstance(gid, int):
            out.append({"id": f"group:{gid}", "name": (g.get("data") or {}).get("name", f"group {gid}")})
    return out


def collections(library: str, api_key: str | None) -> list[dict]:
    api_key = _check_key(api_key)
    pre = _prefix(library, api_key)
    out, start = [], 0
    while start < 300:
        r = _request(f"{pre}/collections", {"limit": PAGE, "start": start}, api_key)
        rows = r.json()
        for c in rows:
            out.append({"key": c["key"], "name": (c.get("data") or {}).get("name", c["key"]),
                        "parent": (c.get("data") or {}).get("parentCollection") or None,
                        "items": (c.get("meta") or {}).get("numItems", 0)})
        if len(rows) < PAGE:
            break
        start += PAGE
    return out


def fetch_bibtex(library: str, collection: str | None, api_key: str | None) -> tuple[str, int]:
    """(BibTeX text, total items in the library/collection). Capped at MAX_ENTRIES entries."""
    api_key = _check_key(api_key)
    pre = _prefix(library, api_key)
    if collection:
        if not _COLLECTION.match(collection):
            raise ZoteroError("bad collection key", 400)
        pre += f"/collections/{collection}"
    parts, start, total = [], 0, 0
    while start < MAX_ENTRIES:
        r = _request(f"{pre}/items/top", {"format": "bibtex", "limit": PAGE, "start": start}, api_key)
        total = int(r.headers.get("Total-Results", "0") or 0)
        if r.text.strip():
            parts.append(r.text.strip())
        start += PAGE
        if start >= total:
            break
    return "\n\n".join(parts), total


def merge(existing: dict[str, bytes], exported: str, taken_keys: set[str]) -> dict:
    """Entries of `exported` not already present, with unique keys: {entries: [..], skipped: n, renamed: [(old, new)]}"""
    have_doi, have_title = set(), set()
    for e in bibverify.parse_entries(existing):
        d = bibverify._clean_doi(e["fields"].get("doi", "")).lower()
        if d:
            have_doi.add(d)
        t = bibverify.normalize_title(e["fields"].get("title", ""))
        if t:
            have_title.add(t)
    taken = set(taken_keys)
    out, skipped, renamed = [], 0, []
    starts = list(re.finditer(r"^@\w+\s*\{\s*[^,\s]+\s*,", exported, re.M))
    for i, m in enumerate(starts):
        block = exported[m.start(): starts[i + 1].start() if i + 1 < len(starts) else len(exported)].strip()
        parsed = bibverify.parse_entries({"x.bib": block.encode()})
        if not parsed:
            continue
        f = parsed[0]["fields"]
        doi = bibverify._clean_doi(f.get("doi", "")).lower()
        title = bibverify.normalize_title(f.get("title", ""))
        if (doi and doi in have_doi) or (title and title in have_title):
            skipped += 1
            continue
        old = parsed[0]["key"]
        try:
            block, key = refs.with_unique_key(block, taken)
        except refs.ReferenceError:
            skipped += 1
            continue
        if key != old:
            renamed.append((old, key))
        taken.add(key)
        if doi:
            have_doi.add(doi)
        if title:
            have_title.add(title)
        out.append(block)
    return {"entries": out, "skipped": skipped, "renamed": renamed}
