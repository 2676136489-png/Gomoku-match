"""终局结算。

单独抽一个模块，因为「一局棋结束该发生什么」有好几个触发点：最后一手落下、
认输、禁手判负。这几个入口各自去写一遍「记战绩 + 落库」，迟早会出现某条路径
漏了记。

幂等由 `game.recorded` 保证：结算本身会被多个触发点接近着调用（比如落子判出
胜负之后客户端又补了一次请求），第二次进来直接返回 None。
"""

from __future__ import annotations

from . import repo
from .runtime import accounts, store


def settle(game) -> dict | None:
    with game.lock:
        if game.status != "finished" or game.recorded:
            return None
        game.recorded = True

    # 没有账户的对局（脚本跑出来的实验局）只落库，不记战绩
    if not game.owner_id:
        store.finish(game)
        return None

    if game.winner is None:
        result = "draw"
    elif game.winner == game.human_color:
        result = "win"
    else:
        result = "loss"

    try:
        accounts.record(_name_of(game.owner_id) or "", result=result)
    except Exception:
        pass
    store.finish(game)
    return {"result": result, "human_color": game.human_color, "winner": game.winner}


def _name_of(user_id: int | None) -> str | None:
    if not user_id:
        return None
    user = repo.user_by_id(user_id)
    return user["name"] if user else None
