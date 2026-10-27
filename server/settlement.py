"""终局结算。

单独抽一个模块，因为「一局棋结束该发生什么」有好几个触发点：最后一手落下、
认输、超时、对手掉线判负。这四个入口各自去写一遍「记战绩 + 算分 + 落库」，
迟早会出现某条路径漏了算分。

幂等由 `game.recorded` 保证：结算过程本身会调用多次（比如超时判负之后客户端
又补了一手），第二次进来直接返回 None。
"""

from __future__ import annotations

from . import repo
from .gomoku.position import BLACK, WHITE
from .runtime import accounts, store


def settle(game) -> dict | None:
    with game.lock:
        if game.status != "finished" or game.recorded:
            return None
        game.recorded = True

    if game.mode == "online":
        return _settle_online(game)
    return _settle_solo(game)


def _settle_solo(game) -> dict | None:
    """人机 / 同屏双人。只有人类那一侧的胜负进统计。"""
    if not game.owner_id or game.mode == "pvp":
        store.finish(game)
        return None

    if game.winner is None:
        result = "draw"
    elif game.winner == game.human_color:
        result = "win"
    else:
        result = "loss"

    try:
        accounts.record(
            _name_of(game.owner_id) or "",
            mode=game.mode,
            result=result,
            moves=len(game.moves),
            opponent=f"{game.difficulty}·{game.persona}",
            meta={"rule": game.rule, "reason": game.result_reason},
        )
    except Exception:
        pass
    store.finish(game)
    return {"rated": False, "result": result}


def _settle_online(game) -> dict | None:
    """联网对局：双方都记战绩，rated 的还要算天梯分。"""
    black_id, white_id = game.black_id, game.white_id
    if not black_id or not white_id:
        store.finish(game)
        return None

    draw = game.winner is None
    if draw:
        winner_id = None
    else:
        winner_id = black_id if game.winner == BLACK else white_id
    loser_id = white_id if winner_id == black_id else black_id

    rating = {"rated": False}
    before = after = None
    if game.rated:
        try:
            rating = accounts.settle_rated(winner_id, loser_id, draw=draw)
            seat = rating.get("black") or {}
            before, after = seat.get("before"), seat.get("after")
        except Exception:
            rating = {"rated": False, "reason": "结算失败"}

    for user_id, outcome in (
        (black_id, "draw" if draw else ("win" if winner_id == black_id else "loss")),
        (white_id, "draw" if draw else ("win" if winner_id == white_id else "loss")),
    ):
        try:
            accounts.record(
                _name_of(user_id) or "",
                mode="online",
                result=outcome,
                moves=len(game.moves),
                opponent=_name_of(loser_id if winner_id != user_id else winner_id) or "",
                meta={"rule": game.rule, "reason": game.result_reason},
            )
        except Exception:
            pass

    store.finish(game, rated=bool(rating.get("rated")), rating_before=before, rating_after=after)
    rating["result"] = {
        "winner": None if draw else (BLACK if winner_id == black_id else WHITE),
        "reason": game.result_reason,
    }
    return rating


def _name_of(user_id: int | None) -> str | None:
    if not user_id:
        return None
    user = repo.user_by_id(user_id)
    return user["name"] if user else None
