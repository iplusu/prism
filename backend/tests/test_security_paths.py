import io
import stat
import zipfile

import pytest

from app.security.paths import PathError, glob_match, is_protected, normalize_rel_path
from app.security.zipimport import ZipImportError, extract_project_zip


@pytest.mark.parametrize("bad", ["../x.tex", "a/../../x", "/etc/passwd", "~/x", "a\\b.tex", "a\x00b", ".git/config", "", "a//b/../../.."])
def test_rejects_unsafe_paths(bad):
    with pytest.raises(PathError):
        normalize_rel_path(bad)


def test_normalizes_safe_paths():
    assert normalize_rel_path("sections/./intro.tex") == "sections/intro.tex"


def test_protected_files():
    for p in ("CLAUDE.md", "sub/AGENTS.md", ".claude/settings.json", "latexmkrc", ".codex/config.toml", "x/.gemini/a"):
        assert is_protected(p), p
    assert not is_protected("sections/intro.tex")


def test_glob_scope():
    assert glob_match("sections/a.tex", ["sections/*.tex"])
    assert not glob_match("sections/sub/a.tex", ["sections/*.tex"])
    assert glob_match("sections/sub/a.tex", ["**/*.tex"])
    assert glob_match("main.tex", ["**/*.tex"])
    assert not glob_match("main.tex", ["sections/**"])


def _zip(entries, tmp_path, name="z.zip"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        for e in entries:
            if isinstance(e, zipfile.ZipInfo):
                zf.writestr(e, b"target")
            else:
                zf.writestr(e[0], e[1])
    return p


def _extract(p, tmp_path, **kw):
    return extract_project_zip(p, tmp_path / "out", max_total_bytes=kw.get("max_bytes", 10_000_000),
                               max_entries=kw.get("max_entries", 100))


def test_zip_traversal_blocked(tmp_path):
    p = _zip([("../evil.tex", b"x")], tmp_path)
    with pytest.raises(ZipImportError):
        _extract(p, tmp_path)
    assert not (tmp_path / "evil.tex").exists()


def test_zip_absolute_blocked(tmp_path):
    p = _zip([("/tmp/evil.tex", b"x")], tmp_path)
    with pytest.raises(ZipImportError):
        _extract(p, tmp_path)


def test_zip_symlink_skipped(tmp_path):
    info = zipfile.ZipInfo("link.tex")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    p = _zip([info, ("main.tex", b"\\documentclass{article}")], tmp_path)
    res = _extract(p, tmp_path)
    assert res.files == ["main.tex"]
    assert res.skipped and res.skipped[0]["path"] == "link.tex"
    assert not (tmp_path / "out" / "link.tex").exists()


def test_zip_bomb_declared_size(tmp_path):
    p = _zip([("big.tex", b"0" * 2_000_000)], tmp_path)
    with pytest.raises(ZipImportError, match="exceeds limit|size"):
        _extract(p, tmp_path, max_bytes=1_000_000)


def test_zip_bomb_lying_header(tmp_path):
    """Declared size is forged small; the streaming counter must still stop it."""
    p = _zip([("big.tex", b"0" * 3_000_000)], tmp_path)
    raw = bytearray(p.read_bytes())
    # Patch the central directory's uncompressed size (offset 24 in the CD header) to 10 bytes.
    cd = raw.rfind(b"PK\x01\x02")
    raw[cd + 24:cd + 28] = (10).to_bytes(4, "little")
    lh = raw.find(b"PK\x03\x04")
    raw[lh + 22:lh + 26] = (10).to_bytes(4, "little")
    p.write_bytes(bytes(raw))
    with pytest.raises((ZipImportError, zipfile.BadZipFile, Exception)):
        _extract(p, tmp_path, max_bytes=1_000_000)
    out = tmp_path / "out" / "big.tex"
    assert not out.exists() or out.stat().st_size <= 1_000_000 + 65536


def test_zip_too_many_entries(tmp_path):
    p = _zip([(f"f{i}.tex", b"x") for i in range(20)], tmp_path)
    with pytest.raises(ZipImportError, match="too many"):
        _extract(p, tmp_path, max_entries=10)


def test_zip_strips_single_top_folder_and_skips_git(tmp_path):
    p = _zip([("paper/main.tex", b"x"), ("paper/sec/a.tex", b"y"), ("paper/.git/config", b"[core]")], tmp_path)
    res = _extract(p, tmp_path)
    assert sorted(res.files) == ["main.tex", "sec/a.tex"]
    assert any(".git" in s["path"] for s in res.skipped)


def test_import_api_rejects_traversal(alice, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../escape.tex", "x")
    r = alice.post("/api/projects/import", files={"file": ("x.zip", buf.getvalue(), "application/zip")}, data={"name": "bad"})
    assert r.status_code == 400
    assert "unsafe path" in r.json()["detail"]
