"""国际连珠（Renju）规则。

黑棋有禁手、白棋没有，这是连珠和"随便下"最根本的区别。禁手分三种：

    长连  一手落下形成六子及以上的连珠
    四四  一手同时形成两个「四」
    三三  一手同时形成两个「活三」

三者的优先级不一样，而且会和胜负互相压制：

    长连  >  五连  >  三三 / 四四

黑棋一手既成五又成四四，算赢（五连优先）；既成五又成长连，算输（长连禁手优先）。
所以判定不能"先查禁手再查胜负"，必须一次算完再按优先级取舍。

最绕的是三三。一个「三」要成立，必须存在一个补点能把它变成活四，
而这个补点本身不能是黑棋的禁手点 —— 否则就是假活三，不能算数。
这条规则让三三判定带上了递归：判三先判四，判四又要判它会不会是禁手。
递归最多做两层，因为每补一子棋盘就少一个空点，不可能绕回去。

「四」的计数也有坑：活四两头都能成五，但那是同一个四。
所以这里用「四颗子的位置集合」去重，而不是数成五点的个数 —— 数点会把活四算成两个四，
任何活四都被判成四四禁手。

棋盘用 15×15 的 bytearray，索引 = y * 15 + x。假设棋通过一层 patch 叠加，
不复制整块棋盘：判三三时最多叠加两子，拷 225 字节比查字典慢得多。
"""

from __future__ import annotations

from dataclasses import dataclass, field

SIZE = 15
EMPTY, BLACK, WHITE = 0, 1, 2
OUT = -1

# 横、竖、右下、左下。四个方向各管一条轴线，正反都在同一条线上。
DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))

FREESTYLE = "freestyle"
RENJU = "renju"

LONG = "long"
FIVE = "five"
DOUBLE_FOUR = "double-four"
DOUBLE_THREE = "double-three"

BAN_LABELS = {
    LONG: "长连禁手",
    DOUBLE_FOUR: "四四禁手",
    DOUBLE_THREE: "三三禁手",
}

RULE_MODES = [
    {
        "key": RENJU,
        "label": "国际连珠",
        "brief": "黑棋禁手：长连、四四、三三。白棋无禁手，长连算白胜。",
    },
    {
        "key": FREESTYLE,
        "label": "自由规则",
        "brief": "无禁手，黑白都是五子及以上连珠即胜。",
    },
]

# 三三判定的递归深度。2 层已经能覆盖实战里出现过的假活三，
# 再深下去收益极小，而代价是每层都要重算一遍四。
RECURSION_DEPTH = 2


class _Stones:
    """棋盘 + 若干假设落点。

    假设落点用 patch 叠加而不是复制棋盘：判三三要临时补一到两子，
    复制 225 字节的 board 在热路径上太贵。
    """

    __slots__ = ("board", "extra")

    def __init__(self, board, extra: dict[int, int] | None = None) -> None:
        self.board = board
        self.extra = extra or {}

    def at(self, idx: int) -> int:
        extra = self.extra
        if extra and idx in extra:
            return extra[idx]
        return self.board[idx]

    def put(self, idx: int, color: int) -> "_Stones":
        extra = dict(self.extra)
        extra[idx] = color
        return _Stones(self.board, extra)


@dataclass
class Verdict:
    """一手棋的判定结果。"""

    kind: str | None = None
    winner: int | None = None
    line: list[int] = field(default_factory=list)
    label: str = ""

    @property
    def finished(self) -> bool:
        return self.winner is not None


def _cell(stones: _Stones, x: int, y: int) -> int:
    if 0 <= x < SIZE and 0 <= y < SIZE:
        return stones.at(y * SIZE + x)
    return OUT


def _bounds(stones: _Stones, x: int, y: int, dx: int, dy: int, color: int) -> tuple[int, int]:
    """(x,y) 所在同色连在轴线上的起止偏移。两端越界返回 OUT 的格子不算同色。"""
    lo = 0
    while _cell(stones, x + dx * (lo - 1), y + dy * (lo - 1)) == color:
        lo -= 1
    hi = 0
    while _cell(stones, x + dx * (hi + 1), y + dy * (hi + 1)) == color:
        hi += 1
    return lo, hi


def _run(stones: _Stones, x: int, y: int, dx: int, dy: int, color: int) -> int:
    lo, hi = _bounds(stones, x, y, dx, dy, color)
    return hi - lo + 1


def _run_cells(stones: _Stones, x: int, y: int, dx: int, dy: int, color: int) -> list[int]:
    lo, hi = _bounds(stones, x, y, dx, dy, color)
    return [(y + dy * k) * SIZE + (x + dx * k) for k in range(lo, hi + 1)]


# ---------------------------------------------------------------- 四


def _four_sets(stones: _Stones, x: int, y: int, color: int) -> set[frozenset[int]]:
    """这一手形成的「四」，用四颗子的位置集合表示。

    一个四是 5 格窗口里的 4 颗同色 + 1 个空点，补上空点恰好成五。
    活四会命中两个窗口（左窗口、右窗口），但四颗子是同一组，集合去重后只算一个。
    """
    found: set[frozenset[int]] = set()
    for dx, dy in DIRS:
        # 含 (x,y) 的 5 格窗口，起点相对落点从 -4 到 0
        for offset in range(-4, 1):
            cells = []
            for j in range(5):
                cx, cy = x + dx * (offset + j), y + dy * (offset + j)
                if not (0 <= cx < SIZE and 0 <= cy < SIZE):
                    cells = []
                    break
                cells.append(cy * SIZE + cx)
            if not cells:
                continue

            vals = [stones.at(i) for i in cells]
            if vals.count(color) != 4 or vals.count(EMPTY) != 1:
                continue

            gap = cells[vals.index(EMPTY)]
            gx, gy = gap % SIZE, gap // SIZE
            # 补上这个空点必须恰好成五。补成六说明这条线本来就死了，不算四。
            if _run(stones.put(gap, color), gx, gy, dx, dy, color) != 5:
                continue

            found.add(frozenset(i for i, v in zip(cells, vals) if v == color))
    return found


