"""局面分析：胜率、点位预测、棋形普查、单步解说。

这一层不引入新的棋力判断，只把引擎内部已经在算的量翻译成人能看的数字。
每个数字都有出处——棋形评估、搜索或标定过的模型，没有一处是「看起来差不多」。

胜率分两级：能一眼断定的局面走战术判定（有成五点、有活四点、对手两个成五点），
剩下的模糊地带交给一个标定过的逻辑回归。模型权重由 tools/fit_winrate.py
用自对弈样本拟合，文件里存着样本量、AUC 和可靠性表。
文件缺失时退回一组手写权重并把来源标成 default —— clone 下来不跑训练脚本也能用。

为什么不让落点网络算胜率：它输出的是「这个点值得下吗」，不是「这盘棋谁会赢」。
上一轮量过，它作为主信号连贪心都打不过，作为排序先验才有用。
所以网络只进点位预测，不进胜率。

本模块是纯函数，不碰对局对象、不做缓存。集成层在 server/analysis.py。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import net as net_mod
from . import shapes
from .net import get_net
from .position import BLACK, CELLS, OPPONENT, WEIGHT, Position, coord, SIZE
from .search import Searcher

MODEL_FILE = Path(__file__).resolve().parent / "winrate_model.json"

# 特征顺序固定。模型文件里的 weights 按这个顺序解释，顺序一变就必须重训。
#
# 试过把「活三点数 / 冲四点数的差」也塞进去，AUC 只从 0.656 涨到 0.658，
# 而且系数是负的——棋形总量已经把威胁信息吸收掉了，剩下的残差反映的是
# 「我方空点中可造杀招的点位很多，说明对手未施加压力」，方向正好相反。
# 换来一个解释不清的系数不值这 0.002，因此只保留两个：主信号 + 盘面阶段。
FEATURES = (
    "log_ratio",  # 双方棋形总量之比的对数
    "occupancy",  # 已落子占比，用来区分同一比值在开局和中盘的含义
)

FEATURE_LABELS = {
    "log_ratio": "棋形总量",
    "occupancy": "盘面密度",
}

# 棋形总分能到千万级，直接取 log 会让开局阶段的微小差距被放大成几十个百分点的跳动。
# 先除以 500 再 log，等于把「一子之差」压在噪声范围内，把「一个冲四」留在显著区间。
MATERIAL_SCALE = 500.0

SHAPE_ORDER = ("活二", "眠三", "活三", "冲四", "活四", "连五")
RANK_OF = {name: rank for rank, name in shapes.SHAPE_NAMES.items()}
DIR_NAMES = ("横", "竖", "右下", "左下")

FIVE = WEIGHT[shapes.FIVE]


# --------------------------------------------------------------------- 模型


class WinrateModel:
    """二维逻辑回归。标准化参数一起存，推理时不用再猜量纲。"""

    def __init__(self) -> None:
        self.source = "default"
        self.meta: dict = {}
        # 手写的兜底权重：只用棋形总量的对数，斜率刻意压得比较平。
        # 它比标定过的模型钝，但方向不会错，而且 clone 下来就能用。
        self.weights: list[float] = [0.0, 0.6, 0.0]
        self.mean: list[float] = [0.0] * len(FEATURES)
        self.std: list[float] = [1.0] * len(FEATURES)
        self.predict_cfg = {"prior_weight": 2.2, "temperature": 1.0}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if list(data.get("features", [])) != list(FEATURES):
            # 特征顺序变了，旧权重就是错的。宁可退回默认值，也不要静默算错。
            return
        weights = data.get("weights", [])
        mean = data.get("mean", [])
        std = data.get("std", [])
        if (
            len(weights) != len(FEATURES) + 1
            or len(mean) != len(FEATURES)
            or len(std) != len(FEATURES)
        ):
            return
        self.weights = [float(v) for v in weights]
        self.mean = [float(v) for v in mean]
        self.std = [float(v) if abs(float(v)) > 1e-9 else 1.0 for v in std]
        self.meta = data.get("meta", {})
        cfg = data.get("predict", {})
        self.predict_cfg = {
            "prior_weight": float(cfg.get("prior_weight", 2.2)),
            "temperature": float(cfg.get("temperature", 1.0)) or 1.0,
        }
        self.source = "selfplay"

    def logit(self, values: list[float]) -> tuple[float, list[float]]:
        return logit_of(self.weights, self.mean, self.std, values)

    def describe(self) -> dict:
        # meta 先铺，显式字段后覆盖：标定产物里多写一个同名键，
        # 也不能把「权重从哪来」这种关键字段换掉。
        out = dict(self.meta)
        out.update(
            {
                "source": self.source,
                "features": list(FEATURES),
                "weights": [round(w, 4) for w in self.weights],
                "params": len(self.weights),
            }
        )
        return out


_MODEL: WinrateModel | None = None


def get_model() -> WinrateModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = WinrateModel()
    return _MODEL


def reload_model() -> WinrateModel:
    """训练脚本跑完之后让服务即时换上新权重，不用重启。"""
    global _MODEL
    _MODEL = WinrateModel()
    return _MODEL


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def logit_of(
    weights: list[float], mean: list[float], std: list[float], values: list[float]
) -> tuple[float, list[float]]:
    """推理和训练评估共用这一个函数。

    训练脚本打印的 AUC 必须和线上跑的是同一套算法，否则那份报告就是自我安慰。
    """
    z = weights[0]
    parts = []
    for i, v in enumerate(values):
        term = weights[i + 1] * ((v - mean[i]) / std[i])
        z += term
        parts.append(term)
    return z, parts


# --------------------------------------------------------------------- 扫描


def survey(pos: Position, color: int) -> tuple[dict[str, int], list[tuple[int, int]]]:
    """一次扫盘，同时得到棋形分布和活三以上的威胁点。

    分布按「这个空点若被 color 占据，四个方向里最强的一条是什么形」统计。
    不按已有棋子统计，是因为那样一串四会被记成四个冲四，数字会虚高。
    """
    hist = {name: 0 for name in SHAPE_ORDER}
    threats: list[tuple[int, int]] = []
    for idx in pos.candidate_list():
        ranks = pos.shapes_if_placed(idx, color)
        best = max(ranks) if ranks else shapes.NONE
        name = shapes.SHAPE_NAMES[best]
        if name in hist:
            hist[name] += 1
        if best >= shapes.OPEN_THREE:
            threats.append((idx, best))
    return hist, threats


def scan(pos: Position) -> dict[int, dict[str, int]]:
    """双方的威胁点统计。一次扫盘，两遍候选遍历。

    四个计数互不重叠，按「这个空点若被该色占据，四个方向里最强的一条」归档：
      fives       落下即成五的点。对手有两个以上，怎么堵都来不及。
      open_fours  落下即成活四的点。自己有一个，两步之内结束。
      fours       落下即成冲四的点。
      threes      落下即成活三的点。
    """
    out: dict[int, dict[str, int]] = {}
    for color in (BLACK, OPPONENT[BLACK]):
        counts = {"threes": 0, "fours": 0, "open_fours": 0, "fives": 0}
        for idx in pos.candidate_list():
            best = max(pos.shapes_if_placed(idx, color))
            if best >= shapes.FIVE:
                counts["fives"] += 1
            elif best >= shapes.OPEN_FOUR:
                counts["open_fours"] += 1
            elif best >= shapes.FOUR:
                counts["fours"] += 1
            elif best >= shapes.OPEN_THREE:
                counts["threes"] += 1
        out[color] = counts
    return out


def _features(pos: Position, side: int) -> dict[str, float]:
    """全部从 side 视角算。镜像样本会把这些特征整体取反，先后手天然对称。"""
    opp = OPPONENT[side]
    return {
        "log_ratio": math.log1p(pos.total[side] / MATERIAL_SCALE)
        - math.log1p(pos.total[opp] / MATERIAL_SCALE),
        "occupancy": pos.occupied / CELLS,
    }


# --------------------------------------------------------------------- 胜率


def _tactical(info: dict[int, dict[str, int]], turn: int) -> tuple[float, str] | None:
    """能一眼断定的局面不走模型。

    模型回答的是「这个局面对谁有利」，回答不了「三步之内结束」。
    这里抓三个确定性结论，条件不满足就返回 None 交给模型。
    """
    me = info[turn]
    them = info[OPPONENT[turn]]

    if me["fives"]:
        return 1.0, "有直接成五的点，这一手就结束"
    if them["fives"] >= 2:
        # 对手有两个成五点，堵掉一个还剩一个
        return 0.0, "对手有两个成五点，堵不过来"
    if me["open_fours"] and not them["fives"]:
        return 0.995, "落下成活四，两个成五点对手只能堵一个"
    return None


def _facts(pos: Position, side: int, info: dict[int, dict[str, int]]) -> list[dict]:
    """喂给天平面板的三个原始量。都是可以在棋盘上数出来的数。"""
    opp = OPPONENT[side]
    return [
        {
            "label": "棋形总量",
            "mine": pos.total[side],
            "theirs": pos.total[opp],
            "raw": True,
        },
        {
            "label": "冲四可用点",
            "mine": info[side]["fours"] + info[side]["open_fours"],
            "theirs": info[opp]["fours"] + info[opp]["open_fours"],
        },
        {
            "label": "活三可用点",
            "mine": info[side]["threes"],
            "theirs": info[opp]["threes"],
        },
    ]


def winrate(pos: Position, side: int, turn: int, *, info: dict | None = None) -> dict:
    """side 的胜率。turn 是当前轮到走的一方——模型条件在「轮到你走」上。

    想算不在走棋的那一方时用 1 减对方的值。这不是近似：轮到谁走是确定的，
    两个事件互补。反过来把「轮白走」的局面直接喂给「黑棋胜率」才是错的。
    """
    opp = OPPONENT[side]

    if pos.total[side] >= FIVE or pos.total[opp] >= FIVE:
        winner = side if pos.total[side] >= FIVE else opp
        return {
            "p": 1.0 if winner == side else 0.0,
            "side": side,
            "turn": turn,
            "source": "tactical",
            "reason": "盘面上已经有连五",
            "confidence": "certain",
            "confidence_note": "盘面上已经有连五",
            "facts": [],
            "model": get_model().describe(),
        }

    if info is None:
        info = scan(pos)

    values = _features(pos, turn)
    z, _ = get_model().logit([values[k] for k in FEATURES])
    p_stm = _sigmoid(z)
    source, reason = "model", "标定模型按棋形总量和盘面阶段给出"

    tactical = _tactical(info, turn)
    if tactical is not None:
        p_stm, reason = tactical
        source = "tactical"

    p = p_stm if side == turn else 1.0 - p_stm

    my_fours = info[side]["fours"]
    their_fours = info[opp]["fours"]

    if source == "tactical":
        confidence, note = "certain", reason
    elif their_fours:
        confidence, note = "high", "对手有冲四级别的杀招，局面接近定局"
    elif my_fours:
        confidence, note = "high", "己方有冲四级别的杀招，局面接近定局"
    elif abs(p - 0.5) > 0.2:
        confidence, note = "medium", "棋形差距明显，但还没到定局"
    else:
        confidence, note = "low", "双方棋形接近，任何一手都可能翻盘"

    return {
        "p": round(p, 4),
        "side": side,
        "turn": turn,
        "source": source,
        "reason": reason,
        "confidence": confidence,
        "confidence_note": note,
        "facts": _facts(pos, side, info),
        "model": get_model().describe(),
    }


# --------------------------------------------------------------------- 点位预测


def _why(pos: Position, idx: int, side: int) -> str:
    opp = OPPONENT[side]
    my_rank = max(pos.shapes_if_placed(idx, side))
    opp_rank = max(pos.shapes_if_placed(idx, opp))
    mine = shapes.SHAPE_NAMES[my_rank]
    theirs = shapes.SHAPE_NAMES[opp_rank]

    if my_rank >= shapes.FIVE:
        return "落下即连五"
    if opp_rank >= shapes.FIVE:
        return "不堵这里对手就连五"
    if my_rank >= shapes.OPEN_FOUR:
        return f"形成{mine}，两头都堵不住"
    if opp_rank >= shapes.OPEN_FOUR:
        return f"挡住对手的{theirs}"
    if my_rank >= shapes.FOUR:
        return "冲四，逼对手应一手"
    if opp_rank >= shapes.FOUR:
        return f"拆掉对手的{theirs}"
    if my_rank >= shapes.OPEN_THREE:
        return f"做{mine}抢攻"
    if opp_rank >= shapes.OPEN_THREE:
        return f"压住对手的{theirs}"
    if my_rank >= shapes.THREE:
        return f"做{mine}试探"
    return "贴近已有棋形，往中腹展开"


def predict(pos: Position, side: int, limit: int = 6) -> dict:
    """预测 side 下一手会落在哪，返回候选池内的倾向份额。

    排序分两部分：静态棋形分（主）+ 落点网络（先验）。上一轮实测过网络单独
    决定搜索宽度会掉棋力，所以这里它只是一个加项，作用是在静态分接近的几个点里
    分出先后。份额不是统计概率，准不准由 tools/fit_winrate.py 的命中率回答。
    """
    if pos.occupied == 0:
        return {
            "points": [{"x": 7, "y": 7, "share": 1.0, "why": "空盘，天元开局"}],
            "considered": 1,
            "net_used": False,
        }

    moves = Searcher(time_limit=0.05, max_depth=1).rank_moves(pos, side, 24)
    if not moves:
        return {"points": [], "considered": 0, "net_used": False}

    cfg = get_model().predict_cfg
    prior_weight = cfg["prior_weight"]
    temperature = cfg["temperature"] or 1.0

    # 标定出来 prior_weight=0 时就不该再跑网络：白算一遍 64×34 次乘加，
    # 还会让 net_used 报出一个根本没参与决策的 true。
    model = get_net()
    idxs = [m.idx for m in moves]
    use_net = prior_weight > 0 and model.ready
    feats = net_mod.encode(pos, idxs, side) if use_net else None

    raw = []
    for i, m in enumerate(moves):
        prior = model.score(feats[i]) if feats else 0.5
        raw.append((math.log1p(m.score) + prior_weight * prior) / temperature)

    peak = max(raw)
    exps = [math.exp(v - peak) for v in raw]
    total = sum(exps) or 1.0

    points = []
    for i, m in enumerate(moves[:limit]):
        x, y = coord(m.idx)
        points.append(
            {
                "x": x,
                "y": y,
                "share": round(exps[i] / total, 4),
                "why": _why(pos, m.idx, side),
            }
        )

    return {"points": points, "considered": len(moves), "net_used": bool(feats)}


# --------------------------------------------------------------------- 普查


def census(pos: Position) -> dict:
    """数据面板用的静态普查。每一项都可以在棋盘上数出来，没有估算。"""
    hist = {color: survey(pos, color) for color in (BLACK, OPPONENT[BLACK])}
    info = scan(pos)

    stones: dict[int, list[int]] = {BLACK: [], OPPONENT[BLACK]: []}
    for i, v in enumerate(pos.board):
        if v:
            stones[v].append(i)

    cent: dict[str, list[float] | None] = {}
    for name, color in (("black", BLACK), ("white", OPPONENT[BLACK])):
        cells = stones[color]
        if not cells:
            cent[name] = None
            continue
        xs = [c % SIZE for c in cells]
        ys = [c // SIZE for c in cells]
        cent[name] = [round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2)]

    return {
        "shapes": {"black": hist[BLACK][0], "white": hist[OPPONENT[BLACK]][0]},
        "threats": {
            "black": info[BLACK],
            "white": info[OPPONENT[BLACK]],
            "black_points": [{"x": i % SIZE, "y": i // SIZE} for i, _ in hist[BLACK][1][:4]],
            "white_points": [{"x": i % SIZE, "y": i // SIZE} for i, _ in hist[OPPONENT[BLACK]][1][:4]],
        },
        "stones": {"black": len(stones[BLACK]), "white": len(stones[OPPONENT[BLACK]])},
        "material": {"black": pos.total[BLACK], "white": pos.total[OPPONENT[BLACK]]},
        "centroid": cent,
        "occupancy": round(pos.occupied / CELLS, 4),
        "occupied": pos.occupied,
    }


# --------------------------------------------------------------------- 单步解说


def replay(moves: list[dict], upto: int, rule: str) -> Position:
    """重建第 upto 手之前的局面。对局本身不存历史局面，只能重放。"""
    pos = Position(rule=rule)
    for m in moves[:upto]:
        pos.place(m["y"] * SIZE + m["x"], m["color"])
    return pos


def _alternatives(pos: Position, color: int, chosen: int, limit: int = 3) -> list[dict]:
    """被放弃的那几手，以及放弃它们的可验证理由。"""
    opp = OPPONENT[color]
    ranked = Searcher(time_limit=0.05, max_depth=1).rank_moves(pos, color, limit + 4)
    out = []
    for m in ranked:
        if m.idx == chosen:
            continue
        out.append(
            {
                "x": coord(m.idx)[0],
                "y": coord(m.idx)[1],
                "why": _why(pos, m.idx, color),
                "attack": shapes.SHAPE_NAMES[max(pos.shapes_if_placed(m.idx, color))],
                "defence": shapes.SHAPE_NAMES[max(pos.shapes_if_placed(m.idx, opp))],
            }
        )
        if len(out) >= limit:
            break
    return out


def analyze(pos: Position, idx: int, color: int, meta: dict | None = None) -> dict:
    """给一手棋生成结构化解说。pos 必须是落子之前的局面。"""
    meta = meta or {}
    opp = OPPONENT[color]
    x, y = coord(idx)

    info = scan(pos)
    p_before = winrate(pos, color, color, info=info)["p"]

    ranks = pos.shapes_if_placed(idx, color)
    opp_ranks = pos.shapes_if_placed(idx, opp)
    my_rank = max(ranks)
    opp_rank = max(opp_ranks)
    my_label = shapes.SHAPE_NAMES[my_rank]
    opp_label = shapes.SHAPE_NAMES[opp_rank]

    # 落子前对手的杀招在哪。落完之后这个信息就没了，必须在之前读。
    _, their_threats = survey(pos, opp)
    lethal = [i for i, r in their_threats if r >= shapes.FOUR]
    blocked_here = opp_rank >= shapes.FOUR

    ban = pos.forbidden(idx, color)
    pos.place(idx, color)
    p_after = winrate(pos, color, opp)["p"]
    pos.undo()

    delta = p_after - p_before
    facts: list[dict] = []

    dirs = [f"{n}{shapes.SHAPE_NAMES[r]}" for n, r in zip(DIR_NAMES, ranks) if r >= shapes.OPEN_THREE]
    if dirs:
        facts.append({"kind": "shape", "text": f"四个方向里 {'、'.join(dirs)}"})
    elif my_rank > shapes.NONE:
        facts.append({"kind": "shape", "text": f"最强的一条线只有{my_label}，还构不成威胁"})
    else:
        facts.append({"kind": "shape", "text": "四个方向都没有形成棋形"})

    if opp_rank >= shapes.OPEN_THREE:
        facts.append(
            {"kind": "defence", "text": f"该点若被对手占据会形成{opp_label}，占位后威胁解除"}
        )

    unaddressed = [i for i in lethal if i != idx]
    if lethal and not blocked_here:
        points = "、".join(f"({i % SIZE},{i // SIZE})" for i in unaddressed[:3])
        facts.append({"kind": "miss", "text": f"对手在 {points} 有冲四级别的杀招，这一手没处理"})
    elif lethal and blocked_here:
        facts.append({"kind": "save", "text": "挡住了对手的冲四，否则下一手就结束了"})

    if abs(delta) >= 0.005:
        arrow = "抬高" if delta > 0 else "压低"
        facts.append(
            {
                "kind": "winrate",
                "text": f"胜率从 {p_before * 100:.1f}% 到 {p_after * 100:.1f}%，{arrow} {abs(delta) * 100:.1f} 个点",
            }
        )

    alternatives = _alternatives(pos, color, idx)
    beating = [
        a
        for a in alternatives
        if RANK_OF.get(a["attack"], 0) > my_rank
        and RANK_OF.get(a["attack"], 0) >= shapes.OPEN_THREE
    ]
    if beating:
        b = beating[0]
        facts.append(
            {"kind": "alternative", "text": f"备选 ({b['x']},{b['y']}) 能形成{b['attack']}，比这一手强"}
        )

    if ban:
        verdict = "ban"
    elif my_rank >= shapes.FIVE:
        verdict = "win"
    elif delta <= -0.06 or (lethal and not blocked_here):
        verdict = "bad"
    elif delta >= 0.04 or blocked_here or my_rank >= shapes.OPEN_THREE:
        verdict = "good"
    else:
        verdict = "neutral"

    headline = _headline(verdict, my_label, opp_label, delta)
    return {
        "index": meta.get("index"),
        "x": x,
        "y": y,
        "color": color,
        "source": meta.get("source", ""),
        "comment": meta.get("comment", ""),
        "think_ms": meta.get("think_ms", 0),
        "pending": bool(meta.get("pending")),
        "headline": headline,
        "verdict": verdict,
        "shape": {"mine": my_label, "theirs": opp_label, "directions": dirs},
        "winrate": {
            "before": round(p_before, 4),
            "after": round(p_after, 4),
            "delta": round(delta, 4),
        },
        "facts": facts,
        "alternatives": alternatives,
        "narrative": _narrative(headline, facts, color, meta),
    }


def _headline(verdict: str, mine: str, theirs: str, delta: float) -> str:
    if verdict == "ban":
        return "这一手是禁手，直接判负"
    if verdict == "win":
        return "连五，这一手结束对局"
    if verdict == "bad":
        if theirs == "无":
            return "这一手让出了主动"
        return f"没处理对手的{theirs}"
    if verdict == "good":
        if theirs in ("冲四", "活四", "连五"):
            return f"挡住了对手的{theirs}"
        if mine in ("活三", "冲四", "活四", "连五"):
            return f"做{mine}抢攻"
        return "局面因此改善"
    return "过渡手，没有立刻改变局势"


def _narrative(headline: str, facts: list[dict], color: int, meta: dict) -> str:
    side = "黑棋" if color == BLACK else "白棋"
    parts = [f"{side}这一手，{headline}。"]
    parts.extend(f["text"] + "。" for f in facts)
    comment = str(meta.get("comment") or "").strip()
    if comment:
        parts.append(f"它自己给的说明是「{comment}」。")
    return "".join(parts)


def explain(moves: list[dict], index: int, rule: str) -> dict:
    """解释历史中的第 index 手（0 起）。"""
    if not moves:
        raise IndexError("没有可解释的棋步")
    index = max(0, min(index, len(moves) - 1))
    move = moves[index]
    pos = replay(moves, index, rule)
    meta = {
        "index": index,
        "source": move.get("source", ""),
        "comment": move.get("comment", ""),
        "think_ms": move.get("think_ms", 0),
    }
    return analyze(pos, move["y"] * SIZE + move["x"], move["color"], meta)


def preview(moves: list[dict], idx: int, color: int, rule: str, meta: dict | None = None) -> dict:
    """解释还没落下的一手。单步模式下用来解说「它打算下这里」。

    meta 里带着这一手的来源和 Agent 自己写的说明，必须原样转给解说——
    「它打算下这里」的答案有一半在它自己的那句说明里。
    """
    pos = replay(moves, len(moves), rule)
    merged = {"index": None, "source": "pending", "pending": True, **(meta or {})}
    return analyze(pos, idx, color, merged)
