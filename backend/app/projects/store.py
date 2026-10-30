"""On-disk project storage.

Layout:  <data>/projects/<pid>/repo   working tree + .git (history)
                                /lock   inter-process lock file

The working tree mirrors the live manuscript. When collaboration is enabled
the collab service is authoritative for text files and writes through to this
tree; see `documents.py`. Git is used only for history (commits of
snapshots); the working tree is never overwritten by `git checkout`.
"""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from contextlib import contextmanager
from pathlib import Path

from ..config import get_settings
from ..security.paths import PathError, normalize_rel_path, safe_join

TEXT_EXTENSIONS = {
    ".tex", ".bib", ".sty", ".cls", ".bst", ".bbx", ".cbx", ".lbx", ".dtx", ".ins", ".txt", ".md",
    ".cfg", ".def", ".tikz", ".pgf", ".csv", ".tsv", ".dat", ".json", ".yaml", ".yml", ".ltx", ".clo",
    ".fd", ".ist", ".gst", ".latex", ".rnw", ".bbl", ".py", ".r", ".m", ".gp", ".svg", ".xml",
}


class StoreError(RuntimeError):
    pass


def projects_root() -> Path:
    return get_settings().data_dir / "projects"


def project_dir(pid: str) -> Path:
    if not pid.isalnum():
        raise StoreError("invalid project id")
    return projects_root() / pid


def repo_dir(pid: str) -> Path:
    return project_dir(pid) / "repo"


@contextmanager
def project_lock(pid: str):
    """Exclusive inter-process lock for mutating a project's working tree."""
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "lock", "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_text(path: str, data: bytes | None = None) -> bool:
    ext = os.path.splitext(path)[1].lower()
    if ext not in TEXT_EXTENSIONS:
        return False
    if data is None:
        return True
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# --------------------------------------------------------------------- git ---

def _git_env() -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "HOME": str(get_settings().data_dir),
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    return env


_GIT_SAFE = [
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
    "-c", "core.autocrlf=false", "-c", "core.symlinks=false", "-c", "commit.gpgsign=false",
    "-c", "user.name=Prism", "-c", "user.email=prism@localhost",
]


def git(pid: str, *args: str, input: bytes | None = None, check: bool = True, env_extra: dict | None = None) -> bytes:
    env = _git_env()
    if env_extra:
        env.update(env_extra)
    r = subprocess.run(
        ["git", *_GIT_SAFE, *args], cwd=repo_dir(pid), input=input, capture_output=True, env=env, timeout=120
    )
    if check and r.returncode != 0:
        raise StoreError(f"git {args[0]} failed: {r.stderr.decode(errors='replace')[:500]}")
    return r.stdout


def init_project(pid: str, files: dict[str, bytes] | None = None, source_dir: Path | None = None,
                 author: str = "Prism") -> str:
    rd = repo_dir(pid)
    rd.mkdir(parents=True, exist_ok=True)
    git(pid, "init", "-q", "-b", "main")
    # A local exclude keeps build products out of history.
    (rd / ".git" / "info").mkdir(parents=True, exist_ok=True)
    (rd / ".git" / "info" / "exclude").write_text("*.aux\n*.log\n*.fls\n*.fdb_latexmk\n*.synctex.gz\n")
    if source_dir is not None:
        for p in source_dir.rglob("*"):
            if p.is_symlink():
                continue
            if p.is_file():
                rel = p.relative_to(source_dir).as_posix()
                if rel.split("/", 1)[0] == ".git":
                    continue
                dst = rd / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(p, dst)
    for rel, data in (files or {}).items():
        write_file(pid, rel, data)
    return commit(pid, "Initial import", author)


def commit(pid: str, message: str, author: str = "Prism") -> str:
    git(pid, "add", "-A")
    status = git(pid, "status", "--porcelain")
    if status.strip() or not head(pid):
        env = {"GIT_AUTHOR_NAME": author or "Prism", "GIT_AUTHOR_EMAIL": "prism@localhost",
               "GIT_COMMITTER_NAME": "Prism", "GIT_COMMITTER_EMAIL": "prism@localhost"}
        git(pid, "commit", "-q", "--allow-empty", "-m", message, env_extra=env)
    return head(pid)


