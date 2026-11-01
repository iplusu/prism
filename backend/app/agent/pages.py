"""Page-limit arithmetic for requests like "fit in 8 pages". The numbers are estimates from the text lines
pdftotext finds on each page: figures, tables and float placement are not measured."""

from __future__ import annotations

import re

_TARGET = re.compile(r"(\d{1,3})\s*(?:pages?\b|p\b|페이지|쪽)", re.I)


def target_pages(request: str) -> int | None:
    """The page limit a request names ("8 pages", "8페이지 이내"), or None."""
    m = _TARGET.search(request or "")
    n = int(m.group(1)) if m else 0
    return n if 1 <= n <= 200 else None


def overflow(page_lines: list[int] | None, target: int) -> dict | None:
    """How much text sits beyond `target` pages: {pages, lines, full_page_lines}. None if unmeasured."""
    if not page_lines:
        return None
    body = sorted(page_lines[:-1]) or page_lines
    full = body[len(body) // 2]  # median of the pages before the last one
    if len(page_lines) <= target:
        return {"pages": len(page_lines), "lines": 0, "full_page_lines": full, "spare_lines": max(0, full - page_lines[-1]) if len(page_lines) == target else None}
    return {"pages": len(page_lines), "lines": sum(page_lines[target:]), "full_page_lines": full, "spare_lines": None}


def note(page_lines: list[int] | None, target: int | None) -> str:
    if target is None or not page_lines:
        return ""
    o = overflow(page_lines, target)
    assert o is not None
    if o["lines"] == 0:
        spare = f"; about {o['spare_lines']} lines of room on the last page" if o["spare_lines"] is not None else ""
        return f"Requested limit {target} pages: already met ({o['pages']} pages{spare})."
    return (f"Requested limit {target} pages: over by {o['pages'] - target} page(s). Roughly {o['lines']} text lines sit beyond page {target} "
            f"(a full body page holds about {o['full_page_lines']} lines). ESTIMATE only: figures, tables and float placement are not "
            f"counted, so cutting that many lines may not be enough, or a smaller figure may be enough instead.")
