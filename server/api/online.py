"""在线对战 REST 部分。落子走 WebSocket，这里只负责「凑桌」。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import online, realtime, repo
from ..gomoku import rules
from .deps import require_user

router = APIRouter(prefix="/api", tags=["online"])


class RoomRequest(BaseModel):
    rule: str = Field(default=rules.RENJU, pattern="^(renju|freestyle)$")
    clock_base: int = Field(default=300, ge=30, le=1800)
    clock_inc: int = Field(default=5, ge=0, le=60)
    rated: bool = True


@router.get("/rooms")
def list_rooms(request: Request, limit: int = 30) -> dict:
    require_user(request)
    return online.lobby(max(1, min(limit, 60)))


@router.post("/rooms")
def create_room(req: RoomRequest, request: Request) -> dict:
    user = require_user(request)
    return online.create_room(
        user, rule=req.rule, base=req.clock_base, inc=req.clock_inc, rated=req.rated
    )


@router.get("/rooms/{code}")
def peek_room(code: str, request: Request) -> dict:
    require_user(request)
    room = repo.room_by_code(code.upper())
    if room is None:
        raise HTTPException(404, "房间不存在或已关闭")
    game = repo.game_by_id(room.get("game_id") or "")
    return {
        "room": online._room_view(room),
        "started": room["status"] == "playing",
        "game_id": game["id"] if game else None,
    }


@router.post("/rooms/{code}/join")
def join_room(code: str, request: Request) -> dict:
    user = require_user(request)
    try:
        return online.join_room(user, code)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/rooms/{room_id}")
def close_room(room_id: str, request: Request) -> dict:
    user = require_user(request)
    try:
        online.close_room(room_id, user["id"])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"ok": True}


@router.get("/online/stats")
def online_stats(request: Request) -> dict:
    user = require_user(request)
    return {
        "you": {"name": user["name"], "rating": user["rating"]},
        **realtime.stats(),
        "queue": realtime.hub.queue_size(),
    }
