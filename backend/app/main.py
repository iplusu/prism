from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from .config import get_settings
from .db import SessionLocal, engine
from .models import Base, User
from .routers import auth, builds, collab_proxy, complete, files, zotero as zotero_router, internal, projects, proposals, references, system, tasks
from .security.passwords import hash_password

log = logging.getLogger("prism")


def bootstrap() -> None:
    s = get_settings()
    if not s.is_postgres:
        Base.metadata.create_all(engine())  # dev convenience; PostgreSQL uses Alembic migrations
    if s.admin_username and s.admin_password:
        db = SessionLocal()
        try:
            if db.execute(select(User).where(User.username == s.admin_username)).scalar_one_or_none() is None:
                db.add(User(username=s.admin_username, display_name=s.admin_username,
                            password_hash=hash_password(s.admin_password), is_admin=True))
                db.commit()
                log.info("created administrator %s", s.admin_username)
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Prism Research Workspace", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")
    for r in (auth.router, projects.router, files.router, builds.router, tasks.router, proposals.router,
              system.router, internal.router, collab_proxy.router, references.router, complete.router, zotero_router.router):
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"ok": True, "demo_mode": s.demo_mode}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return resp

    dist = s.frontend_dist
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith(("api/", "internal/", "collab/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and str(f).startswith(str(dist.resolve())):
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    return app


app = create_app()
