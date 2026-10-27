"""训练落点价值网络。

做法是蒸馏：让引擎自对弈，每一步记录局面，然后把「引擎实际走的那一手」
当正样本、搜索排序里靠后和随机抽的合法点当负样本，用二元交叉熵拟合一个
30 → 32 → 16 → 1 的全连接网络。

之所以用蒸馏而不是自己跑强化学习：五子棋的胜负信号太稀疏，一盘棋只有一个
胜负标签摊到几十手上，REINFORCE 的方差在 CPU 上根本压不住。搜索本来就已经
比网络强，让它当教师是最省样本的路子。

    python tools/train_net.py --games 12 --time 0.5 --epochs 60

训练用 numpy，产物是一份纯 JSON 权重；运行时不依赖 numpy，见
server/gomoku/net.py 的说明。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.gomoku import net, rules  # noqa: E402
from server.gomoku.position import BLACK, CELLS, OPPONENT, WHITE, Position  # noqa: E402
from server.gomoku.search import Searcher  # noqa: E402

HIDDEN1, HIDDEN2 = 48, 24


def collect(games: int, time_limit: float, depth: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = random.Random(seed)
    X: list[list[float]] = []
    Y: list[float] = []

    started = time.perf_counter()
    for g in range(games):
        pos = Position(rules.RENJU)
        color = BLACK
        moves = 0
        while pos.occupied < CELLS and moves < 120:
            spots = pos.candidate_list() or [i for i in range(CELLS) if pos.board[i] == 0]

            # 前四手随机开局，否则每盘棋的前十个局面几乎一模一样
            if moves < 4:
                idx = rng.choice([s for s in spots if not pos.forbidden(s, color)])
                pos.place(idx, color)
                color = OPPONENT[color]
                moves += 1
                continue

            searcher = Searcher(time_limit=time_limit, max_depth=depth)
            result = searcher.search(pos, color)
            play = result.move
            ranking = [m.idx for m in searcher.rank_moves(pos, color, 10)]

            picks: list[tuple[int, float]] = [(play, 1.0)]
            for idx in ranking[:4]:
                if idx != play:
                    picks.append((idx, 0.35))
            pool = [i for i in spots if i != play]
            rng.shuffle(pool)
            for idx in pool[:8]:
                picks.append((idx, 0.0))

            feats = net.encode(pos, [p[0] for p in picks], color)
            X.extend(feats)
            Y.extend([p[1] for p in picks])

            pos.place(play, color)
            kind = pos.win_kind(play, color)
            if kind == rules.FIVE:
                break
            if kind == rules.LONG and color == BLACK:
                break
            color = OPPONENT[color]
            moves += 1

        if (g + 1) % 2 == 0:
            print(
                f"  自对弈 {g + 1}/{games} 局，样本 {len(X)}，"
                f"耗时 {time.perf_counter() - started:.0f}s",
                flush=True,
            )

    return np.asarray(X, dtype=np.float64), np.asarray(Y, dtype=np.float64).reshape(-1, 1)


def init_params(rng: np.random.Generator) -> dict[str, np.ndarray]:
    return {
        "W1": rng.normal(0, np.sqrt(2.0 / net.FEATURES), (HIDDEN1, net.FEATURES)),
        "b1": np.zeros(HIDDEN1),
        "W2": rng.normal(0, np.sqrt(2.0 / HIDDEN1), (HIDDEN2, HIDDEN1)),
        "b2": np.zeros(HIDDEN2),
        "W3": rng.normal(0, np.sqrt(2.0 / HIDDEN2), (1, HIDDEN2)),
        "b3": np.zeros(1),
    }


def forward(params: dict[str, np.ndarray], X: np.ndarray) -> tuple[np.ndarray, tuple]:
    z1 = X @ params["W1"].T + params["b1"]
    h1 = np.tanh(z1)
    z2 = h1 @ params["W2"].T + params["b2"]
    h2 = np.tanh(z2)
    z3 = h2 @ params["W3"].T + params["b3"]
    p = 1.0 / (1.0 + np.exp(-np.clip(z3, -30, 30)))
    return p, (X, h1, h2, p)


def train(
    X: np.ndarray, Y: np.ndarray, epochs: int, lr: float, seed: int, init: dict | None = None
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    params = init if init is not None else init_params(rng)
    n = X.shape[0]
    if n == 0:
        raise SystemExit("没有训练样本，检查采样阶段")
    m = {k: np.zeros_like(v) for k, v in params.items()}
    v = {k: np.zeros_like(val) for k, val in params.items()}
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    step = 0
    batch = 128

    for epoch in range(epochs):
        order = rng.permutation(n)
        total = 0.0
        for start in range(0, n, batch):
            idx = order[start : start + batch]
            xb, yb = X[idx], Y[idx]
            p, (xb_, h1, h2, _) = forward(params, xb)

            # BCE + L2，梯度直接手推：dL/dz3 = p - y
            dz3 = (p - yb) / len(idx)
            grads = {
                "W3": dz3.T @ h2 + 1e-4 * params["W3"],
                "b3": dz3.sum(axis=0),
            }
            dh2 = dz3 @ params["W3"] * (1 - h2**2)
            grads["W2"] = dh2.T @ h1 + 1e-4 * params["W2"]
            grads["b2"] = dh2.sum(axis=0)
            dh1 = dh2 @ params["W2"] * (1 - h1**2)
            grads["W1"] = dh1.T @ xb_ + 1e-4 * params["W1"]
            grads["b1"] = dh1.sum(axis=0)

            step += 1
            loss = float(-np.mean(yb * np.log(p + 1e-9) + (1 - yb) * np.log(1 - p + 1e-9)))
            total += loss * len(idx)

            for k, g in grads.items():
                m[k] = beta1 * m[k] + (1 - beta1) * g
                v[k] = beta2 * v[k] + (1 - beta2) * g * g
                mh = m[k] / (1 - beta1**step)
                vh = v[k] / (1 - beta2**step)
                params[k] -= lr * mh / (np.sqrt(vh) + eps)

        if (epoch + 1) % 10 == 0 or epoch == 0:
            print(f"  epoch {epoch + 1:>3}/{epochs}  loss={total / n:.4f}", flush=True)
    return params


def export(params: dict[str, np.ndarray], path: Path, meta: dict) -> None:
    payload = {**{k: v.tolist() for k, v in params.items()}, "meta": meta}
    path.write_text(json.dumps(payload), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=12)
    ap.add_argument("--time", type=float, default=0.5)
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--lr", type=float, default=0.01)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--out", type=Path, default=net.WEIGHTS_FILE)
    args = ap.parse_args()

    print(f"采样：{args.games} 局自对弈，搜索 depth={args.depth} / {args.time}s", flush=True)
    X, Y = collect(args.games, args.time, args.depth, args.seed)
    print(f"样本 {X.shape[0]} 条，正样本占比 {Y.mean():.3f}", flush=True)

    params = train(X, Y, args.epochs, args.lr, args.seed)
    p, _ = forward(params, X)
    acc = float(((p > 0.5) == (Y > 0.5)).mean())
    print(f"训练集准确率 {acc:.3f}", flush=True)

    export(
        params,
        args.out,
        {
            "trained_at": time.strftime("%Y-%m-%d"),
            "samples": int(X.shape[0]),
            "games": args.games,
            "teacher_depth": args.depth,
            "epochs": args.epochs,
            "train_acc": round(acc, 4),
        },
    )
    print(f"权重已写入 {args.out}", flush=True)


if __name__ == "__main__":
    main()
