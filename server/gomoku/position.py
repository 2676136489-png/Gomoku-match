"""棋盘状态与增量评估。

棋盘用一维 bytearray 存，索引 = y * 15 + x。
落子和撤销会增量维护两个量：

1. total[color] —— 整盘该颜色的静态分，等于其所有棋子单点得分之和
2. cands        —— 候选落点集合，只包含已有棋子周围 2 格内的空点

增量维护是搜索能跑到深度 4 的前提。如果每个叶子节点都重扫全盘，
Python 这点速度连深度 3 都撑不住。
"""

from __future__ import annotations

import random

from . import rules, shapes
from .shapes import BLOCK_CH, CENTER, EMPTY_CH, OWN_CH

SIZE = 15
CELLS = SIZE * SIZE

EMPTY = 0
BLACK = 1
WHITE = 2

OPPONENT = {BLACK: WHITE, WHITE: BLACK}

# 方向：横、竖、右下、左下。每个方向取以落点为中心的 11 格窗口，
# 一个窗口同时覆盖轴线两侧，所以 4 个方向就够，不需要 8 个。
DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))

# 棋形基础分值
WEIGHT = {
    shapes.NONE: 0,
    shapes.TWO: 30,
    shapes.OPEN_TWO: 100,
    shapes.THREE: 350,
    shapes.OPEN_THREE: 1_500,
    shapes.FOUR: 15_000,
    shapes.OPEN_FOUR: 300_000,
    shapes.FIVE: 10_000_000,
}

# 单点上的多重威胁加成
BONUS_OPEN_FOUR = 400_000
BONUS_DOUBLE_FOUR = 250_000
BONUS_FOUR_THREE = 180_000
BONUS_DOUBLE_THREE = 20_000

WIN_SCORE = 10_000_000

_rng = random.Random(20240614)
ZOBRIST = [[_rng.getrandbits(64) for _ in range(3)] for _ in range(CELLS)]

_RING_CACHE: dict[tuple[int, int], list[int]] = {}


def _ring(idx: int, radius: int) -> list[int]:
    """返回 idx 周围切比雪夫距离 <= radius 的其它格子索引。"""
    key = (idx, radius)
    cached = _RING_CACHE.get(key)
    if cached is not None:
        return cached

    x, y = idx % SIZE, idx // SIZE
    out = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < SIZE and 0 <= ny < SIZE:
                out.append(ny * SIZE + nx)
    _RING_CACHE[key] = out
    return out


