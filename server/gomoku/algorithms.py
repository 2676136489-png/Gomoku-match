"""内置算法。

竞技场和代码实验室都用同一个入口：给局面，返回一个合法落点。
四路实现刻意拉开层次，好让人看清「搜索」到底带来了什么：

- random  随机合法点。基线，用来证明其它三个不是靠运气。
- greedy  只算当前这一手的棋形分，不看对手后面的应手。快，也短视。
- minimax negamax + alpha-beta，深度按难度走。
- neural  直接跑落点价值网络，不做搜索。

四者都遵守同一条硬约束：连珠规则下黑棋不得落在禁手点。过滤放在选区之后
惰性做——排序一次、从高到低逐个验，命中第一个合法的就收手，通常只验一次。
"""

from __future__ import annotations

import random
import time

from . import net, rules, shapes
from .position import BLACK, CELLS, OPPONENT, WEIGHT, Position, coord
from .search import Searcher

ALGORITHMS = {
    "random": {
        "label": "随机",
        "brief": "在合法点里随机选。它的作用是当基线：任何算法打不过它，就说明实现有问题。",
        "tag": "基线",
        "search": False,
        "speed": "即时",
    },
    "greedy": {
        "label": "贪心",
        "brief": "只评估当前这一手的棋形得分，己方进攻分加对手同点的防守分。看得见当下，看不见两步之后。",
        "tag": "无搜索",
        "search": False,
        "speed": "约 10 ms",
    },
    "minimax": {
        "label": "极小化极大",
        "brief": "negamax + alpha-beta + 置换表 + 迭代加深。会算对手的应手，所以能发现两步杀。",
        "tag": "搜索",
        "search": True,
        "speed": "0.3–3.5 s",
    },
    "neural": {
        "label": "神经网络",
        "brief": "三层全连接给候选点打分，权重来自以搜索为教师的自对弈蒸馏；选定 Top-K 后逐点试下、"
        "再用网络评估对手最强应手，取净价值最高的一手。",
        "tag": "学习",
        "search": True,
        "speed": "约 60 ms",
    },
}

DEFAULT = "greedy"


def _legal(pos: Position, idx: int, color: int) -> bool:
    if pos.board[idx] != 0:
        return False
    if pos.rule == rules.RENJU and color == BLACK and pos.forbidden(idx, color):
        return False
    return True


def _candidates(pos: Position, color: int) -> list[int]:
    spots = pos.candidate_list()
    if not spots:
        return [i for i in range(CELLS) if pos.board[i] == 0]
    return spots


def _first_legal(pos: Position, ranked: list[int], color: int) -> int:
    for idx in ranked:
        if _legal(pos, idx, color):
            return idx
    # 候选全是禁手点时才走到这里，退回任意空点
    for i in range(CELLS):
        if pos.board[i] == 0:
            return i
    return 112


def _random(pos: Position, color: int, _difficulty: int) -> tuple[int, str]:
    spots = [i for i in _candidates(pos, color) if _legal(pos, i, color)]
    if not spots:
        return 112, "无合法点"
    idx = random.choice(spots)
    return idx, "随机取一个合法点"


def _greedy(pos: Position, color: int, _difficulty: int) -> tuple[int, str]:
    opp = OPPONENT[color]
    spots = _candidates(pos, color)
    scored = []
    for idx in spots:
        attack = pos.point_value(idx, color)
        defence = pos.point_value(idx, opp)
        # 开局时几乎所有点的棋形分都是 0，只按分数排会退化成按索引倒序，
        # 第一手直接落到棋盘角落。次级键只在前者相等时生效，不改变主排序。
        x, y = idx % 15, idx // 15
        tie = pos.neighbor[idx] * 3 + (14 - (abs(x - 7) + abs(y - 7)))
        scored.append((attack + int(defence * 0.9), tie, idx, attack, defence))
    scored.sort(reverse=True)
    best = scored[0] if scored else (0, 0, 112, 0, 0)
    _, _, idx, attack, defence = best
    idx = _first_legal(pos, [s[2] for s in scored], color)
    label = shapes.SHAPE_NAMES[max(pos.shapes_at(idx, color))]
    return idx, f"棋形分 {label}，进攻 {attack} / 防守 {defence}"


def _minimax(pos: Position, color: int, difficulty: int) -> tuple[int, str]:
    from .agent import DIFFICULTY

    level = DIFFICULTY.get(difficulty, DIFFICULTY[3])
    searcher = Searcher(time_limit=level["time"], max_depth=level["depth"])
    result = searcher.search(pos, color)
    x, y = coord(result.move)
    if result.reason:
        return result.move, f"{result.reason}（{x},{y}）"
    return result.move, f"深度 {result.depth}，{result.nodes} 个节点"


