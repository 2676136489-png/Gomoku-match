"""生成内置棋谱。

内置棋谱的作用是「第一次打开贪心学习页就有东西可看、可训练」。它不是人类高手的
对局——手头没有可授权的棋谱库，编一份「名家对局」是不诚实的。这里用引擎自战：
两边都是 alpha-beta 搜索，一边深度 2、一边深度 1，开局随机落在天元附近，
每盘棋都不一样。

为什么用搜索而不是贪心当老师：棋谱是「标准答案」，标准答案的水平决定了学生能学到
什么。贪心自战学出来的分值表只是在模仿手写分值表自己，搜索则会走一些「评估分不高
但必须挡住」的应手，这类样本才能矫正分值表。生成过程完全可复现（种子固定）。

    python tools/make_corpus.py              # 默认 8 盘
    python tools/make_corpus.py --games 12

输出写进 server/gomoku/corpus_seed.json，随仓库走。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.gomoku import algorithms, rules  # noqa: E402
from server.gomoku.position import CELLS, Position  # noqa: E402

OUT = ROOT / "server" / "gomoku" / "corpus_seed.json"


def play(seed: int, *, deep: int = 2, shallow: int = 1, max_moves: int = 100) -> dict:
    rng = random.Random(seed)
    pos = Position(rules.RENJU)
    opening = (7 + rng.choice((-1, 0, 1))) * 15 + 7 + rng.choice((-1, 0, 1))
    pos.place(opening, 1)
    moves = [[opening % 15, opening // 15]]
    winner = None
    reason = "满盘未分胜负"
    # 谁执深搜每盘轮换，免得棋谱里只有一种赢法
    deep_color = 1 if seed % 2 == 0 else 2

    for ply in range(1, max_moves):
        color = 2 if ply % 2 == 1 else 1
        level = deep if color == deep_color else shallow
        idx = algorithms.choose("minimax", pos, color, difficulty=level)["idx"]
        if pos.board[idx] != 0:
            idx = next(i for i in range(CELLS) if pos.board[i] == 0)
        pos.place(idx, color)
        moves.append([idx % 15, idx // 15])
        verdict = rules.judge(pos.board, idx, color, rules.RENJU)
        if verdict.finished:
            winner = verdict.winner
            reason = verdict.label
            break

    return {"moves": moves, "winner": winner, "reason": reason}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=8)
    args = ap.parse_args()

    started = time.time()
    items = []
    for i in range(args.games):
        game = play(20260000 + i * 97)
        if len(game["moves"]) < 12:
            continue
        side = {1: "黑胜", 2: "白胜", None: "未分胜负"}[game["winner"]]
        items.append(
            {
                "id": f"seed-{i + 1:02d}",
                "name": f"引擎自战 {i + 1:02d}",
                "source": "builtin",
                "author": "引擎生成",
                # 备注不写手数：列表里另有一列，重复一遍只是噪音
                "note": f"{side} · {game['reason']}",
                "moves": game["moves"],
                "winner": game["winner"],
                "reason": game["reason"],
                "created_at": 1_760_000_000 + i * 3600,
            }
        )
        print(f"  第 {i + 1} 盘：{len(game['moves'])} 手，{side}（{game['reason']}）", flush=True)

    payload = {
        "version": 1,
        "generated_at": time.time(),
        "generator": "tools/make_corpus.py",
        "items": items,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    total = sum(len(i["moves"]) for i in items)
    print(f"\n写出 {len(items)} 盘 / {total} 手 → {OUT}（{time.time() - started:.1f}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
