"""搜索。

negamax + alpha-beta，配 Zobrist 置换表、迭代加深和按静态分排序的候选截断。
时间预算硬性生效，超时就从迭代加深的上一层结果返回。

没有引 VCF / VCT 专项搜索。深度 4 加上棋形评估已经能覆盖绝大多数
两步内的杀棋和反杀，再往上堆专项搜索的收益不如先把评估做对。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import rules, shapes
from .position import CELLS, OPPONENT, WEIGHT, Position, coord

INF = 1 << 30
WIN_SCORE = 9_000_000

# 剩余深度 -> 每层展开的候选点数量。越靠近叶子越窄，这是 Python 里能跑多深的关键。
WIDTH = {0: 6, 1: 6, 2: 8, 3: 10, 4: 12, 5: 14, 6: 16}

EXACT, LOWER, UPPER = 0, 1, 2


class SearchTimeout(Exception):
    pass


@dataclass
class RankedMove:
    idx: int
    score: int
    attack: int
    defence: int
    shapes: list[int] = field(default_factory=list)


@dataclass
class SearchResult:
    move: int
    score: int
    depth: int
    nodes: int
    elapsed: float
    ranking: list[RankedMove] = field(default_factory=list)
    reason: str = ""


class Searcher:
    def __init__(
        self,
        time_limit: float = 2.5,
        max_depth: int = 5,
        check_bans: bool = True,
    ) -> None:
        self.time_limit = time_limit
        self.max_depth = max_depth
        self.deadline = 0.0
        self.nodes = 0
        self.tt: dict[int, tuple[int, int, int, int]] = {}
        self._history: list[int] = []
        # 连珠规则下黑棋不能走禁手点。禁手判定比评估贵一个数量级，
        # 所以按 (局面哈希, 落点) 缓存 —— 迭代加深和置换表会反复回到同一个局面。
        self.check_bans = check_bans
        self.ban_depth = rules.RECURSION_DEPTH
        self.ban_cache: dict[tuple[int, int], str | None] = {}

    # ------------------------------------------------------------ 候选点排序

    def _banned(self, pos: Position, idx: int) -> bool:
        key = (pos.hash, idx)
        cache = self.ban_cache
        if key in cache:
            return cache[key] is not None
        reason = rules.forbidden(pos.board, idx, color=rules.BLACK, rule=pos.rule, depth=self.ban_depth)
        cache[key] = reason
        return reason is not None

    def rank_moves(
        self, pos: Position, color: int, limit: int, *, check_bans: bool | None = None
    ) -> list[RankedMove]:
        opp = OPPONENT[color]
        enabled = self.check_bans if check_bans is None else check_bans
        filter_bans = enabled and pos.rule == rules.RENJU and color == rules.BLACK
        out: list[RankedMove] = []
        for idx in pos.candidate_list():
            if filter_bans and self._banned(pos, idx):
                continue
            attack = pos.point_value(idx, color)
            defence = pos.point_value(idx, opp)
            # 进攻略高于防守。先手方靠这个权重维持主动，防守方不会被动挨打。
            score = attack * 10 + defence * 9

            # 开局阶段大量候选点的静态分完全相等，光靠棋形分排序会退化成
            # 「按 set 迭代顺序随便挑一个」，AI 就会把子撒到离战场很远的地方。
            # 加两项轻量修正做 tie-break：贴住已有棋子的接触点优先，其次靠中腹。
            x, y = idx % 15, idx // 15
            contact = pos.neighbor[idx] * 3
            center = 14 - (abs(x - 7) + abs(y - 7))
            score += contact + center

            out.append(RankedMove(idx, score, attack, defence))
        out.sort(key=lambda m: m.score, reverse=True)
        return out[:limit]

    def annotate(self, pos: Position, color: int, limit: int = 5) -> list[RankedMove]:
        """给 Agent 层用的候选说明，带四方向棋形。"""
        moves = self.rank_moves(pos, color, limit)
        for m in moves:
            m.shapes = pos.shapes_if_placed(m.idx, color)
        return moves

    # ------------------------------------------------------------ 主入口

    def search(self, pos: Position, color: int) -> SearchResult:
        start = time.perf_counter()
        self.deadline = start + self.time_limit
        self.nodes = 0
        self.tt.clear()
        self.ban_cache.clear()

        op = OPPONENT[color]

        if pos.occupied == 0:
            center = (7) * 15 + 7
            return SearchResult(center, 0, 0, 0, 0.0, [], "空盘开局")

        ranking = self.annotate(pos, color, 8)

        # 手算两条必胜捷径：自己能连五就直接赢，对手能连五就必须堵。
        # 放在这里是为了保证「杀」永远不会被深度不够的搜索漏掉。
        for m in ranking:
            if pos.point_value(m.idx, color) >= WEIGHT[shapes.FIVE]:
                return SearchResult(m.idx, WIN_SCORE, 1, 0, time.perf_counter() - start, ranking, "直接连五")
        for m in ranking:
            if pos.point_value(m.idx, op) >= WEIGHT[shapes.FIVE]:
                return SearchResult(m.idx, WIN_SCORE, 1, 0, time.perf_counter() - start, ranking, "封堵对手连五")

        best_move = ranking[0].idx if ranking else self._fallback(pos)
        best_score = -INF
        reached = 1

        # 迭代加深每一层都会 place / undo。超时异常可能从递归深处抛出来，
        # 绕过当层的 undo()，棋盘就会残留幽灵棋子。所以在这里兜底回滚。
        snapshot = len(pos.history)
        try:
            for depth in range(1, self.max_depth + 1):
                try:
                    move, score = self._root(pos, color, depth, best_move)
                except SearchTimeout:
                    break
                best_move, best_score, reached = move, score, depth
                if abs(score) >= WIN_SCORE:
                    break
        finally:
            while len(pos.history) > snapshot:
                pos.undo()

        return SearchResult(
            best_move,
            best_score,
            reached,
            self.nodes,
            time.perf_counter() - start,
            ranking,
        )

    def _fallback(self, pos: Position) -> int:
        for i in range(CELLS):
            if pos.board[i] == 0:
                return i
        return 112

    @staticmethod
    def _outcome(pos: Position, idx: int, color: int) -> int:
        """刚落下的这一手的终局分：1 己方胜、-1 己方负、0 未终局。

        连珠规则下黑棋六子及以上是长连禁手，直接判负。引擎必须看得见这条，
        否则到残局会主动去连第六子当赢棋。
        """
        kind = pos.win_kind(idx, color)
        if kind is None:
            return 0
        if kind == rules.FIVE:
            return 1
        return 1 if (color == rules.WHITE or pos.rule != rules.RENJU) else -1

    def _root(self, pos: Position, color: int, depth: int, hint: int) -> tuple[int, int]:
        moves = self.rank_moves(pos, color, WIDTH.get(depth, 16))
        if not moves:
            return self._fallback(pos), pos.evaluate(color)
        if hint != moves[0].idx:
            for i, m in enumerate(moves):
                if m.idx == hint:
                    moves.insert(0, moves.pop(i))
                    break

        alpha, beta = -INF, INF
        best_move, best_score = moves[0].idx, -INF
        op = OPPONENT[color]

        for m in moves:
            if time.perf_counter() > self.deadline:
                raise SearchTimeout

            pos.place(m.idx, color)
            outcome = self._outcome(pos, m.idx, color)
            score = outcome * (WIN_SCORE - 1) if outcome else -self._negamax(pos, depth - 1, -beta, -alpha, op)
            pos.undo()

            if score > best_score:
                best_score, best_move = score, m.idx
            if score > alpha:
                alpha = score
        return best_move, best_score

    def _negamax(self, pos: Position, depth: int, alpha: int, beta: int, color: int) -> int:
        self.nodes += 1
        if self.nodes & 0x3FF == 0 and time.perf_counter() > self.deadline:
            raise SearchTimeout

        key = pos.hash
        entry = self.tt.get(key)
        if entry is not None and entry[0] >= depth:
            _, flag, value, stored = entry
            if flag == EXACT:
                return value
            if flag == LOWER and value > alpha:
                alpha = value
            elif flag == UPPER and value < beta:
                beta = value
            if alpha >= beta:
                return value

        if depth <= 0:
            return pos.evaluate(color)

        moves = self.rank_moves(pos, color, WIDTH.get(depth, 6))
        if not moves:
            return pos.evaluate(color)

        op = OPPONENT[color]
        best = -INF
        best_move = moves[0].idx
        alpha0 = alpha

        for m in moves:
            pos.place(m.idx, color)
            outcome = self._outcome(pos, m.idx, color)
            score = outcome * (WIN_SCORE - depth) if outcome else -self._negamax(pos, depth - 1, -beta, -alpha, op)
            pos.undo()

            if score > best:
                best = score
                best_move = m.idx
            if score > alpha:
                alpha = score
            if alpha >= beta:
                break

        flag = EXACT
        if best <= alpha0:
            flag = UPPER
        elif best >= beta:
            flag = LOWER
        self.tt[key] = (depth, flag, best, best_move)
        return best


def describe_move(pos: Position, idx: int, color: int) -> str:
    """把一个落点翻译成人话，供解说面板和 LLM prompt 使用。"""
    x, y = coord(idx)
    ranks = pos.shapes_if_placed(idx, color)
    labels = [shapes.SHAPE_NAMES[r] for r in ranks if r >= shapes.OPEN_THREE]
    best = max(ranks) if ranks else shapes.NONE
    if best >= shapes.FIVE:
        return f"({x},{y}) 直接连五"
    if best >= shapes.OPEN_FOUR:
        return f"({x},{y}) 形成活四，对手无法同时封堵"
    if best == shapes.FOUR:
        return f"({x},{y}) 形成冲四"
    if labels:
        return f"({x},{y}) 形成{'/'.join(dict.fromkeys(labels))}"
    return f"({x},{y})"
