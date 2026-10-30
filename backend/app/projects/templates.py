"""Official conference/preprint templates.

Prism does not redistribute style files: several official author kits carry
no license that would allow it. Instead, a project created from a template
downloads the kit from its publisher's official URL at that moment and goes
through the same hardened ZIP import as a user upload.

Only the URLs listed here are ever fetched (no user-supplied URLs), over
HTTPS, with a size limit. Add or update an entry when a venue publishes a
new year's kit.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx

from ..security.paths import glob_match


class TemplateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    description: str
    url: str
    source: str  # human-readable origin, shown in the UI
    main_file: str
    # renames applied after extraction, e.g. the kit's example file -> main.tex
    rename: tuple[tuple[str, str], ...] = ()
    # kit files that are not part of a paper (CI, tests, generated PDFs)
    exclude: tuple[str, ...] = (".github/**", "tests/**", ".gitignore")


TEMPLATES: tuple[Template, ...] = (
    Template("acl", "ACL / *ACL", "ACL Rolling Review and *ACL conferences (review mode)",
             "https://codeload.github.com/acl-org/acl-style-files/zip/refs/heads/master",
             "github.com/acl-org/acl-style-files", "acl_latex.tex",
             exclude=(".github/**", "tests/**", ".gitignore", "acl_lualatex.tex", "formatting.md", "anthology.bib.txt")),
    Template("cvpr", "CVPR / ICCV", "CVPR/ICCV/3DV author kit (review mode, with rebuttal template)",
             "https://codeload.github.com/cvpr-org/author-kit/zip/refs/heads/main",
             "github.com/cvpr-org/author-kit", "main.tex"),
    Template("neurips2025", "NeurIPS 2025", "NeurIPS 2025 style files and formatting instructions",
             "https://media.neurips.cc/Conferences/NeurIPS2025/Styles.zip",
             "media.neurips.cc", "main.tex", rename=(("neurips_2025.tex", "main.tex"),),
             exclude=("*.pdf",)),
    Template("icml2026", "ICML 2026", "ICML 2026 style files and example paper",
             "https://media.icml.cc/Conferences/ICML2026/Styles/icml2026.zip",
             "media.icml.cc", "main.tex", rename=(("example_paper.tex", "main.tex"),),
             exclude=("example_paper.pdf",)),  # icml_numpapers.pdf is included by the style file: keep it
    Template("arxiv", "arXiv preprint", "Clean preprint style (arxiv.sty, MIT license)",
             "https://codeload.github.com/kourgeorge/arxiv-style/zip/refs/heads/master",
             "github.com/kourgeorge/arxiv-style", "main.tex", rename=(("template.tex", "main.tex"),),
             exclude=(".gitignore", "template.pdf")),
)

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def get(template_id: str) -> Template:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    raise TemplateError(f"unknown template '{template_id}'")


def catalogue() -> list[dict]:
    return [{"id": t.id, "label": t.label, "description": t.description, "source": t.source} for t in TEMPLATES]


def download(t: Template, dest_zip: Path, *, timeout: float = 30.0) -> None:
    if not t.url.startswith("https://"):
        raise TemplateError("template URLs must use HTTPS")
    size = 0
    try:
        with httpx.stream("GET", t.url, timeout=timeout, follow_redirects=True) as r:
            if r.status_code != 200:
                raise TemplateError(f"{t.source} answered HTTP {r.status_code}; the kit may have moved")
            with open(dest_zip, "wb") as out:
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise TemplateError("template download exceeds 20 MB")
                    out.write(chunk)
    except httpx.HTTPError as e:
        raise TemplateError(f"could not download the {t.label} kit from {t.source}: {type(e).__name__}") from e


def prepare(t: Template, root: Path, files: list[str]) -> list[str]:
    """Drop excluded kit files and apply renames inside an extracted kit."""
    kept = []
    for f in files:
        if glob_match(f, list(t.exclude)):
            (root / f).unlink(missing_ok=True)
        else:
            kept.append(f)
    for src, dst in t.rename:
        if src in kept and dst not in kept:
            (root / src).rename(root / dst)
            kept = [dst if f == src else f for f in kept]
    for d in sorted({p for p in root.rglob("*") if p.is_dir()}, key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    if t.main_file not in kept:
        raise TemplateError(f"the downloaded kit has no {t.main_file}; the publisher may have changed its layout")
    return sorted(kept)