class Position:
    """一局棋的可变状态。搜索时反复 place / undo，都是在同一个对象上做。"""

    __slots__ = (
        "board",
        "neighbor",
        "cands",
        "total",
        "contrib",
        "history",
        "hash",
        "occupied",
        "rule",
    )

    def __init__(self, rule: str = rules.RENJU) -> None:
        self.rule = rule
        self.board = bytearray(CELLS)
        self.neighbor = [0] * CELLS
        self.cands: set[int] = set()
        self.total = [0, 0, 0]
        self.contrib: dict[int, tuple[int, int]] = {}
        self.history: list[tuple[int, int, list[int]]] = []
        self.hash = 0
        self.occupied = 0

    # ---------------------------------------------------------------- 基础操作

    def clone(self) -> "Position":
        p = Position(self.rule)
        p.board = bytearray(self.board)
        p.neighbor = list(self.neighbor)
        p.cands = set(self.cands)
        p.total = list(self.total)
        p.contrib = dict(self.contrib)
        p.history = []
        p.hash = self.hash
        p.occupied = self.occupied
        return p

    def place(self, idx: int, color: int) -> None:
        affected = self._affected(idx)
        # 先把受影响棋子的旧贡献从总分里扣掉，落子后再整体重算。
        self._unrefresh(affected)

        self.board[idx] = color
        self.hash ^= ZOBRIST[idx][color]
        self.occupied += 1

        for j in _ring(idx, 2):
            self.neighbor[j] += 1
        self.cands.discard(idx)
        for j in _ring(idx, 2):
            if self.board[j] == 0:
                self.cands.add(j)

        self._refresh(affected)
        self.history.append((idx, color, affected))

    def undo(self) -> None:
        idx, color, affected = self.history.pop()
        self._unrefresh(affected)
        self.board[idx] = 0
        self.hash ^= ZOBRIST[idx][color]
        self.occupied -= 1

        for j in _ring(idx, 2):
            self.neighbor[j] -= 1
        for j in _ring(idx, 2):
            if self.board[j] == 0 and self.neighbor[j] <= 0:
                self.cands.discard(j)
        if self.neighbor[idx] > 0:
            self.cands.add(idx)

        rest = [k for k in affected if k != idx]
        if rest:
            self._refresh(rest)

    # ---------------------------------------------------------------- 增量评分

    def _affected(self, idx: int) -> list[int]:
        """落子会影响半径 4 以内已有棋子的棋形（五个子最多横跨 4 格）。

        四个方向都要往两侧走——只走正向会漏掉左/上/左上/右上四个半轴，
        增量分就会慢慢漂。
        """
        pts = [idx]
        x, y = idx % SIZE, idx // SIZE
        for dx, dy in DIRS:
            for sign in (1, -1):
                for k in range(1, 5):
                    nx, ny = x + dx * k * sign, y + dy * k * sign
                    if not (0 <= nx < SIZE and 0 <= ny < SIZE):
                        break
                    j = ny * SIZE + nx
                    if self.board[j]:
                        pts.append(j)
        return pts

    def _refresh(self, idxs: list[int]) -> None:
        for k in idxs:
            if not self.board[k]:
                continue
            b, w = self._stone_value(k, BLACK), self._stone_value(k, WHITE)
            self.contrib[k] = (b, w)
            self.total[BLACK] += b
            self.total[WHITE] += w

    def _unrefresh(self, idxs: list[int]) -> None:
        for k in idxs:
            v = self.contrib.pop(k, None)
            if v is not None:
                self.total[BLACK] -= v[0]
                self.total[WHITE] -= v[1]

    def _window(self, idx: int, dx: int, dy: int, color: int) -> str:
        x, y = idx % SIZE, idx // SIZE
        out = []
        append = out.append
        board = self.board
        for k in range(-CENTER, CENTER + 1):
            nx, ny = x + dx * k, y + dy * k
            if 0 <= nx < SIZE and 0 <= ny < SIZE:
                v = board[ny * SIZE + nx]
                if v == 0:
                    append(EMPTY_CH)
                elif v == color:
                    append(OWN_CH)
                else:
                    append(BLOCK_CH)
            else:
                append(BLOCK_CH)
        return "".join(out)

    def shapes_at(self, idx: int, color: int) -> list[int]:
        """该点四个方向现有的棋形等级。空点就是空的，不会替它补子。"""
        return [shapes.rank(self._window(idx, dx, dy, color)) for dx, dy in DIRS]

    def shapes_if_placed(self, idx: int, color: int) -> list[int]:
        """假设在空点 idx 落下 color 的子，四个方向的棋形等级。

        和 shapes_at 的差别就是「补不补这一子」，但结论差很远：
        对着一串四连问 shapes_at，它看到的是「这一线目前是冲四」，
        补上中心才看得出「落下即连五」。凡是回答「这一手会形成什么」的地方
        都必须用这个，否则连五永远判不出来。
        """
        out = []
        for dx, dy in DIRS:
            line = self._window(idx, dx, dy, color)
            out.append(shapes.rank(line[:CENTER] + OWN_CH + line[CENTER + 1 :]))
        return out

    def _stone_value(self, idx: int, color: int) -> int:
        ranks = self.shapes_at(idx, color)
        value = 0
        fours = 0
        threes = 0
        open_four = False
        for r in ranks:
            value += WEIGHT[r]
            if r == shapes.FIVE:
                return WEIGHT[shapes.FIVE]
            if r == shapes.OPEN_FOUR:
                open_four = True
                fours += 1
            elif r == shapes.FOUR:
                fours += 1
            elif r == shapes.OPEN_THREE:
                threes += 1

        # 多重威胁不是四个方向的分数相加——它们互为杀招，
        # 一条线上有两个活三和两条线上各有一个活三，对对手来说不是一回事。
        if open_four:
            value += BONUS_OPEN_FOUR
        if fours >= 2:
            value += BONUS_DOUBLE_FOUR
        elif fours >= 1 and threes >= 1:
            value += BONUS_FOUR_THREE
        if threes >= 2:
            value += BONUS_DOUBLE_THREE
        return value

    def point_value(self, idx: int, color: int) -> int:
        """假设 color 在该空点落子，能形成的棋形价值。用于落点排序和前端提示。"""
        v = self._stone_value(idx, color)
        return v

    def evaluate(self, color: int) -> int:
        """从 color 视角看整盘局势。"""
        me = self.total[color]
        op = self.total[OPPONENT[color]]
        return me - int(op * 1.05)

    # ---------------------------------------------------------------- 胜负

    def run_line(self, idx: int, color: int) -> list[int]:
        """过该点的最长同色连（返回全部坐标）。"""
        x, y = idx % SIZE, idx // SIZE
        board = self.board
        best: list[int] = []
        for dx, dy in DIRS:
            run = [idx]
            for sign in (1, -1):
                k = 1
                while True:
                    nx, ny = x + dx * k * sign, y + dy * k * sign
                    if not (0 <= nx < SIZE and 0 <= ny < SIZE):
                        break
                    j = ny * SIZE + nx
                    if board[j] != color:
                        break
                    run.append(j)
                    k += 1
            if len(run) > len(best):
                best = run
        best.sort()
        return best

    def run_length(self, idx: int, color: int) -> int:
        """搜索里每落一子都会调，所以刻意不分配任何临时对象。"""
        x, y = idx % SIZE, idx // SIZE
        board = self.board
        longest = 1
        for dx, dy in DIRS:
            cnt = 1
            for sign in (1, -1):
                k = 1
                while True:
                    nx, ny = x + dx * k * sign, y + dy * k * sign
                    if not (0 <= nx < SIZE and 0 <= ny < SIZE):
                        break
                    if board[ny * SIZE + nx] != color:
                        break
                    cnt += 1
                    k += 1
            if cnt > longest:
                longest = cnt
        return longest

    def win_kind(self, idx: int, color: int) -> str | None:
        """这一手连成了几子：五连记 five，六子及以上记 long。不连成返回 None。

        连珠规则下黑棋的 long 是禁手判负、白棋的 long 是胜，两者的区别由调用方
        按规则处理，这里只报告事实。
        """
        n = self.run_length(idx, color)
        if n < 5:
            return None
        return rules.FIVE if n == 5 else rules.LONG

    def is_win(self, idx: int, color: int) -> bool:
        """自由规则下的胜负。连珠请用 win_kind —— 黑棋长连不算赢。"""
        return self.run_length(idx, color) >= 5

    def win_line(self, idx: int, color: int) -> list[int]:
        return self.run_line(idx, color)

    # ---------------------------------------------------------------- 禁手

    def forbidden(self, idx: int, color: int = BLACK) -> str | None:
        """连珠规则下黑棋在空点 idx 是否禁手。"""
        return rules.forbidden(self.board, idx, color=color, rule=self.rule)

    def banned_points(self) -> list[dict]:
        """当前局面黑棋不能下的点。只扫已有棋子附近的候选点 —— 禁手至少要有三子相连。"""
        if self.rule != rules.RENJU:
            return []
        return rules.banned_points(self.board, self.candidate_list(), self.rule)

    # ---------------------------------------------------------------- 候选点

    def candidate_list(self) -> list[int]:
        return [i for i in self.cands if self.board[i] == 0]

    def empty_count(self) -> int:
        return CELLS - self.occupied


def coord(idx: int) -> tuple[int, int]:
    return idx % SIZE, idx // SIZE
