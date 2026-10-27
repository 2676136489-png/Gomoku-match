"""诊断：落点网络到底漏掉了什么。

核心问题不是「网络分准不准」，而是「网络圈出的点里有没有搜索真正想走的那个」。
所以这里拿全量深度 5 搜索的最优手当标准答案，看它在不在网络 Top-K 里。

    .venv/Scripts/python.exe tools/diagnose_prior.py -n 16
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.gomoku import net, rules  # noqa: E402
from server.gomoku.position import OPPONENT, Position  # noqa: E402
from server.gomoku.search import Searcher  # noqa: E402

TOPK = 12


def static_rank(pos: Position, color: int) -> list[int]:
    """和 Searcher.rank_moves 同口径的静态排序。"""
    opp = OPPONENT[color]
    spots = pos.candidate_list()
    scored = []
    for idx in spots:
        attack = pos.point_value(idx, color)
        defence = pos.point_value(idx, opp)
        x, y = idx % 15, idx // 15
        score = attack * 10 + defence * 9 + pos.neighbor[idx] * 3 + (14 - (abs(x - 7) + abs(y - 7)))
        scored.append((score, idx))
    scored.sort(reverse=True)
    return [i for _, i in scored]


def net_rank(pos: Position, color: int) -> list[int]:
    model = net.get_net()
    spots = pos.candidate_list()
    if not spots:
        return []
    feats = net.encode(pos, spots, color)
    scored = sorted(((model.score(f), i) for f, i in zip(feats, spots)), reverse=True)
    return [i for _, i in scored]


def opening_moves(pos: Position, plies: int, seed: int, depth: int = 3) -> None:
    """用搜索自对弈铺出中盘局面。

    前两手随机落在中腹附近，否则搜索是确定性的，每次都会铺出同一盘棋——
    样本量退化成 1，统计全是精度的假象。
    """
    import random

    rng = random.Random(seed)
    first = rng.choice([(7, 7), (7, 8), (8, 7), (6, 7), (7, 6)])
    pos.place(first[1] * 15 + first[0], 1)
    dx, dy = rng.choice([(1, 0), (0, 1), (1, 1), (-1, 1)])
    second = (min(14, max(0, first[0] + dx)), min(14, max(0, first[1] + dy)))
    pos.place(second[1] * 15 + second[0], 2)

    color = 1
    searcher = Searcher(time_limit=0.5, max_depth=depth)
    while pos.occupied < plies:
        result = searcher.search(pos, color)
        if pos.board[result.move] != 0:
            return
        if pos.rule == rules.RENJU and color == 1 and pos.forbidden(result.move, color):
            return
        pos.place(result.move, color)
        if pos.win_kind(result.move, color):
            return
        color = 3 - color


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--positions", type=int, default=16)
    ap.add_argument("--plies", type=int, default=24)
    ap.add_argument("--oracle-depth", type=int, default=5)
    args = ap.parse_args()

    model = net.get_net()
    if not model.ready:
        print("网络权重没加载，跑不了")
        return 1

    oracle_hits = {"net": 0, "static": 0, "either": 0}
    overlap_sum = 0
    total = 0

    deep = Searcher(time_limit=6.0, max_depth=args.oracle_depth)

    for g in range(args.positions):
        pos = Position(rule=rules.RENJU)
        opening_moves(pos, args.plies + (g % 5) * 4, seed=g * 7919)
        if pos.occupied < 12:
            continue

        color = 1 if pos.occupied % 2 == 0 else 2
        best = deep.search(pos, color)
        if best.reason:
            continue

        nr = net_rank(pos, color)[:TOPK]
        sr = static_rank(pos, color)[:TOPK]
        overlap = len(set(nr) & set(sr)) / TOPK
        overlap_sum += overlap
        total += 1

        in_net = best.move in nr
        in_static = best.move in sr
        oracle_hits["net"] += int(in_net)
        oracle_hits["static"] += int(in_static)
        oracle_hits["either"] += int(in_net or in_static)

        flag = "  " 
        if not in_net and in_static:
            flag = " ← 网络漏"
        elif in_net and not in_static:
            flag = " ← 网络补"
        print(
            f"  #{g + 1:>2} {pos.occupied:>3} 手  最优 {best.move:>3}"
            f"  网络{'✓' if in_net else '✗'} 静态{'✓' if in_static else '✗'}"
            f"  重合 {overlap:.0%}{flag}",
            flush=True,
        )

    if not total:
        print("没有采到有效局面")
        return 1

    print()
    print(f"样本 {total} 个局面（Top-{TOPK}，标准答案 = 深度 {args.oracle_depth} 全量搜索）")
    print(f"  网络      命中 {oracle_hits['net']}/{total} = {oracle_hits['net'] / total:.0%}")
    print(f"  静态      命中 {oracle_hits['static']}/{total} = {oracle_hits['static'] / total:.0%}")
    print(f"  取并集    命中 {oracle_hits['either']}/{total} = {oracle_hits['either'] / total:.0%}")
    print(f"  平均重合度 {overlap_sum / total:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