# ---------------------------------------------------------------- 三


def _open_four(stones: _Stones, x: int, y: int, dx: int, dy: int, color: int) -> list[int] | None:
    """(x,y) 是该方向上的子；若这条线构成长度 4 且两头皆空，返回四颗子。"""
    lo, hi = _bounds(stones, x, y, dx, dy, color)
    if hi - lo + 1 != 4:
        return None
    if _cell(stones, x + dx * (lo - 1), y + dy * (lo - 1)) != EMPTY:
        return None
    if _cell(stones, x + dx * (hi + 1), y + dy * (hi + 1)) != EMPTY:
        return None
    return [(y + dy * k) * SIZE + (x + dx * k) for k in range(lo, hi + 1)]


def _three_sets(stones: _Stones, x: int, y: int, color: int, depth: int) -> set[frozenset[int]]:
    """这一手形成的「活三」，用三颗子的位置集合表示。

    做法是找补点：在哪个空点补一子，能让这条线（且包含本手）变成活四。
    找到之后还要验证补点本身不是禁手点 —— 是的话这就是假活三，不算。
    """
    found: set[frozenset[int]] = set()
    center = y * SIZE + x

    for dx, dy in DIRS:
        for k in range(-4, 5):
            if k == 0:
                continue
            ex, ey = x + dx * k, y + dy * k
            if not (0 <= ex < SIZE and 0 <= ey < SIZE):
                continue
            gap = ey * SIZE + ex
            if stones.at(gap) != EMPTY:
                continue

            after = stones.put(gap, color)
            four = _open_four(after, ex, ey, dx, dy, color)
            if four is None or center not in four:
                continue

            # 假活三：补成活四的那一点本身是黑棋禁手，这个三不成立
            if depth > 0 and _forbidden(after, ex, ey, color, depth - 1) is not None:
                continue

            found.add(frozenset(i for i in four if i != gap))

    return found


# ---------------------------------------------------------------- 禁手


def _forbidden(stones: _Stones, x: int, y: int, color: int, depth: int) -> str | None:
    """(x,y) 已经是 color 的子。返回禁手原因，否则 None。

    优先级：长连 > 五连 > 四四 > 三三。
    五连优先于四四和三三，是因为"成五"本身就是终局，规则不让它被禁手盖掉。
    """
    five = False
    for dx, dy in DIRS:
        n = _run(stones, x, y, dx, dy, color)
        if n >= 6:
            return LONG
        if n == 5:
            five = True
    if five:
        return None

    if len(_four_sets(stones, x, y, color)) >= 2:
        return DOUBLE_FOUR
    if len(_three_sets(stones, x, y, color, depth)) >= 2:
        return DOUBLE_THREE
    return None


def forbidden(board, idx: int, *, color: int = BLACK, rule: str = RENJU, depth: int = RECURSION_DEPTH) -> str | None:
    """空点 idx 上黑棋落子是否禁手。board 中该点必须为空。"""
    if rule != RENJU or color != BLACK:
        return None
    if board[idx] != EMPTY:
        return None

    x, y = idx % SIZE, idx // SIZE
    stones = _Stones(board).put(idx, color)

    # 连不成三的点不可能是禁手。这条挡掉九成以上的候选，是性能的关键。
    if max(_run(stones, x, y, dx, dy, color) for dx, dy in DIRS) < 3:
        return None

    return _forbidden(stones, x, y, color, depth)


# ---------------------------------------------------------------- 胜负


def judge(board, idx: int, color: int, rule: str = RENJU) -> Verdict:
    """board 中 idx 处已经落了 color 的子，判定这一手的结果。"""
    x, y = idx % SIZE, idx // SIZE
    stones = _Stones(board)

    longest = 0
    line: list[int] = []
    for dx, dy in DIRS:
        cells = _run_cells(stones, x, y, dx, dy, color)
        if len(cells) > longest:
            longest, line = len(cells), cells

    if rule != RENJU:
        if longest >= 5:
            return Verdict(FIVE, color, line, f"{'黑' if color == BLACK else '白'}棋连成 {longest} 子")
        return Verdict()

    if color == WHITE:
        # 白棋没有禁手，长连也算胜
        if longest >= 5:
            label = "白棋长连，白胜" if longest > 5 else "白棋连五"
            return Verdict(FIVE, WHITE, line, label)
        return Verdict()

    if longest >= 6:
        return Verdict(LONG, WHITE, line, f"黑棋长连 {longest} 子，判负")
    if longest == 5:
        return Verdict(FIVE, BLACK, line, "黑棋连五")

    ban = _forbidden(stones, x, y, color, RECURSION_DEPTH)
    if ban:
        return Verdict(ban, WHITE, [], f"黑棋{BAN_LABELS[ban]}，判负")
    return Verdict()


def banned_points(board, points, rule: str = RENJU) -> list[dict]:
    """一批空点里，黑棋不能下的那些。给前端标红叉用。"""
    if rule != RENJU:
        return []
    out = []
    for idx in points:
        reason = forbidden(board, idx, rule=rule)
        if reason:
            out.append(
                {
                    "x": idx % SIZE,
                    "y": idx // SIZE,
                    "kind": reason,
                    "label": BAN_LABELS[reason],
                }
            )
    return out
