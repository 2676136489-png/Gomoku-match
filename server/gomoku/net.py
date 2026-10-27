"""落点价值网络。

一个 30 维特征进、三层全连接出的小网络，输出「这个空点值得下的概率」。
训练方式是蒸馏：拿搜索深度 3 的引擎自对弈，把引擎实际走的点当正样本、
同局面里随机抽的合法点当负样本，用二元交叉熵拟合。

前向传播是纯 Python 手写的，不依赖 numpy。参数只有一千五百多个，
一次落子评估八十来个候选点大约一百毫秒——为了这么点算力在运行环境里
背一个 numpy，部署时多一个二进制兼容风险，不划算。训练脚本另说，
那边用 numpy 才写得动梯度，见 tools/train_net.py。

网络只做落点价值排序，不做搜索。它比贪心强，比带搜索的引擎弱，
这个差距是真实的，也是竞技场里值得看的对照。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SIZE = 15
CELLS = SIZE * SIZE
EMPTY = 0

WEIGHTS_FILE = Path(__file__).resolve().parent / "net_weights.json"

DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))

# 特征维度：4 个方向 × 5 项 + 14 项全局
FEATURES = 4 * 5 + 14

# 棋形分能到 10^7，取对数压到 0~1 区间再喂给网络
SCORE_SCALE = 18.0


def _line(board: bytearray, x: int, y: int, dx: int, dy: int, c: int) -> tuple[int, int]:
    """以 (x,y) 假设落 c 子的连续长度和两侧活的端数。"""
    run = 1
    ends = 0
    for sign in (1, -1):
        k = 1
        while True:
            nx = x + dx * k * sign
            ny = y + dy * k * sign
            if nx < 0 or nx >= SIZE or ny < 0 or ny >= SIZE:
                break
            if board[ny * SIZE + nx] != c:
                break
            run += 1
            k += 1
        if 0 <= nx < SIZE and 0 <= ny < SIZE and board[ny * SIZE + nx] == EMPTY:
            ends += 1
    return run, ends


def _density(board: bytearray, x: int, y: int, dx: int, dy: int, c: int) -> int:
    """该方向上 9 格窗口里 c 子的个数，含假设的这一格。"""
    n = 1
    for k in range(-4, 5):
        if k == 0:
            continue
        nx = x + dx * k
        ny = y + dy * k
        if 0 <= nx < SIZE and 0 <= ny < SIZE and board[ny * SIZE + nx] == c:
            n += 1
    return n


def encode_one(pos, idx: int, color: int) -> list[float]:
    board = pos.board
    x, y = idx % SIZE, idx // SIZE
    opp = 3 - color
    occupied = pos.occupied
    f: list[float] = []

    my_runs: list[int] = []
    opp_runs: list[int] = []
    my_threes = 0
    my_fours = 0
    opp_threes = 0
    opp_fours = 0

    for dx, dy in DIRS:
        run, ends = _line(board, x, y, dx, dy, color)
        my_runs.append(run)
        f.append(run / 5.0)
        f.append(ends / 2.0)
        if run == 3 and ends == 2:
            my_threes += 1
        if run == 4:
            my_fours += 1

        orun, oends = _line(board, x, y, dx, dy, opp)
        opp_runs.append(orun)
        f.append(orun / 5.0)
        f.append(oends / 2.0)
        if orun == 3 and oends == 2:
            opp_threes += 1
        if orun == 4:
            opp_fours += 1

        f.append((_density(board, x, y, dx, dy, color) - _density(board, x, y, dx, dy, opp)) / 9.0)

    attack = pos.point_value(idx, color)
    defence = pos.point_value(idx, opp)
    mine = pos.total[color]
    theirs = pos.total[opp]
    denom = mine + theirs + 1.0

    # 全局特征：手数、离中腹的距离、周围棋子密度、双方最长连、双威胁计数
    f.append(occupied / CELLS)
    f.append(max(0, 14 - (abs(x - 7) + abs(y - 7))) / 14.0)
    f.append(min(pos.neighbor[idx], 16) / 16.0)
    f.append(max(my_runs) / 5.0)
    f.append(max(opp_runs) / 5.0)
    f.append(my_threes / 4.0)
    f.append(my_fours / 4.0)
    f.append(opp_threes / 4.0)
    f.append(opp_fours / 4.0)
    # 整盘棋形分能到千万级，直接喂进去会把其它特征全淹掉，取对数压到 0~1
    f.append(math.log1p(attack) / SCORE_SCALE)
    f.append(math.log1p(defence) / SCORE_SCALE)
    f.append(mine / denom)
    f.append(theirs / denom)
    # 这一手是「进攻更值」还是「防守更急」，相对量比两个绝对值有信息量
    f.append((attack - defence) / (attack + defence + 1.0))
    return f


def encode(pos, idxs: list[int], color: int) -> list[list[float]]:
    return [encode_one(pos, i, color) for i in idxs]


def _mat(vec: list[float], w: list[list[float]], b: list[float]) -> list[float]:
    out = []
    for row, bias in zip(w, b):
        s = bias
        for v, k in zip(vec, row):
            s += v * k
        out.append(s)
    return out


class Net:
    def __init__(self, path: Path = WEIGHTS_FILE) -> None:
        self.ready = False
        self.W1: list[list[float]] = []
        self.b1: list[float] = []
        self.W2: list[list[float]] = []
        self.b2: list[float] = []
        self.W3: list[list[float]] = []
        self.b3: list[float] = []
        self.meta: dict = {}
        self._load(path)

    def _load(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        try:
            self.W1, self.b1 = data["W1"], data["b1"]
            self.W2, self.b2 = data["W2"], data["b2"]
            self.W3, self.b3 = data["W3"], data["b3"]
        except KeyError:
            return
        if len(self.W1[0]) != FEATURES:
            return
        self.meta = data.get("meta", {})
        self.ready = bool(self.W1 and self.W2 and self.W3)

    def score(self, feats: list[float]) -> float:
        h1 = _mat(feats, self.W1, self.b1)
        h1 = [math.tanh(v) for v in h1]
        h2 = _mat(h1, self.W2, self.b2)
        h2 = [math.tanh(v) for v in h2]
        z = _mat(h2, self.W3, self.b3)[0]
        if z < -30:
            return 0.0
        if z > 30:
            return 1.0
        return 1.0 / (1.0 + math.exp(-z))

    def describe(self) -> dict:
        return {
            "ready": self.ready,
            "features": FEATURES,
            "hidden": [len(self.b1), len(self.b2)],
            "params": sum(len(r) for r in self.W1) + len(self.b1)
            + sum(len(r) for r in self.W2) + len(self.b2)
            + sum(len(r) for r in self.W3) + len(self.b3),
            **self.meta,
        }


_NET: Net | None = None


def get_net() -> Net:
    global _NET
    if _NET is None:
        _NET = Net()
    return _NET
