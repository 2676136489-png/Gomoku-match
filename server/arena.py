"""对局编排。

把「参战方是什么」和「怎么跑一局」分开：前端只传一份声明式的 spec，
这里负责把它变成能出招的 Player。代码实验室和竞技场共用这一套，
区别只在参战方配置——实验室必定有一方是用户代码，竞技场全是内置算法。
"""

from __future__ import annotations

from typing import Iterator

from . import lab
from .gomoku import algorithms, net, rules

MAX_MOVES = 225


def build(spec: dict, *, label: str | None = None) -> lab.BasePlayer:
    kind = (spec or {}).get("kind", "algorithm")
    if kind == "algorithm":
        algo = spec.get("algorithm", algorithms.DEFAULT)
        if algo not in algorithms.ALGORITHMS:
            raise lab.LabError(f"未知算法 {algo}")
        if algo == "neural" and not net.get_net().ready:
            # 权重缺失时退回贪心等于骗人，直接报错让用户知道要先去训练
            raise lab.LabError("神经网络权重尚未训练，先跑 tools/train_net.py")
        return lab.EnginePlayer(
            algo,
            difficulty=int(spec.get("difficulty", 3) or 3),
            label=label or algorithms.ALGORITHMS[algo]["label"],
        )

    if kind == "code":
        language = spec.get("language", "python")
        if language == "cpp" and not lab.capabilities()["cpp"]["available"]:
            raise lab.LabError(lab.capabilities()["cpp"]["detail"])
        return lab.ProcessPlayer(
            spec.get("source", ""),
            language,
            label=label or "你的代码",
            timeout=float(spec.get("timeout", 2.0) or 2.0),
            avoid_bans=bool(spec.get("avoid_bans", True)),
        )

    raise lab.LabError(f"未知参战方类型 {kind}")


def run(
    left_spec: dict,
    right_spec: dict,
    *,
    rule: str = rules.RENJU,
    difficulty: int = 3,
    max_moves: int = MAX_MOVES,
    max_seconds: float = 90.0,
    opening: str | None = "center",
) -> Iterator[dict]:
    left = build({**left_spec, "difficulty": left_spec.get("difficulty", difficulty)}, label=left_spec.get("label"))
    right = build(
        {**right_spec, "difficulty": right_spec.get("difficulty", difficulty)}, label=right_spec.get("label")
    )
    return lab.play(
        left, right, rule=rule, max_moves=max_moves, max_seconds=max_seconds, opening=opening
    )


def verify(spec: dict) -> dict:
    """只做构建 + 空盘一手，用来在开局前给用户反馈。

    编译失败、语法错误、超时都要在这一步暴露，而不是等对局跑到第 30 手。
    """
    import time

    from .gomoku.position import Position

    started = time.perf_counter()
    player = build(spec)
    try:
        pos = Position(rules.RENJU)
        outcome = player.choose(pos, 1, 1)
        if outcome.error:
            return {
                "ok": False,
                "error": outcome.error,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        return {
            "ok": True,
            "move": {"x": outcome.x, "y": outcome.y},
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "note": outcome.note,
            "log": getattr(player, "compile_log", "")[:2000],
        }
    finally:
        player.close()
