"""Botzone 协议适配。

这个项目最早是给 Botzone 写的比赛 bot，改造成 Web 应用之后没有把那条路线扔掉：
同一个引擎换个入口就能接受比赛服务器的调度。

换手规则也只在这一层保留。网页版单人玩不需要它——那个规则解决的是
"先手方优势过大"的平衡问题，不是游戏本身的玩法。
"""

from __future__ import annotations

from .gomoku import rules
from .gomoku.position import BLACK, SIZE, WHITE, Position, coord
from .gomoku.search import Searcher

# 黑棋首子落在天元附近的星位区，先手优势明显，必须换手
STAR_LOW, STAR_HIGH = 3, 11


def _place(pos: Position, x: int, y: int, color: int) -> None:
    if 0 <= x < SIZE and 0 <= y < SIZE:
        pos.place(y * SIZE + x, color)


def _should_swap(pos: Position, x: int, y: int) -> bool:
    if STAR_LOW <= x <= STAR_HIGH and STAR_LOW <= y <= STAR_HIGH:
        return True
    return pos.point_value(y * SIZE + x, BLACK) > 666


def play(payload: dict, time_limit: float = 1.5) -> dict:
    requests = payload.get("requests") or []
    responses = payload.get("responses") or []
    if not requests:
        return {"response": {"x": 7, "y": 7}}

    pos = Position(rules.FREESTYLE)  # Botzone 的五子棋没有禁手，别把比赛规则搞混
    mine, theirs = WHITE, BLACK
    turn_id = len(responses)

    for i in range(turn_id):
        ox, oy = int(requests[i]["x"]), int(requests[i]["y"])
        mx, my = int(responses[i]["x"]), int(responses[i]["y"])
        _place(pos, ox, oy, theirs)
        if (ox == -1 and oy == -1) or (mx == -1 and my == -1):
            mine, theirs = theirs, mine
        _place(pos, mx, my, mine)

    ox, oy = int(requests[turn_id]["x"]), int(requests[turn_id]["y"])
    if ox == -1 and oy == -1:
        mine, theirs = theirs, mine
    _place(pos, ox, oy, theirs)

    if ox >= 0 and turn_id == 0 and _should_swap(pos, ox, oy):
        return {"response": {"x": -1, "y": -1}}

    searcher = Searcher(time_limit=time_limit, max_depth=5)
    result = searcher.search(pos, mine)
    x, y = coord(result.move)
    return {"response": {"x": x, "y": y}}
