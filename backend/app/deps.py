from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuthSession, Membership, Project, User

COOKIE = "prism_session"
ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id,
                       expires_at=datetime.now(UTC) + timedelta(hours=get_settings().session_hours)))
    db.commit()
    return token


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    sess = db.get(AuthSession, token_hash(token))
    if sess is None:
        raise HTTPException(401, "session expired")
    exp = sess.expires_at if sess.expires_at.tzinfo else sess.expires_at.replace(tzinfo=UTC)
    if exp < datetime.now(UTC):
        db.delete(sess)
        db.commit()
        raise HTTPException(401, "session expired")
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(401, "unknown user")
    # CSRF: state-changing requests must carry a custom header, which browsers
    # only send from same-origin scripts (no CORS is enabled).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("x-prism-csrf") != "1":
        raise HTTPException(403, "missing CSRF header")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "administrator only")
    return user


def project_role(db: Session, project_id: str, user: User) -> str | None:
    m = db.execute(select(Membership).where(Membership.project_id == project_id, Membership.user_id == user.id)).scalar_one_or_none()
    if m:
        return m.role
    return "owner" if user.is_admin else None


def get_project_for(db: Session, project_id: str, user: User, min_role: str = "viewer") -> tuple[Project, str]:
    if not project_id.isalnum():
        raise HTTPException(404, "project not found")
    p = db.get(Project, project_id)
    role = project_role(db, project_id, user) if p else None
    if p is None or role is None:
        # Same answer for "missing" and "not yours": do not leak existence.
        raise HTTPException(404, "project not found")
    if ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise HTTPException(403, f"requires {min_role} role")
    return p, role


def user_json(u: User) -> dict:
    return {"id": u.id, "username": u.username, "display_name": u.display_name or u.username, "is_admin": u.is_admin}
