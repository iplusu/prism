"""Static change report: citations, labels/references, numbers, equations.

This is a textual analysis. It does not judge correctness of content or
layout, and nothing here looks at the rendered PDF.
"""

from __future__ import annotations

import re
from collections import Counter

from ..agent.context import bib_entries, citations_used, labels

_REF = re.compile(r"\\(?:ref|eqref|pageref|autoref|cref|Cref|nameref|vref)\*?\{([^}]+)\}")
_NUM = re.compile(r"(?<![A-Za-z\\])[-+]?\d+(?:[.,]\d+)*(?:\\?%)?")
_MATH_ENV = re.compile(r"\\begin\{(equation|align|gather|multline|eqnarray|flalign|alignat)(\*?)\}(.*?)\\end\{\1\2\}", re.S)
_DISPLAY = re.compile(r"\\\[(.*?)\\\]|\$\$(.*?)\$\$", re.S)
_INLINE = re.compile(r"(?<![\\$])\$([^$\n]{1,300}?)\$(?!\$)")


def _refs(files: dict[str, bytes]) -> set[str]:
    out = set()
    for p, d in files.items():
        if p.endswith(".tex"):
            for m in _REF.finditer(d.decode("utf-8", errors="replace")):
                out.update(k.strip() for k in m.group(1).split(",") if k.strip())
    return out


def _strip_comments(text: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in text.splitlines())


def _numbers(text: str) -> Counter:
    return Counter(m.group(0).replace("\\%", "%") for m in _NUM.finditer(_strip_comments(text)))


def _equations(text: str) -> Counter:
    text = _strip_comments(text)
    eqs = [re.sub(r"\s+", " ", m.group(3)).strip() for m in _MATH_ENV.finditer(text)]
    eqs += [re.sub(r"\s+", " ", m.group(1) or m.group(2)).strip() for m in _DISPLAY.finditer(text)]
    eqs += [re.sub(r"\s+", " ", m.group(1)).strip() for m in _INLINE.finditer(text)]
    return Counter(e for e in eqs if e)


def _counter_diff(before: Counter, after: Counter, limit: int = 30) -> dict:
    removed = list((before - after).elements())
    added = list((after - before).elements())
    return {"removed": removed[:limit], "added": added[:limit], "removed_count": len(removed), "added_count": len(added)}


def analyze(base: dict[str, bytes], cand: dict[str, bytes], changed: list[str]) -> dict:
    bib_b = {e["key"] for e in bib_entries(base)}
    bib_c = {e["key"] for e in bib_entries(cand)}
    cit_b, cit_c = citations_used(base), citations_used(cand)
    lab_b = {x["label"] for x in labels(base)}
    lab_c = {x["label"] for x in labels(cand)}
    ref_b, ref_c = _refs(base), _refs(cand)
    undef_cit_b, undef_cit_c = cit_b - bib_b, cit_c - bib_c
    undef_ref_b, undef_ref_c = ref_b - lab_b, ref_c - lab_c

    numbers, equations = {}, {}
    for p in changed:
        if not p.endswith(".tex"):
            continue
        tb = base.get(p, b"").decode("utf-8", errors="replace")
        tc = cand.get(p, b"").decode("utf-8", errors="replace")
        nd = _counter_diff(_numbers(tb), _numbers(tc))
        if nd["added_count"] or nd["removed_count"]:
            numbers[p] = nd
        ed = _counter_diff(_equations(tb), _equations(tc), limit=10)
        if ed["added_count"] or ed["removed_count"]:
            equations[p] = ed
    return {
        "citations": {
            "added": sorted(cit_c - cit_b), "removed": sorted(cit_b - cit_c),
            "bib_entries_added": sorted(bib_c - bib_b), "bib_entries_removed": sorted(bib_b - bib_c),
            "new_undefined": sorted(undef_cit_c - undef_cit_b), "undefined_total": sorted(undef_cit_c),
        },
        "labels": {
            "added": sorted(lab_c - lab_b), "removed": sorted(lab_b - lab_c),
            "new_dangling_refs": sorted(undef_ref_c - undef_ref_b), "dangling_total": sorted(undef_ref_c),
        },
        "numbers": numbers,
        "equations": equations,
    }


def evaluate_checks(checks: list[str], report: dict, build_ok: bool | None, base_build_ok: bool | None,
                    scope_ok: bool, warn_delta: dict) -> list[dict]:
    res = []
    for c in checks:
        if c == "compiles":
            st = "not_evaluated" if build_ok is None else ("passed" if build_ok else "failed")
            detail = "" if build_ok is not False or base_build_ok is not False else "the base version did not compile either"
        elif c == "only_write_scope":
            st, detail = ("passed" if scope_ok else "failed"), ""
        elif c == "no_new_undefined_citations":
            bad = report["citations"]["new_undefined"]
            st, detail = ("failed" if bad or warn_delta.get("citation", 0) > 0 else "passed"), ", ".join(bad)
        elif c == "no_new_undefined_references":
            bad = report["labels"]["new_dangling_refs"]
            st, detail = ("failed" if bad or warn_delta.get("reference", 0) > 0 else "passed"), ", ".join(bad)
        elif c == "preserve_citations":
            rem = report["citations"]["removed"]
            st, detail = ("failed" if rem else "passed"), ", ".join(rem)
        elif c == "preserve_labels":
            rem = report["labels"]["removed"]
            st, detail = ("failed" if rem else "passed"), ", ".join(rem)
        elif c == "preserve_numbers":
            st, detail = ("failed" if report["numbers"] else "passed"), ", ".join(report["numbers"])
        elif c == "preserve_equations":
            st, detail = ("failed" if report["equations"] else "passed"), ", ".join(report["equations"])
        else:
            st, detail = "not_evaluated", "unknown check"
        res.append({"check": c, "status": st, "detail": detail})
    return res


def warning_counts(diags: list[dict]) -> dict:
    c = {"citation": 0, "reference": 0, "warning": 0, "error": 0}
    for d in diags or []:
        m = d.get("message", "")
        if "Citation" in m and "undefined" in m:
            c["citation"] += 1
        elif "Reference" in m and "undefined" in m:
            c["reference"] += 1
        if d.get("severity") in ("warning", "error"):
            c[d["severity"]] += 1
    return c
