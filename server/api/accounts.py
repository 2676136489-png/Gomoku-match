"""账户与会话路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, elo, repo
from ..accounts import AuthError
from ..runtime import accounts, store
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
    return {
        "user": accounts.public(user["name"]),
        "persistent": accounts.persistent,
        "elo": elo.describe(),
    }


@router.post("/auth/password")
def change_password(req: PasswordRequest, request: Request) -> dict:
    user = require_user(request)
    try:
        token = accounts.change_password(user["name"], req.old, req.new)
    except AuthError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return {"ok": True, "token": token}


@router.get("/leaderboard")
def leaderboard(limit: int = 20) -> dict:
    rows = accounts.leaderboard(max(1, min(limit, 100)))
    return {
        "rows": rows,
        "accounts": accounts.count(),
        "board": repo.rating_board_stats(),
        "metrics": db.info()["engine"],
    }


@router.get("/history")
def history(request: Request, limit: int = 20, offset: int = 0) -> dict:
    """棋谱列表。带 mode / result / 对手 / 手数 / 时间，前端点进去能回放。"""
    user = require_user(request)
    limit = max(1, min(limit, 100))
    rows = repo.games_of_user(user["id"], limit=limit, offset=max(0, offset))
    out = []
    for row in rows:
        human = 1 if row["black_id"] == user["id"] else 2
        if row["winner"] is None:
            result = "draw"
        else:
            result = "win" if row["winner"] == human else "loss"
        opponent = "机器"
        if row["mode"] in ("online", "pvp"):
            other_id = row["white_id"] if human == 1 else row["black_id"]
            other = repo.user_by_id(other_id) if other_id else None
            opponent = other["name"] if other else "对手"
        out.append(
            {
                "id": row["id"],
                "mode": row["mode"],
                "result": result if row["status"] in ("finished", "abandoned") else "live",
                "status": row["status"],
                "opponent": opponent,
                "moves": row["ply_count"] or row["moves_count"],
                "at": row["finished_at"] or row["created_at"],
                "rated": bool(row["rated"]),
                "rating_delta": (
                    round((row["rating_after"] or 0) - (row["rating_before"] or 0), 1)
                    if row["rated"] and row["rating_before"] is not None
                    else None
                ),
                "reason": row["result_reason"],
            }
        )
    return {"rows": out, "offset": offset, "limit": limit}


@router.get("/profile/{name}")
def profile(name: str) -> dict:
    """公开主页：段位、排名、数据、最近棋谱。天梯点名字就跳这儿。"""
    user = repo.user_by_name(name)
    if user is None:
        raise HTTPException(404, "账户不存在")
    view = accounts.public(user["name"])
    view["rank"] = repo.rank_of(user["id"])
    view["tier"] = elo.tier(user["rating"])
    return {"user": view}
