"""内置算法互相对弈，统计胜率与耗时。

两边轮流执黑——连珠规则下黑棋有先手优势，固定一方执黑会让结果失真。

    .venv/Scripts/python.exe tools/bench_algorithms.py neural greedy -n 10
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.gomoku import algorithms, rules  # noqa: E402
from server.gomoku.position import BLACK, WHITE, Position  # noqa: E402


def one_game(
    left: str, right: str, difficulty: int, max_moves: int, open_seed: int | None = None
) -> dict:
    import random

    pos = Position(rule=rules.RENJU)
    seats = {BLACK: left, WHITE: right}
    color = BLACK
    times: dict[str, list[int]] = {left: [], right: []}
    moves = 0

    # 两路算法都没有随机性，同一局面永远给出同一手。不加随机开局的话，
    # 「打 8 局」实际是同一对局重复 4 遍，统计出的胜率不具参考性。
    if open_seed is not None:
        rng = random.Random(open_seed)
        first = rng.choice([(7, 7), (7, 8), (8, 7), (6, 7), (7, 6), (8, 8)])
        pos.place(first[1] * 15 + first[0], BLACK)
        dx, dy = rng.choice([(1, 0), (0, 1), (1, 1), (-1, 1)])
        second = (min(14, max(0, first[0] + dx)), min(14, max(0, first[1] + dy)))
        pos.place(second[1] * 15 + second[0], WHITE)
        moves = 2
        color = BLACK

    while moves < max_moves:
        key = seats[color]
        out = algorithms.choose(key, pos, color, difficulty=difficulty)
        times[key].append(out["ms"])
        idx = out["idx"]
        if pos.board[idx] != 0:
            return {"winner": 3 - color, "reason": f"{key} 给了已占用的点", "moves": moves, "times": times}
        if pos.rule == rules.RENJU and color == BLACK and pos.forbidden(idx, color):
            return {"winner": WHITE, "reason": f"{key} 落禁手", "moves": moves, "times": times}

        pos.place(idx, color)
        moves += 1
        kind = pos.win_kind(idx, color)
        if kind:
            return {"winner": color, "reason": kind, "moves": moves, "times": times}
        color = 3 - color

    return {"winner": 0, "reason": "达到手数上限", "moves": moves, "times": times}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("left")
    ap.add_argument("right")
    ap.add_argument("-n", "--games", type=int, default=8)
    ap.add_argument("-d", "--difficulty", type=int, default=3)
    ap.add_argument("-m", "--max-moves", type=int, default=225)
    ap.add_argument("--time", type=float, default=0.0, help="覆盖难度档的时间预算（秒）")
    ap.add_argument("--depth", type=int, default=0, help="覆盖难度档的最大深度")
    ap.add_argument(
        "--same-open",
        action="store_true",
        help="固定开局（默认每局换一个随机开局，否则结果只是同一盘棋重复）",
    )
    args = ap.parse_args()

    if args.time or args.depth:
        # 同预算对照实验的关键：两边必须拿一样的算力，否则比出来的是配置差不是算法差
        from server.gomoku import agent

        level = dict(agent.DIFFICULTY[args.difficulty])
        if args.time:
            level["time"] = args.time
        if args.depth:
            level["depth"] = args.depth
        agent.DIFFICULTY[args.difficulty] = level
        print(f"  [覆盖] 难度 {args.difficulty} → {level}")

    score = {args.left: 0, args.right: 0, "draw": 0}
    avg: dict[str, list[int]] = {args.left: [], args.right: []}
    started = time.perf_counter()

    for i in range(args.games):
        seed = None if args.same_open else 1000 + i * 13
        # 偶数局 left 执黑，奇数局交换
        if i % 2 == 0:
            res = one_game(args.left, args.right, args.difficulty, args.max_moves, seed)
            black, white = args.left, args.right
        else:
            res = one_game(args.right, args.left, args.difficulty, args.max_moves, seed)
            black, white = args.right, args.left

        if res["winner"] == 0:
            score["draw"] += 1
            tag = "和"
        else:
            name = black if res["winner"] == BLACK else white
            score[name] += 1
            tag = f"{name} 胜"
        for k, v in res["times"].items():
            avg[k].extend(v)
        print(
            f"  #{i + 1}: {black} 执黑 vs {white} 执白  →  {tag}"
            f"（{res['reason']}，{res['moves']} 手）",
            flush=True,
        )

    print()
    print(f"{args.left:>10}  {score[args.left]} 胜", end="")
    print(f"  |  {score[args.right]} 胜  {args.right}")
    if score["draw"]:
        print(f"{'和棋':>10}  {score['draw']}")
    for k in (args.left, args.right):
        if avg[k]:
            print(f"{k:>10}  每手均 {sum(avg[k]) / len(avg[k]):.0f} ms（{len(avg[k])} 手）")
    print(f"{'总耗时':>10}  {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
