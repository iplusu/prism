"""Hardened ZIP extraction for project import.

Blocks: path traversal, absolute paths, symlinks and special files, .git
directories, too many entries, decompression bombs (both declared and actual
size are checked while streaming).
"""

from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .paths import PathError, normalize_rel_path

_JUNK_PREFIXES = ("__MACOSX/",)
_JUNK_NAMES = {".DS_Store", "Thumbs.db"}


class ZipImportError(ValueError):
    pass


@dataclass
class ZipImportResult:
    files: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    total_bytes: int = 0
    stripped_prefix: str = ""


def _common_prefix(names: list[str]) -> str:
    """If every entry lives under one top-level folder (typical of Overleaf /
    GitHub exports), strip it."""
    tops = {n.split("/", 1)[0] for n in names if n}
    if len(tops) == 1 and all("/" in n for n in names) and next(iter(tops)) not in ("", ".", "..", ".git"):
        return next(iter(tops)) + "/"
    return ""


def extract_project_zip(
    zip_path: Path, dest: Path, *, max_total_bytes: int, max_entries: int, max_ratio: int = 200
) -> ZipImportResult:
    dest.mkdir(parents=True, exist_ok=True)
    res = ZipImportResult()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise ZipImportError(f"not a valid ZIP file: {e}") from e
    with zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise ZipImportError(f"too many entries ({len(infos)} > {max_entries})")
        declared = sum(i.file_size for i in infos)
        if declared > max_total_bytes:
            raise ZipImportError(f"uncompressed size {declared} exceeds limit {max_total_bytes}")
        names = [i.filename for i in infos if not i.is_dir() and not i.filename.startswith(_JUNK_PREFIXES)]
        prefix = _common_prefix(names)
        res.stripped_prefix = prefix
        written = 0
        for info in infos:
            name = info.filename
            if name.startswith(_JUNK_PREFIXES) or name.rsplit("/", 1)[-1] in _JUNK_NAMES:
                continue
            if info.is_dir():
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and (stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or mode & 0o170000 == 0)):
                res.skipped.append({"path": name, "reason": "symbolic link or special file"})
                continue
            if info.flag_bits & 0x1:
                raise ZipImportError("encrypted ZIP entries are not supported")
            # Validate the full entry name first, then strip the common folder.
            if ".git" in name.split("/")[:2]:
                res.skipped.append({"path": name, "reason": "foreign .git data is not imported"})
                continue
            try:
                normalize_rel_path(name)
                rel = normalize_rel_path(name[len(prefix):] if prefix and name.startswith(prefix) else name)
            except PathError as e:
                raise ZipImportError(f"unsafe path in archive: {name!r} ({e})") from e
            if info.compress_size and info.file_size / max(info.compress_size, 1) > max_ratio and info.file_size > 1_000_000:
                raise ZipImportError(f"suspicious compression ratio for {name!r}")
            target = dest / rel
            # Parents must not be symlinks (they cannot be, we create them), and
            # the final path must stay inside dest.
            if not str(target.resolve()).startswith(str(dest.resolve()) + "/"):
                raise ZipImportError(f"unsafe path in archive: {name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                while True:
                    chunk = src.read(1 << 16)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_total_bytes:
                        raise ZipImportError("archive expands beyond the size limit")
                    out.write(chunk)
            res.files.append(rel)
        res.total_bytes = written
    if not res.files:
        raise ZipImportError("archive contains no files")
    return res