def _five_points(pos: Position, color: int) -> list[int]:
    """这一手就能连五的点。

    成五点必然邻接已有棋子，所以只需扫 cands。判断走 point_value——
    它内部只要任一方向成五就直接返回 WEIGHT[FIVE]，不会继续累加，
    拿来做阈值比较是准的。
    """
    out = []
    for idx in pos.candidate_list():
        if pos.board[idx] == 0 and pos.point_value(idx, color) >= WEIGHT[shapes.FIVE]:
            out.append(idx)
    return out


# 前瞻宽度按难度走。0 表示不做前瞻，纯策略网——留这一档是为了能直接
# 对比「同样一份权重，有没有前瞻差多少」，而不是只能听描述。
NEURAL_WIDTH = {1: 0, 2: 3, 3: 6, 4: 10}

# 对手应手分的折扣。取小于 1 是因为对手不一定真下得成——他要考虑我下一步，
# 而这里只算他这一步的净收益。0.75 是试出来的：太高会变成纯防守，太低又会漏防。
NEURAL_REPLY_DISCOUNT = 0.75


def _neural(pos: Position, color: int, difficulty: int) -> tuple[int, str]:
    model = net.get_net()
    if not model.ready:
        return _greedy(pos, color, difficulty)

    opp = OPPONENT[color]

    # 战术层。这两件事是确定性的，不该交给网络去赌——它只有 2881 个参数，
    # 学不会「这里正好四个子连成一条线」这种硬事实，试下才知道。
    mine = _five_points(pos, color)
    if mine:
        return mine[0], "连五"
    theirs = _five_points(pos, opp)
    if theirs:
        return theirs[0], "堵对手连五"

    spots = _candidates(pos, color)
    feats = net.encode(pos, spots, color)
    scored = sorted(
        ((model.score(f), idx) for f, idx in zip(feats, spots)), reverse=True
    )
    ranked = [idx for _, idx in scored]

    width = NEURAL_WIDTH.get(difficulty, NEURAL_WIDTH[3])
    if width <= 0:
        idx = _first_legal(pos, ranked, color)
        top = scored[0][0] if scored else 0.0
        return idx, f"网络置信度 {top:.3f}（无前瞻）"

    # 前瞻层：把前 K 手逐一试下，再让网络以对手视角给全盘应手打分，
    # 取其中最高的一个当作「我下这里之后要挨的最重一拳」。
    shortlist = [(s, i) for s, i in scored if _legal(pos, i, color)][:width]
    if not shortlist:
        return _first_legal(pos, ranked, color), "候选全是禁手点，退回空点"

    best_idx, best_value, best_note = shortlist[0][1], None, ""
    for my_score, idx in shortlist:
        pos.place(idx, color)
        try:
            opp_spots = _candidates(pos, opp)
            reply = 0.0
            if opp_spots:
                of = net.encode(pos, opp_spots, opp)
                reply = max(model.score(f) for f in of)
        finally:
            pos.undo()
        value = my_score - NEURAL_REPLY_DISCOUNT * reply
        if best_value is None or value > best_value:
            best_value, best_idx = value, idx
            best_note = f"自评 {my_score:.3f} − 0.75×对手 {reply:.3f}"

    return best_idx, f"净价值 {best_value:.3f}（{best_note}）"


# 网络先验给搜索留的池子宽度。取 12：中盘 cands 通常四五十个，
# 砍到 12 之后根节点每层少展开三倍多，迭代加深能多跑一到两层。
_IMPL = {
    "random": _random,
    "greedy": _greedy,
    "minimax": _minimax,
    "neural": _neural,
}


def choose(key: str, pos: Position, color: int, *, difficulty: int = 3) -> dict:
    key = key if key in _IMPL else DEFAULT
    started = time.perf_counter()
    idx, note = _IMPL[key](pos, color, difficulty)
    x, y = coord(idx)
    return {
        "algorithm": key,
        "idx": idx,
        "x": x,
        "y": y,
        "note": note,
        "ms": int((time.perf_counter() - started) * 1000),
    }


def describe() -> list[dict]:
    from .agent import DIFFICULTY

    model = net.get_net()
    out = []
    for key, meta in ALGORITHMS.items():
        item = {"key": key, **meta}
        if key == "neural":
            item["ready"] = model.ready
            item["params"] = model.describe()["params"]
        if key == "minimax":
            item["levels"] = [
                {"level": k, "label": v["label"], "depth": v["depth"]}
                for k, v in sorted(DIFFICULTY.items())
            ]
        out.append(item)
    return out
