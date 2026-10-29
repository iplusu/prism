"""Administrative commands.

  python -m app.cli create-user <username> [--admin] [--display-name NAME]   (password read from stdin/prompt)
  python -m app.cli llm-test           run the orchestrator LLM connection test, print JSON
  python -m app.cli tools              print external tool health
  python -m app.cli doctor             TeX, sandbox, LLM config, collab, tools at a glance
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("--admin", action="store_true")
    cu.add_argument("--display-name", default="")
    sub.add_parser("llm-test")
    sub.add_parser("tools")
    sub.add_parser("doctor")
    a = ap.parse_args()

    from .main import bootstrap

    bootstrap()
    if a.cmd == "create-user":
        from .db import SessionLocal
        from .models import User
        from .security.passwords import hash_password

        pw = sys.stdin.readline().rstrip("\n") if not sys.stdin.isatty() else getpass.getpass("Password: ")
        if len(pw) < 8:
            sys.exit("password must be at least 8 characters")
        db = SessionLocal()
        try:
            if db.query(User).filter_by(username=a.username).one_or_none():
                sys.exit("user exists")
            db.add(User(username=a.username, display_name=a.display_name or a.username,
                        password_hash=hash_password(pw), is_admin=a.admin))
            db.commit()
        finally:
            db.close()
        print(f"created {a.username}{' (admin)' if a.admin else ''}")
    elif a.cmd == "llm-test":
        from .llm.diagnostics import run_connection_test

        rep = run_connection_test()
        print(json.dumps(rep, indent=2))
        sys.exit(0 if rep["ok"] else 1)
    elif a.cmd == "tools":
        from .adapters.registry import adapters

        print(json.dumps([a_.health(force=True) for a_ in adapters().values()], indent=2))
    elif a.cmd == "doctor":
        from .compile.latex import sandbox_status, tex_status
        from .config import get_settings
        from .routers.system import _collab, _llm_config, _tools

        s = get_settings()
        print(json.dumps({"database": "postgresql" if s.is_postgres else "sqlite", "data_dir": str(s.data_dir),
                          "demo_mode": s.demo_mode, "tex": tex_status(), "compile_sandbox": sandbox_status(),
                          "llm": _llm_config(), "collab": _collab(),
                          "tools": [{k: t[k] for k in ("name", "status", "version", "auth", "detail")} for t in _tools(True)]},
                         indent=2, default=str))


if __name__ == "__main__":
    main()
