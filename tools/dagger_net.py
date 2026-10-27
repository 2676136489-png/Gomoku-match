"""用 DAgger 继续训练落点网络。

纯克隆式蒸馏有个已知毛病：学生一旦下出教师没走过的局面，就没人告诉它该怎么做，
误差会越滚越大。这个网络第一次训练完能堵冲四、能连五，但整局还是输给贪心，
输的那手正是这种「学生自己跑偏」的位置。

DAgger 的做法很直接：让学生自己下，每一步请教师现场批改，把批改结果加回训练集。
学生跑得越偏，补的正是它偏到的地方。

    python tools/dagger_net.py --rounds 3 --games 6 --epochs 40

权重原地覆盖 server/gomoku/net_weights.json。
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
sys.path.insert(0, str(ROOT / "tools"))

from server.gomoku import net, rules  # noqa: E402
from server.gomoku.position import BLACK, CELLS, OPPONENT, Position  # noqa: E402
from server.gomoku.search import Searcher  # noqa: E402
import train_net  # noqa: E402


def load_params(path: Path) -> dict[str, np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: np.asarray(data[k], dtype=np.float64) for k in ("W1", "b1", "W2", "b2", "W3", "b3")}


def score_batch(params: dict[str, np.ndarray], feats: list[list[float]]) -> np.ndarray:
    X = np.asarray(feats, dtype=np.float64)
    p, _ = train_net.forward(params, X)
    return p.reshape(-1)


def student_pick(params, pos: Position, color: int, rng: random.Random, epsilon: float) -> int:
    spots = [i for i in pos.candidate_list() if pos.board[i] == 0]
    if pos.rule == rules.RENJU and color == BLACK:
        spots = [i for i in spots if not pos.forbidden(i, color)]
    if not spots:
        return 112
    if rng.random() < epsilon:
        return rng.choice(spots)
    feats = net.encode(pos, spots, color)
    scores = score_batch(params, feats)
    return spots[int(np.argmax(scores))]


def collect_round(params, games: int, teacher_time: float, teacher_depth: int, seed: int):
    rng = random.Random(seed)
    X: list[list[float]] = []
    Y: list[float] = []
    states = 0

    for g in range(games):
        pos = Position(rules.RENJU)
        color = BLACK
        for move_no in range(1, 90):
            # 空盘没有候选点（候选集只包含已有棋子周围），第一手直接落天元
            if pos.occupied == 0:
                pos.place(112, color)
                color = OPPONENT[color]
                continue

            spots = [i for i in pos.candidate_list() if pos.board[i] == 0]
            if not spots:
                break

            searcher = Searcher(time_limit=teacher_time, max_depth=teacher_depth)
            result = searcher.search(pos, color)
            teacher = result.move
            ranked = [m.idx for m in searcher.rank_moves(pos, color, 8)]

            picks = [(teacher, 1.0)]
            for idx in ranked[:4]:
                if idx != teacher:
                    picks.append((idx, 0.35))
            pool = [i for i in spots if i != teacher]
            rng.shuffle(pool)
            for idx in pool[:8]:
                picks.append((idx, 0.0))

            X.extend(net.encode(pos, [p[0] for p in picks], color))
            Y.extend([p[1] for p in picks])
            states += 1

            # 学生自己下，让它把跑偏的局面暴露出来
            play = student_pick(params, pos, color, rng, epsilon=0.12)
            if pos.board[play] != 0 or (color == BLACK and pos.forbidden(play, color)):
                play = teacher
            pos.place(play, color)
            kind = pos.win_kind(play, color)
            if kind == rules.FIVE:
                break
            if kind == rules.LONG:
                break
            color = OPPONENT[color]

    return X, Y, states


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--lr", type=float, default=0.004)
    ap.add_argument("--teacher-time", type=float, default=0.35)
    ap.add_argument("--teacher-depth", type=int, default=3)
    ap.add_argument("--seed", type=int, default=20261024)
    ap.add_argument("--weights", type=Path, default=net.WEIGHTS_FILE)
    args = ap.parse_args()

    params = load_params(args.weights)
    print(f"载入现有权重：{args.weights.name}", flush=True)

    all_x: list[list[float]] = []
    all_y: list[float] = []

    for r in range(1, args.rounds + 1):
        started = time.perf_counter()
        X, Y, states = collect_round(
            params, args.games, args.teacher_time, args.teacher_depth, args.seed + r * 17
        )
        all_x.extend(X)
        all_y.extend(Y)
        print(
            f"  第 {r} 轮：{args.games} 局、{states} 个局面、{len(X)} 条样本，"
            f"耗时 {time.perf_counter() - started:.0f}s",
            flush=True,
        )

        params = train_net.train(
            np.asarray(all_x, dtype=np.float64),
            np.asarray(all_y, dtype=np.float64).reshape(-1, 1),
            args.epochs,
            args.lr,
            args.seed + r,
            init=params,
        )
        p, _ = train_net.forward(
            params, np.asarray(all_x, dtype=np.float64)
        )
        acc = float(((p > 0.5) == (np.asarray(all_y).reshape(-1, 1) > 0.5)).mean())
        print(f"  第 {r} 轮训练完成，累计样本 {len(all_x)}，准确率 {acc:.3f}", flush=True)

    meta = {
        "trained_at": time.strftime("%Y-%m-%d"),
        "samples": len(all_x),
        "method": "distill + DAgger",
        "dagger_rounds": args.rounds,
        "teacher_depth": args.teacher_depth,
        "epochs": args.epochs,
    }
    train_net.export(params, args.weights, meta)
    print(f"权重已更新：{args.weights}", flush=True)


if __name__ == "__main__":
    main()
