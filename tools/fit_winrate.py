"""标定胜率模型。

做三件事：

1. **自对弈采样。** 每一手落子前记下当时的局面特征和「轮到谁走」，对局结束后
   按胜负打标签。同一个局面还会生成一份镜像样本（双方特征对调、标签取反），
   这样模型对先后手严格对称，均势局面必然给出 50%——不需要额外的偏置项。

2. **拟合与体检。** 六维逻辑回归用 IRLS 求解，打印 AUC、Brier、准确率和可靠性表。
   可靠性表是关键：把预测值分十档，看每档的实际胜率是不是贴着预测值走。
   只报 AUC 不报可靠性表的话，没人知道「70%」到底是不是真 70%。

3. **调点位预测。** 标准答案是搜索预算给足时实际走的那一手，看它落不落在
   预测的前三格里，据此选温度和先验权重。

开局必须随机。上一轮踩过这个坑：两路算法都没有随机性时，「打 12 局」
其实是同一盘棋重复 6 遍，统计出来的数字全是假的。

    python tools/fit_winrate.py --games 700
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.gomoku import insight, rules  # noqa: E402
from server.gomoku.position import BLACK, OPPONENT, Position, SIZE  # noqa: E402
from server.gomoku.search import Searcher  # noqa: E402

OUT = ROOT / "server" / "gomoku" / "winrate_model.json"

# 镜像增广的符号：反特征取反，对称特征不变。
# 这四条把「先手优势」从数据里剔除干净——镜像之后截距必须接近 0，
# 否则说明特征里还藏着颜色偏差，得回去查。
MIRROR_SIGN = {
    "log_ratio": -1.0,
    "occupancy": 1.0,
}


# ------------------------------------------------------------------ 走子策略


def _rank(pos: Position, color: int, width: int = 6):
    return Searcher(time_limit=0.05, max_depth=1).rank_moves(pos, color, width)


def greedy(pos: Position, color: int, rng: random.Random, eps: float) -> int | None:
    moves = _rank(pos, color, 6)
    if not moves:
        return None
    if rng.random() < eps:
        return rng.choice(moves[: min(4, len(moves))]).idx
    return moves[0].idx


def search_move(pos: Position, color: int, depth: int, seconds: float) -> int | None:
    if pos.occupied == 0:
        return 112
    return Searcher(time_limit=seconds, max_depth=depth).search(pos, color).move


# ------------------------------------------------------------------ 采样


def play_game(rng: random.Random, strong_ratio: float, eps: float):
    """一局自对弈。返回 (样本, 胜方, 落子序列)。"""
    pos = Position(rule=rules.RENJU)
    samples: list[tuple[dict, int]] = []
    moves: list[tuple[int, int]] = []

    # 随机开局，只取中腹附近。四角乱飞的局面对棋形分布没有代表性。
    plies = rng.choice((2, 3, 4))
    pool = [i for i in range(SIZE * SIZE) if 4 <= i % SIZE <= 10 and 4 <= i // SIZE <= 10]
    rng.shuffle(pool)
    for _ in range(plies):
        idx = pool.pop()
        color = BLACK if pos.occupied % 2 == 0 else OPPONENT[BLACK]
        pos.place(idx, color)
        moves.append((idx, color))

    strong = rng.random() < strong_ratio
    strong_color = rng.choice((BLACK, OPPONENT[BLACK]))

    winner: int | None = None
    while pos.occupied < SIZE * SIZE:
        turn = BLACK if pos.occupied % 2 == 0 else OPPONENT[BLACK]
        samples.append((insight._features(pos, turn), turn))

        if strong and turn == strong_color:
            idx = search_move(pos, turn, 3, 0.18)
        else:
            idx = greedy(pos, turn, rng, eps)
        if idx is None or pos.board[idx] != 0:
            break

        pos.place(idx, turn)
        moves.append((idx, turn))
        kind = pos.win_kind(idx, turn)
        if kind is not None:
            # 连珠规则：黑棋长连是禁手判负，白棋长连算胜
            winner = turn if (kind == rules.FIVE or turn == rules.WHITE) else OPPONENT[turn]
            break

    return samples, winner, moves


def label_samples(raw, winner: int):
    xs: list[list[float]] = []
    ys: list[int] = []
    for values, turn in raw:
        y = 1 if turn == winner else 0
        xs.append([values[k] for k in insight.FEATURES])
        ys.append(y)
        # 镜像增广：同一局面换一方视角，胜负必然互换
        xs.append([values[k] * MIRROR_SIGN[k] for k in insight.FEATURES])
        ys.append(1 - y)
    return xs, ys


# ------------------------------------------------------------------ 拟合


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """高斯消元，只用于 6x6。"""
    n = len(a)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            continue
        m[col], m[pivot] = m[pivot], m[col]
        inv = 1.0 / m[col][col]
        for j in range(col, n + 1):
            m[col][j] *= inv
        for r in range(n):
            if r == col or abs(m[r][col]) < 1e-15:
                continue
            factor = m[r][col]
            for j in range(col, n + 1):
                m[r][j] -= factor * m[col][j]
    return [m[i][n] for i in range(n)]


def fit(xs, ys, l2: float = 1.0, iters: int = 14):
    n = len(xs)
    k = len(xs[0])
    mean = [0.0] * k
    std = [1.0] * k
    for i in range(k):
        col = [row[i] for row in xs]
        mu = sum(col) / n
        var = sum((v - mu) ** 2 for v in col) / n
        mean[i] = mu
        std[i] = math.sqrt(var) or 1.0

    z = [[(row[i] - mean[i]) / std[i] for i in range(k)] for row in xs]
    w = [0.0] * k

    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for row, y in zip(z, ys):
            p = 1.0 / (1.0 + math.exp(-sum(wi * xi for wi, xi in zip(w, row))))
            r = y - p
            weight = max(p * (1 - p), 1e-6)
            for i in range(k):
                grad[i] += r * row[i]
                for j in range(i, k):
                    hess[i][j] += weight * row[i] * row[j]
        for i in range(k):
            for j in range(i):
                hess[i][j] = hess[j][i]
            hess[i][i] += l2
        step = _solve(hess, grad)
        w = [wi + si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-7:
            break

    # 存的就是标准化空间的权重，推理时由 insight.logit_of 一致地还原。
    # 不折回原始量纲——折回去再标准化的双重处理会让系数被缩放两次，
    # 上一版就是这么把符号弄反的。
    return w, mean, std


def metrics(xs, ys, weights, mean, std) -> dict:
    """用和线上完全相同的 logit_of 计算，保证报告和运行时是同一套算法。"""
    ps = [
        1.0 / (1.0 + math.exp(-insight.logit_of(weights, mean, std, x)[0]))
        for x in xs
    ]

    pairs = sorted(zip(ps, ys))
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    pos_n = sum(ys)
    neg_n = len(ys) - pos_n
    rank_sum = sum(ranks[i] for i in range(len(pairs)) if pairs[i][1] == 1)
    auc = (rank_sum - pos_n * (pos_n + 1) / 2) / (pos_n * neg_n) if pos_n and neg_n else float("nan")

    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys)
    acc = sum(1 for p, y in zip(ps, ys) if (p >= 0.5) == (y == 1)) / len(ys)

    buckets = []
    for b in range(10):
        lo, hi = b / 10, (b + 1) / 10
        group = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi or (b == 9 and p >= 1.0)]
        if not group:
            continue
        buckets.append(
            {
                "range": f"{int(lo * 100)}-{int(hi * 100)}%",
                "n": len(group),
                "predicted": round(sum(p for p, _ in group) / len(group), 4),
                "actual": round(sum(y for _, y in group) / len(group), 4),
            }
        )
    return {"auc": round(auc, 4), "brier": round(brier, 4), "acc": round(acc, 4), "buckets": buckets}


# ------------------------------------------------------------------ 预测调参


def tune_predict(positions, ref_depth: int, ref_time: float) -> dict:
    """用「前 N 格是否命中标准答案」来选温度和先验权重。"""
    truths = []
    started = time.perf_counter()
    for pos, turn in positions:
        truths.append(search_move(pos, turn, ref_depth, ref_time))
    print(f"  标准答案 {len(positions)} 个，耗时 {time.perf_counter() - started:.1f}s")

    rows = []
    for prior in (0.0, 1.0, 2.2, 3.5):
        for temp in (0.6, 1.0, 1.6, 2.4):
            insight.get_model().predict_cfg = {"prior_weight": prior, "temperature": temp}
            top1 = top3 = 0
            for (pos, turn), truth in zip(positions, truths):
                if truth is None:
                    continue
                pts = insight.predict(pos, turn, 3)["points"]
                if not pts:
                    continue
                tx, ty = truth % SIZE, truth // SIZE
                if pts[0]["x"] == tx and pts[0]["y"] == ty:
                    top1 += 1
                if any(p["x"] == tx and p["y"] == ty for p in pts):
                    top3 += 1
            n = max(1, len(positions))
            rows.append((prior, temp, top1 / n, top3 / n))

    rows.sort(key=lambda r: (r[3], r[2]), reverse=True)
    print("  前五组配置（先验权重 / 温度 / Top-1 / Top-3）：")
    for prior, temp, t1, t3 in rows[:5]:
        print(f"    {prior:>4} / {temp:>3}  →  {t1 * 100:5.1f}%  {t3 * 100:5.1f}%")
    prior, temp, t1, t3 = rows[0]
    return {"prior_weight": prior, "temperature": temp, "top1": round(t1, 4), "top3": round(t3, 4)}


# ------------------------------------------------------------------ 主流程


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=700)
    ap.add_argument("--strong-ratio", type=float, default=0.3, help="多大比例的局面对手换用搜索")
    ap.add_argument("--eps", type=float, default=0.25, help="贪心的随机扰动比例")
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--eval-positions", type=int, default=40)
    ap.add_argument("--ref-depth", type=int, default=4)
    ap.add_argument("--ref-time", type=float, default=0.6)
    ap.add_argument("--skip-write", action="store_true")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    xs: list[list[float]] = []
    ys: list[int] = []
    eval_pool: list[tuple[Position, int]] = []

    started = time.perf_counter()
    wins = {BLACK: 0, OPPONENT[BLACK]: 0}
    draws = 0

    for g in range(args.games):
        raw, winner, moves = play_game(rng, args.strong_ratio, args.eps)
        if winner is None:
            draws += 1
            continue
        wins[winner] += 1
        gx, gy = label_samples(raw, winner)
        xs.extend(gx)
        ys.extend(gy)

        if len(eval_pool) < args.eval_positions and len(moves) >= 16 and g % 3 == 0:
            cut = max(10, int(len(moves) * 0.6))
            pos = Position(rule=rules.RENJU)
            for idx, color in moves[:cut]:
                pos.place(idx, color)
            eval_pool.append((pos, BLACK if cut % 2 == 0 else OPPONENT[BLACK]))

        if (g + 1) % 100 == 0:
            print(f"  {g + 1} 局 / 样本 {len(xs)} / {time.perf_counter() - started:.0f}s")

    print(f"\n对局 {args.games} 局（黑胜 {wins[BLACK]} / 白胜 {wins[OPPONENT[BLACK]]} / 和 {draws}）")
    print(f"样本 {len(xs)} 条（含镜像增广），{time.perf_counter() - started:.0f}s")

    w, mean, std = fit(xs, ys)
    weights = [0.0] + [round(v, 6) for v in w]
    stats = metrics(xs, ys, weights, mean, std)
    print(f"\nAUC {stats['auc']}  Brier {stats['brier']}  准确率 {stats['acc'] * 100:.1f}%")
    print("可靠性表：")
    print("  区间        样本    预测     实际")
    for b in stats["buckets"]:
        print(f"  {b['range']:<10} {b['n']:>6}  {b['predicted'] * 100:5.1f}%  {b['actual'] * 100:5.1f}%")

    print("\n权重（标准化空间）：")
    for name, wi in zip(insight.FEATURES, w):
        print(f"  {name:<12} {wi:+.4f}")
    print(f"  均值 {[round(v, 3) for v in mean]}")
    print(f"  标准差 {[round(v, 3) for v in std]}")

    model = insight.get_model()
    model.weights = weights
    model.mean = mean
    model.std = std

    print("\n调点位预测（标准答案 = 搜索实际选点）")
    tune = {}
    if eval_pool:
        tune = tune_predict(eval_pool, args.ref_depth, args.ref_time)
    else:
        print("  没有可用的评测局面")

    payload = {
        "features": list(insight.FEATURES),
        "weights": weights,
        "mean": [round(v, 6) for v in mean],
        "std": [round(v, 6) for v in std],
        "predict": tune,
        "meta": {
            "games": args.games,
            "samples": len(xs),
            "auc": stats["auc"],
            "brier": stats["brier"],
            "acc": stats["acc"],
            "buckets": stats["buckets"],
            "generated": time.strftime("%Y-%m-%d"),
            # 键名不叫 source——describe() 里的 source 表示「权重从哪来」，
            # 两个 source 撞在一起，前端读到的会变成这段自然语言。
            "sample": (
                f"{args.games} 局自对弈，随机开局 + 贪心扰动 ε={args.eps}，"
                f"其中 {int(args.strong_ratio * 100)}% 的局面一方换用搜索"
            ),
        },
    }

    if args.skip_write:
        print("\n--skip-write：不落盘")
        return
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {OUT.relative_to(ROOT)}（{OUT.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main()
