"""路由公共依赖：鉴权、对局归属校验、错误映射。"""

from __future__ import annotations

from fastapi import HTTPException, Request

from .. import repo
from ..store import Game
from ..runtime import accounts


def bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        return token or None
    return None


def token_user(request: Request) -> dict | None:
    """按 token 取账户行。不做异常——公开接口和需要鉴权的接口都要用它。"""
    user_id = accounts.resolve_id(bearer(request))
    if user_id is None:
        return None
    return repo.user_by_id(user_id)


def require_user(request: Request) -> dict:
    user = token_user(request)
    if user is None:
        raise HTTPException(401, "登录已失效，请重新登录")
    return user


def require_game(game_id: str, user: dict) -> Game:
    """取一局，并校验这个人有权看它。

    上一版对局只活在内存里、活不过重启，所以「知道 id 就能读」看起来没什么损失。
    现在棋谱会落库并长期保留，缺了这一步，任何人拿到 id 就能读别人的棋谱和
    对局过程中的分析。所以归属校验不是可选项。
    """
    game = accounts_game(game_id)
    if game is None:
        raise HTTPException(404, "对局不存在")
    owners = {game.owner_id, game.black_id, game.white_id}
    if user["id"] not in owners:
        raise HTTPException(403, "无权访问该对局")
    return game


def accounts_game(game_id: str):
    from ..runtime import store

    return store.get(game_id)
