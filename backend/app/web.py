"""Web search and page fetch for the orchestrator.

The model only *asks* (action "search" with queries/urls); the server runs the requests and hands the text
back as untrusted data. Rules enforced here, not by the model:
  - http/https on ports 80/443 only; no credentials in the URL
  - every address the host resolves to (and every redirect hop) must be public: loopback, private, link-local,
    CGNAT/tailnet, multicast and reserved ranges are refused, so the server's own network cannot be probed
  - response size, time and returned text are capped
Search uses SearXNG's JSON API when WEB_SEARCH_URL is set, else DuckDuckGo's HTML endpoint (no key).
"""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
import subprocess
import tempfile
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from .config import get_settings

_UA = {"User-Agent": "Mozilla/5.0 (compatible; PrismResearch/1.0)"}
_MAX_BYTES = 3_000_000
_MAX_HOPS = 4
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class WebError(RuntimeError):
    pass


def _public(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and a not in _CGNAT and not a.is_multicast


def check_url(url: str) -> str:
    """Return the URL if it is safe to request, else raise WebError."""
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise WebError("only http(s) URLs are allowed")
    if u.username or u.password:
        raise WebError("URLs with credentials are not allowed")
    if u.port not in (None, 80, 443):
        raise WebError("only ports 80 and 443 are allowed")
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise WebError(f"cannot resolve {u.hostname}") from e
    if not infos or not all(_public(i[4][0]) for i in infos):
        raise WebError(f"{u.hostname} is not a public address; refused")
    return u.geturl()


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html)
    text = "".join(p.out)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _get(url: str, headers: dict | None = None, params: dict | None = None) -> httpx.Response:
    s = get_settings()
    cur = check_url(url)
    with httpx.Client(timeout=s.web_timeout_seconds, follow_redirects=False, headers={**_UA, **(headers or {})}) as c:
        for hop in range(_MAX_HOPS + 1):
            try:
                with c.stream("GET", cur, params=params if hop == 0 else None) as r:
                    if r.is_redirect and r.headers.get("location"):
                        cur = check_url(urljoin(cur, r.headers["location"]))
                        continue
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if len(body) > _MAX_BYTES:
                            break
                    r._content = body[:_MAX_BYTES]
                    return r
            except httpx.HTTPError as e:
                raise WebError(f"request failed: {type(e).__name__}") from e
    raise WebError("too many redirects")


def fetch(url: str, max_chars: int | None = None) -> str:
    """Fetch one page as plain text (HTML stripped, PDF via pdftotext)."""
    limit = max_chars or get_settings().web_fetch_max_chars
    r = _get(url)
    if r.status_code >= 400:
        raise WebError(f"HTTP {r.status_code}")
    ctype = r.headers.get("content-type", "").lower()
    body = r.content
    if "pdf" in ctype or body[:5] == b"%PDF-":
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(body)
            f.flush()
            try:
                out = subprocess.run(["pdftotext", "-layout", "-l", "30", f.name, "-"], capture_output=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                raise WebError("could not read the PDF") from e
        text = out.stdout.decode("utf-8", "replace")
    elif "html" in ctype or "xml" in ctype or not ctype:
        text = html_to_text(body.decode(r.encoding or "utf-8", "replace"))
    elif ctype.startswith("text/"):
        text = body.decode(r.encoding or "utf-8", "replace")
    else:
        raise WebError(f"unsupported content type {ctype.split(';')[0]}")
    text = text.strip()
    return text[:limit] + (f"\n[... cut at {limit} characters]" if len(text) > limit else "")


def search(query: str, n: int = 6) -> list[dict]:
    """Return up to n {title, url, snippet} results."""
    s = get_settings()
    q = " ".join(query.split())[:300]
    if not q:
        raise WebError("empty query")
    if s.web_search_url:
        r = _get(s.web_search_url, params={"q": q, "format": "json"})
        if r.status_code >= 400:
            raise WebError(f"search backend HTTP {r.status_code}")
        try:
            rows = r.json().get("results", [])
        except ValueError as e:
            raise WebError("search backend did not return JSON") from e
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")} for x in rows[:n]]
    out = _duckduckgo(q, n)
    if not out:  # DuckDuckGo answers automated queries with a challenge page (HTTP 202) after a few requests
        out = _bing(q, n)
    if not out:
        raise WebError("the search returned no results (the engines may be blocking automated queries; set WEB_SEARCH_URL to a SearXNG instance)")
    return out


def _bing(q: str, n: int) -> list[dict]:
    r = _get("https://www.bing.com/search", params={"q": q})
    if r.status_code >= 400:
        return []
    out: list[dict] = []
    for blk in r.text.split('<li class="b_algo"')[1:]:
        m = re.search(r'<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
        if not m:
            continue
        href = m.group(1).replace("&amp;", "&")
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):  # bing's click-tracking redirect carries the target as base64url after "a1"
            try:
                href = base64.urlsafe_b64decode(u[2:] + "=" * (-len(u[2:]) % 4)).decode()
            except ValueError:
                continue
        sn = re.search(r'<p[^>]*>(.*?)</p>', blk, re.S)
        if href.startswith("http") and "bing.com/ck/" not in href:
            out.append({"title": html_to_text(m.group(2)), "url": href, "snippet": html_to_text(sn.group(1)) if sn else ""})
        if len(out) >= n:
            break
    return out


def _duckduckgo(q: str, n: int) -> list[dict]:
    r = _get("https://html.duckduckgo.com/html/", params={"q": q})
    if r.status_code != 200:
        return []
    html = r.text
    out: list[dict] = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', html, re.S):
        href, title, rest = m.groups()
        href = href.replace("&amp;", "&")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href if "://" in href else "https:" + href).query).get("uddg", [""])[0])
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        if href.startswith("http"):
            out.append({"title": html_to_text(title), "url": href, "snippet": html_to_text(sn.group(1)) if sn else ""})
        if len(out) >= n:
            break
    return out