def head(pid: str) -> str:
    out = git(pid, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return out.decode().strip()


def log(pid: str, limit: int = 100) -> list[dict]:
    if not head(pid):
        return []
    out = git(pid, "log", f"-n{limit}", "--format=%H%x1f%an%x1f%aI%x1f%s%x1e").decode()
    res = []
    for rec in out.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        h, an, date, subj = rec.split("\x1f")
        res.append({"revision": h, "author": an, "date": date, "message": subj})
    return res


def files_at(pid: str, rev: str) -> dict[str, bytes]:
    if not rev or not all(c in "0123456789abcdef" for c in rev):
        raise StoreError("invalid revision")
    out: dict[str, bytes] = {}
    if not git(pid, "ls-tree", "-r", "--name-only", rev).strip():
        return out  # a commit with no files: `git archive` yields a tar without members, which tarfile rejects
    data = git(pid, "archive", "--format=tar", rev)
    with tarfile.open(fileobj=io.BytesIO(data)) as tf:
        for m in tf.getmembers():
            if m.isfile():
                f = tf.extractfile(m)
                if f is not None:
                    out[m.name] = f.read()
    return out


def revision_exists(pid: str, rev: str) -> bool:
    if not rev or not all(c in "0123456789abcdef" for c in rev):
        return False
    r = subprocess.run(["git", *_GIT_SAFE, "cat-file", "-e", f"{rev}^{{commit}}"], cwd=repo_dir(pid),
                       env=_git_env(), capture_output=True)
    return r.returncode == 0


# ------------------------------------------------------------- working tree ---

def list_files(pid: str) -> list[dict]:
    rd = repo_dir(pid)
    res = []
    for root, dirs, files in os.walk(rd):
        dirs[:] = [d for d in dirs if d != ".git" and not os.path.islink(os.path.join(root, d))]
        for fn in files:
            full = Path(root) / fn
            if full.is_symlink():
                continue
            rel = full.relative_to(rd).as_posix()
            st = full.stat()
            res.append({"path": rel, "size": st.st_size, "is_text": is_text(rel), "mtime": st.st_mtime})
    res.sort(key=lambda f: f["path"])
    return res


def read_all(pid: str) -> dict[str, bytes]:
    rd = repo_dir(pid)
    return {f["path"]: (rd / f["path"]).read_bytes() for f in list_files(pid)}


def read_file(pid: str, rel: str) -> bytes:
    p = safe_join(repo_dir(pid), rel)
    if not p.is_file():
        raise FileNotFoundError(rel)
    return p.read_bytes()


def write_file(pid: str, rel: str, data: bytes) -> None:
    p = safe_join(repo_dir(pid), rel)
    if p.is_dir():
        raise PathError("a directory exists at that path")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.prism-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def delete_file(pid: str, rel: str) -> None:
    p = safe_join(repo_dir(pid), rel)
    if p.is_file():
        p.unlink()
    _prune_empty_dirs(repo_dir(pid), p.parent)


def _prune_empty_dirs(root: Path, d: Path) -> None:
    root = root.resolve()
    d = d.resolve()
    while d != root and str(d).startswith(str(root)) and d.is_dir() and not any(d.iterdir()):
        d.rmdir()
        d = d.parent


def sync_tree(pid: str, files: dict[str, bytes]) -> None:
    """Make the working tree equal to `files` (used after snapshotting the live
    collaborative state)."""
    current = {f["path"] for f in list_files(pid)}
    for rel, data in files.items():
        p = safe_join(repo_dir(pid), rel)
        if not p.is_file() or p.read_bytes() != data:
            write_file(pid, rel, data)
    for rel in current - set(files):
        delete_file(pid, rel)


def tree_hash(files: dict[str, bytes]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode() + b"\0" + sha256(files[rel]).encode() + b"\n")
    return h.hexdigest()


def delete_project(pid: str) -> None:
    shutil.rmtree(project_dir(pid), ignore_errors=True)


def normalize(rel: str) -> str:
    return normalize_rel_path(rel)
