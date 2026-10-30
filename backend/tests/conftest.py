"""Test fixtures.

Every test session gets its own SQLite database and data directory. TeX is the
real TeX Live install (TEX_BIN_DIR, auto-detected); compilation really runs in
bwrap. The LLM server and the CLI tools used in agent tests are *test fakes*
(see fake_llm.py / fake_cli.py) — tests that touch the real CLIs are in
test_real_cli.py and say so.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="prism-test-"))
os.environ.update({
    # never read the deployment's .env: tests must not inherit its settings or secrets
    "PRISM_ENV_FILE": os.devnull,
    "DATABASE_URL": os.environ.get("PRISM_TEST_DATABASE_URL") or f"sqlite:///{_TMP / 'test.db'}",
    "PRISM_DATA_DIR": str(_TMP / "data"),
    "PRISM_SECRET_KEY": "test-secret-key-0123456789",
    "COMPILE_SANDBOX": "bwrap",
    "TOOL_SANDBOX": "bwrap",
    "TEX_PACKAGE_AUTO_INSTALL": "false",  # the suite must never run the real tlmgr; the tests that cover it turn it on with a fake
    "COLLAB_ENABLED": "false",
    "DEMO_MODE": "false",
    # settings a deployment (or a sourced .env in the calling shell) may have changed
    "PRISM_COOKIE_SECURE": "false",  # the test client speaks plain HTTP
    "PRISM_ALLOW_SIGNUP": "false",
    "PRISM_ADMIN_USERNAME": "",
    "PRISM_ADMIN_PASSWORD": "",
    "LLM_API_KEY": "",
    "LLM_TIMEOUT_SECONDS": "30",
    "LLM_BASE_URL": "",
    "LLM_MODEL": "",
    "AGENT_RUN_TIMEOUT_SECONDS": "60",
    "AGENT_TASK_TIMEOUT_SECONDS": "120",
    "COMPILE_TIMEOUT_SECONDS": "120",
    "PRISM_FRONTEND_DIST": str(_TMP / "no-dist"),
})
if not os.environ.get("TEX_BIN_DIR"):
    for cand in (Path.home() / ".TinyTeX/bin/x86_64-linux", Path("/usr/bin")):
        if (cand / "latexmk").exists():
            os.environ["TEX_BIN_DIR"] = str(cand)
            break

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base, User  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[2] / "sample" / "paper"


def set_env(**kw) -> None:
    """Change settings for one test (settings are cached)."""
    for k, v in kw.items():
        os.environ[k] = str(v)
    get_settings.cache_clear()
    s = get_settings()
    return s


@pytest.fixture(autouse=True)
def _reset_settings():
    saved = dict(os.environ)
    get_settings.cache_clear()
    yield
    os.environ.clear()
    os.environ.update(saved)
    get_settings.cache_clear()
    from app.adapters.registry import clear_overrides

    clear_overrides()


@pytest.fixture(scope="session", autouse=True)
def _db():
    if get_settings().is_postgres:
        # Fresh schema built by the real Alembic migrations.
        from alembic import command
        from alembic.config import Config

        Base.metadata.drop_all(engine())
        with engine().begin() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
        cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
        command.upgrade(cfg, "head")
    else:
        Base.metadata.create_all(engine())
    yield
    shutil.rmtree(_TMP, ignore_errors=True)


def make_user(username: str, admin: bool = False) -> User:
    db = SessionLocal()
    try:
        u = db.query(User).filter_by(username=username).one_or_none()
        if u is None:
            u = User(username=username, display_name=username.title(), password_hash=hash_password("password123"),
                     is_admin=admin)
            db.add(u)
            db.commit()
        return u
    finally:
        db.close()


class Client(TestClient):
    """TestClient that sends the CSRF header like the real frontend."""

    def request(self, method, url, **kw):
        headers = dict(kw.pop("headers", None) or {})
        headers.setdefault("X-Prism-CSRF", "1")
        return super().request(method, url, headers=headers, **kw)


def login(username: str, admin: bool = False) -> Client:
    from app.main import create_app

    make_user(username, admin)
    c = Client(create_app())
    r = c.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def alice() -> Client:
    return login("alice")


@pytest.fixture
def bob() -> Client:
    return login("bob")


def new_sample_project(c: Client, name: str = "Sample") -> dict:
    r = c.post("/api/projects", json={"name": name, "template": "sample"})
    assert r.status_code == 200, r.text
    return r.json()


def drain() -> int:
    from app import worker

    return worker.drain()


def read_text(c: Client, pid: str, path: str) -> dict:
    r = c.get(f"/api/projects/{pid}/files/content", params={"path": path})
    assert r.status_code == 200, r.text
    return r.json()


def save_text(c: Client, pid: str, path: str, text: str, base_sha: str | None):
    return c.put(f"/api/projects/{pid}/files/content", json={"path": path, "text": text, "base_sha": base_sha})
