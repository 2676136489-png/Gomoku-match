"""账户与会话路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import repo
from ..accounts import AuthError
from ..runtime import accounts
from .deps import bearer, require_user

router = APIRouter(prefix="/api", tags=["account"])


class AuthRequest(BaseModel):
    name: str
    password: str


class PasswordRequest(BaseModel):
    old: str
    new: str


@router.post("/auth/register")
def register(req: AuthRequest) -> dict:
    try:
        user, token = accounts.register(req.name, req.password)
    except AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"user": user, "token": token}


@router.post("/auth/login")
def login(req: AuthRequest) -> dict:
    try:
        user, token = accounts.login(req.name, req.password)
    except AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"user": user, "token": token}


@router.post("/auth/logout")
def logout(request: Request) -> dict:
    accounts.logout(bearer(request))
    return {"ok": True}


@router.get("/auth/me")
def me(request: Request) -> dict:
    user = require_user(request)
    return {"user": accounts.public(user["name"]), "persistent": accounts.persistent}


@router.post("/auth/password")
def change_password(req: PasswordRequest, request: Request) -> dict:
    user = require_user(request)
    try:
        token = accounts.change_password(user["name"], req.old, req.new)
    except AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"ok": True, "token": token}


@router.get("/history")
def history(request: Request, limit: int = 20, offset: int = 0) -> dict:
    """棋谱列表。带模式 / 结果 / 手数 / 时间，前端点进去能回放。"""
    user = require_user(request)
    limit = max(1, min(limit, 100))
    rows = repo.games_of_user(user["id"], limit=limit, offset=max(0, offset))
    out = []
    for row in rows:
        if row["winner"] is None:
            result = "draw"
        elif row["winner"] == row["human_color"]:
            result = "win"
        else:
            result = "loss"
        out.append(
            {
                "id": row["id"],
                "mode": row["mode"],
                "result": result if row["status"] in ("finished", "abandoned") else "live",
                "status": row["status"],
                "opponent": f"难度 {row['difficulty']} · {row['persona']}",
                "moves": row["ply_count"] or row["moves_count"],
                "at": row["finished_at"] or row["created_at"],
                "reason": row["result_reason"],
            }
        )
    return {"rows": out, "offset": offset, "limit": limit}


@router.get("/profile/{name}")
def profile(name: str) -> dict:
    user = repo.user_by_name(name)
    if user is None:
        raise HTTPException(404, "账户不存在")
    return {"user": accounts.public(user["name"])}
