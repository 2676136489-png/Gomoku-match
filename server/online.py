"""在线对战的服务层：房间、开桌、匹配。

路由和 WebSocket 都要开桌，所以「一桌怎么建」这件事必须只有一个实现。
放在这里而不是路由里，是因为匹配器（跑在 WebSocket 侧）也要调它。
"""

from __future__ import annotations

import random
import secrets
import string
import time
import uuid

from . import repo
from .gomoku import rules
from .gomoku.position import BLACK, WHITE
from .runtime import store

CODE_ALPHABET = string.ascii_uppercase.replace("O", "").replace("I", "") + "23456789"
CODE_LEN = 6
MAX_CLOCK = 1800


def new_code() -> str:
    """房间码。去掉 O/0/I/1 这几个，念给对面听的时候不会争。"""
    for _ in range(20):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        if repo.room_by_code(code) is None:
            return code
    raise RuntimeError("房间码生成失败")


def sanitize_clock(base: int, inc: int) -> tuple[int, int]:
    base = max(30, min(int(base or 300), MAX_CLOCK))
    inc = max(0, min(int(inc or 0), 60))
    return base, inc


def create_room(
    user: dict,
    *,
    rule: str = rules.RENJU,
    base: int = 300,
    inc: int = 5,
    rated: bool = True,
) -> dict:
    base, inc = sanitize_clock(base, inc)
    room = {
        "id": uuid.uuid4().hex[:12],
        "code": new_code(),
        "host_id": user["id"],
        "rule": rule if rule in rules.RULE_MODES else rules.RENJU,
        "clock_base": base,
        "clock_inc": inc,
        "rated": int(rated),
        "status": "open",
        "created_at": time.time(),
    }
    repo.create_room(room)
    return {"room": _room_view(room), "host": {"name": user["name"], "rating": user["rating"]}}


def _room_view(room: dict) -> dict:
    return {
        "id": room["id"],
        "code": room["code"],
        "status": room["status"],
        "rule": room["rule"],
        "clock_base": room["clock_base"],
        "clock_inc": room["clock_inc"],
        "rated": bool(room["rated"]),
        "host_id": room["host_id"],
        "guest_id": room.get("guest_id"),
        "game_id": room.get("game_id"),
        "created_at": room["created_at"],
        "host_name": room.get("host_name"),
        "host_rating": room.get("host_rating"),
    }


def join_room(user: dict, code: str) -> dict:
    """客人进房，顺带把桌开了。

    黑棋随机分：连珠规则下黑棋有禁手约束、先手又有优势，固定房主执黑会让
    「谁建房谁吃亏」变成可观测的偏差。随机分配把这件事摊平。
    """
    room = repo.room_by_code(code.upper())
    if room is None:
        raise LookupError("房间不存在或已关闭")
    if room["host_id"] == user["id"]:
        raise PermissionError("这是你自己的房间")
    if room["status"] != "open":
        raise RuntimeError("房间已经开始了")

    host = repo.user_by_id(room["host_id"])
    if host is None:
        raise LookupError("房主账户不存在")

    black, white = (host, user) if random.random() < 0.5 else (user, host)
    game = _create_game(
        black_id=black["id"],
        white_id=white["id"],
        rule=room["rule"],
        base=room["clock_base"],
        inc=room["clock_inc"],
        rated=bool(room["rated"]),
        room_id=room["id"],
    )
    repo.update_room(room["id"], guest_id=user["id"], game_id=game.id, status="playing")
    fresh = repo.room_by_id(room["id"]) or room
    return {
        "room": _room_view(fresh),
        "game_id": game.id,
        "seat": BLACK if black["id"] == user["id"] else WHITE,
        "players": {
            "black": {"name": black["name"], "rating": black["rating"]},
            "white": {"name": white["name"], "rating": white["rating"]},
        },
    }


def create_match(a: dict, b: dict, *, rule: str = rules.RENJU, base: int = 300, inc: int = 5) -> dict:
    black, white = (a, b) if random.random() < 0.5 else (b, a)
    game = _create_game(
        black_id=black["id"],
        white_id=white["id"],
        rule=rule,
        base=base,
        inc=inc,
        rated=True,
        room_id=None,
    )
    return {
        "game_id": game.id,
        "players": {
            BLACK: {"name": black["name"], "rating": black["rating"], "id": black["id"]},
            WHITE: {"name": white["name"], "rating": white["rating"], "id": white["id"]},
        },
    }


def _create_game(*, black_id, white_id, rule, base, inc, rated, room_id):
    return store.create(
        mode="online",
        difficulty=3,
        persona="balanced",
        human_color=BLACK,
        use_llm=False,
        rule=rule,
        rated=rated,
        black_id=black_id,
        white_id=white_id,
        owner_id=black_id,
        room_id=room_id,
        clock_base=base,
        clock_inc=inc,
    )


def close_room(room_id: str, user_id: int) -> None:
    room = repo.room_by_id(room_id)
    if room is None:
        return
    if room["host_id"] != user_id:
        raise PermissionError("只有房主能关闭房间")
    repo.update_room(room_id, status="closed")


def lobby(limit: int = 30) -> dict:
    repo.reap_rooms()
    rows = repo.list_rooms("open", limit)
    return {"rooms": [_room_view(r) for r in rows]}
