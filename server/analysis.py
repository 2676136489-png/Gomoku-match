"""分析层：把 insight 的纯函数接到对局对象上。

分开写是因为 insight 里全是纯函数——给定棋盘就能算，不依赖对局、缓存和 HTTP。
对局状态、增量曲线、缓存失效这些耦合都集中在这里，改接口不会牵动算法。

胜率曲线的缓存只补新增的棋步。一场七十三手的对局里，如果要每次都从头重放，
第一帧就得花掉好几百毫秒；增量之后每落一手只多算一次扫盘。
"""

from __future__ import annotations

import time

from .gomoku import insight
from .gomoku.position import BLACK, OPPONENT, Position

# 对局结束后黑棋的胜率就是结果本身，不再走模型
WIN, DRAW = 1.0, 0.5


def series(game) -> list[dict]:
    """每手之后的黑棋胜率。统一按黑棋视角存，前端画曲线时不用跟着换手翻符号。"""
    cache: list[dict] = game.series_cache
    pos: Position | None = game.series_pos

    if pos is None or len(cache) > len(game.moves):
        # 撤销过，或者还没建过缓存，整盘重来
        cache.clear()
        pos = Position(rule=game.rule)
        game.series_pos = pos

    finished_at = len(game.moves) - 1 if game.status == "finished" else -1

    for i in range(len(cache), len(game.moves)):
        move = game.moves[i]
        pos.place(move["y"] * insight.SIZE + move["x"], move["color"])

        if i == finished_at:
            if game.winner is None:
                p = DRAW
            else:
                p = WIN if game.winner == BLACK else 1.0 - WIN
        else:
            p = insight.winrate(pos, BLACK, OPPONENT[move["color"]])["p"]

        cache.append({"ply": i + 1, "black": round(p, 4), "x": move["x"], "y": move["y"]})

    return cache


def payload(game, *, with_series: bool = True) -> dict:
    """分析面板一次要的全部数据。

    前端每落一手刷一次，所以这里的计算量要压在二十毫秒内：
    一遍扫盘（双方威胁）、两次模型推理、一遍排序、一遍普查。
    """
    started = time.perf_counter()
    turn = game.turn
    human = game.human_color
    info = insight.scan(game.pos)

    mine = insight.winrate(game.pos, human, turn, info=info)
    theirs = insight.winrate(game.pos, OPPONENT[human], turn, info=info)

    playing = game.status == "playing"
    if playing:
        # 预测的是「对手下一步会落在哪」，不是「你自己会落在哪」。
        # 非双人局里对手就是 Agent（人间隔一手轮到它），双人局只能预测轮到的那一方。
        predictor = turn if game.mode == "pvp" else OPPONENT[human]
        prediction = insight.predict(game.pos, predictor)
        prediction["side"] = predictor
    else:
        prediction = {"points": [], "considered": 0, "net_used": False, "side": None}

    out = {
        "turn": turn,
        "status": game.status,
        "human_color": human,
        "step": game.step,
        "has_pending": game.pending is not None,
        "winrate": {
            "human": mine["p"],
            "opponent": theirs["p"],
            "side": human,
            "confidence": mine["confidence"],
            "confidence_note": mine["confidence_note"],
            "reason": mine["reason"],
            "source": mine["source"],
            "facts": mine["facts"],
        },
        "prediction": prediction,
        "census": insight.census(game.pos),
        "model": insight.get_model().describe(),
    }
    if with_series:
        out["series"] = series(game)
    out["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return out


def explain_move(game, index: int | None = None) -> dict:
    """解释某一手。不给 index 就解释最后一手。"""
    if not game.moves:
        raise IndexError("对局还没有落子")
    idx = len(game.moves) - 1 if index is None else index
    return insight.explain(game.moves, idx, game.rule)


def pending_preview(game) -> dict | None:
    """解说还没落下的一手。单步模式下回答「它打算下这里，为什么」。"""
    if game.pending is None:
        return None
    pending = game.pending
    meta = {
        "source": pending.get("source", "pending"),
        "comment": pending.get("comment", ""),
        "think_ms": pending.get("think_ms", 0),
    }
    return insight.preview(game.moves, pending["idx"], pending["color"], game.rule, meta)
