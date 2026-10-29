from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import COOKIE, create_session, current_user, require_admin, token_hash, user_json
from ..models import AuthSession, User
from ..security.passwords import hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])
_USERNAME = re.compile(r"^[a-zA-Z0-9_.\-]{2,64}$")


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=256)
    display_name: str = ""
    is_admin: bool = False


class UserPatch(BaseModel):
    display_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    is_admin: bool | None = None


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.cookie_secure,
                    max_age=s.session_hours * 3600, path="/")


@router.post("/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    u = db.execute(select(User).where(User.username == body.username)).scalar_one_or_none()
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "invalid username or password")
    _set_cookie(response, create_session(db, u))
    return user_json(u)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    tok = request.cookies.get(COOKIE)
    if tok:
        s = db.get(AuthSession, token_hash(tok))
        if s:
            db.delete(s)
            db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(current_user)):
    return user_json(user)


@router.get("/auth/config")
def auth_config():
    s = get_settings()
    return {"allow_signup": s.allow_signup, "demo_mode": s.demo_mode}


@router.post("/auth/signup")
def signup(body: UserIn, response: Response, db: Session = Depends(get_db)):
    if not get_settings().allow_signup:
        raise HTTPException(403, "sign-up is disabled; ask an administrator for an account")
    u = _create_user(db, body.model_copy(update={"is_admin": False}))
    _set_cookie(response, create_session(db, u))
    return user_json(u)


def _create_user(db: Session, body: UserIn) -> User:
    if not _USERNAME.match(body.username):
        raise HTTPException(400, "username may contain letters, digits, '.', '_' and '-'")
    if db.execute(select(User).where(User.username == body.username)).scalar_one_or_none():
        raise HTTPException(409, "username already exists")
    u = User(username=body.username, display_name=body.display_name or body.username,
             password_hash=hash_password(body.password), is_admin=body.is_admin)
    db.add(u)
    db.commit()
    return u


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [user_json(u) for u in db.execute(select(User).order_by(User.username)).scalars()]


@router.post("/users")
def create_user(body: UserIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return user_json(_create_user(db, body))


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    target = db.get(User, uid)
    if target is None:
        raise HTTPException(404, "user not found")
    if target.id != user.id and not user.is_admin:
        raise HTTPException(403, "administrator only")
    if body.is_admin is not None:
        if not user.is_admin:
            raise HTTPException(403, "administrator only")
        target.is_admin = body.is_admin
    if body.display_name is not None:
        target.display_name = body.display_name[:128]
    if body.password:
        target.password_hash = hash_password(body.password)
    db.commit()
    return user_json(target)
