"""可学习贪心。

手写贪心的本质是一张棋形分值表加一个防守折扣：活三 1500、冲四 15000、
活四 300000，对手的同一套分乘 0.9。这些数字是拍的，没人验证过比例对不对。
这个模块把它们换成**可从棋谱训练的 12 个参数**：

    己方四个方向 × 8 档棋形 → 8 个计数
    对方同一套             → 8 个计数
    邻域密度、离中腹距离、手数 → 3 个特征
    参数：8 个棋形权重 + 3 个辅助权重 + 1 个防守系数 α

    落点分 = Σ w[r]·己方[r] + α·Σ w[r]·对方[r] + 辅助项

防守项刻意和进攻共用同一张 w、只差一个 α。早先给防守单开 8 个权重，
训练出来防守项全部萎缩——棋谱里进攻手远多于防守手，梯度自然把防守压下去，
模型就学会了「只顾自己连」。共用一个 α 之后，防守强弱是一个可读的数
（手写方案是 0.9），也不会因为样本偏斜而崩掉。

训练数据就是棋谱：逐手重放，每个局面里「棋谱实际走的那一手」是正样本，
同局面的其它候选点是负样本，对候选集做 softmax 交叉熵。这是行为克隆——
学的不是「这手棋好不好」，而是「下棋的人在这个局面会选哪」。梯度只往一个方向推：
让被选中的那一手在候选里排得更靠前。

参数少还有个实际好处：几百个样本、十几轮就能收敛，整个训练不到一秒，
用户改完棋谱能立刻看到权重怎么变——比一个只能等十分钟的黑箱有用。
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
from pathlib import Path

from . import rules, shapes
from .position import CELLS, OPPONENT, Position, WEIGHT
from ..paths import DATA_DIR

MODEL_FILE = DATA_DIR / "greedy_model.json"

RANKS = 8
FEATURES = 19  # 8 己方 + 8 对方 + 3 辅助
PARAMS = 12  # 8 棋形权重 + 3 辅助 + 1 防守系数

W_OWN = 0
W_EXTRA = 8  # 8 = 邻域, 9 = 中腹, 10 = 手数
W_ALPHA = 11

SCALE = 8.0  # 棋形分 10^7 → 权重 0~2，学习率才敢用 0.04
ALPHA_MIN, ALPHA_MAX = 0.2, 1.6

WIDTH = 20  # 每个局面取前多少个候选点参与训练
PER_GAME = 12  # 每局最多采样多少个局面
EPOCHS = 12
LR = 0.04
L2 = 0.01


def baseline_params() -> list[float]:
    """手写分值表取对数，作为训练起点。"""
    p = [0.0] * PARAMS
    for r in range(1, RANKS):
        p[W_OWN + r] = math.log1p(WEIGHT[r]) / SCALE
    p[W_EXTRA] = 0.12
    p[W_EXTRA + 1] = 0.06
    p[W_EXTRA + 2] = 0.0
    p[W_ALPHA] = math.log((0.9 - ALPHA_MIN) / (ALPHA_MAX - 0.9))
    return p


def _sigmoid(x: float) -> float:
    if x < -30:
        return 0.0
    if x > 30:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _alpha(p: list[float]) -> float:
    return ALPHA_MIN + (ALPHA_MAX - ALPHA_MIN) * _sigmoid(p[W_ALPHA])


def features(pos: Position, idx: int, color: int) -> list[float]:
    """一个「假设在这里落子」的特征向量。"""
    own = pos.shapes_if_placed(idx, color)
    opp = pos.shapes_if_placed(idx, OPPONENT[color])
    f = [0.0] * FEATURES
    for r in own:
        if r:
            f[r] += 1.0
    for r in opp:
        if r:
            f[W_EXTRA + r] += 1.0
    f[16] = min(pos.neighbor[idx], 16) / 16.0
    x, y = idx % 15, idx // 15
    f[17] = max(0, 28 - (abs(x - 7) + abs(y - 7))) / 28.0
    f[18] = pos.occupied / CELLS
    return f


def score(p: list[float], f: list[float]) -> float:
    alpha = _alpha(p)
    s = 0.0
    for r in range(1, RANKS):
        s += p[W_OWN + r] * (f[r] + alpha * f[W_EXTRA + r])
    s += p[W_EXTRA] * f[16] + p[W_EXTRA + 1] * f[17] + p[W_EXTRA + 2] * f[18]
    return s


def legal(pos: Position, idx: int, color: int) -> bool:
    if pos.board[idx] != 0:
        return False
    if pos.rule == rules.RENJU and color == 1 and pos.forbidden(idx, color):
        return False
    return True


class Model:
    def __init__(self, path: Path = MODEL_FILE) -> None:
        self.path = path
        self.w = baseline_params()
        self.meta: dict = {}
        self.trained = False
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        w = data.get("w")
        if not isinstance(w, list) or len(w) != PARAMS:
            return
        self.w = [float(v) for v in w]
        self.meta = data.get("meta", {}) or {}
        self.trained = True

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "w": [round(v, 6) for v in self.w], "meta": self.meta}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def reset(self) -> None:
        self.w = baseline_params()
        self.meta = {}
        self.trained = False
        try:
            self.path.unlink()
        except OSError:
            pass

    # ---------------------------------------------------------------- 打分

    @property
    def alpha(self) -> float:
        return round(_alpha(self.w), 3)

    def point_score(self, pos: Position, idx: int, color: int) -> float:
        return score(self.w, features(pos, idx, color))

    def ranked(self, pos: Position, color: int) -> list[tuple[float, int]]:
        spots = pos.candidate_list()
        if not spots:
            spots = [i for i in range(CELLS) if pos.board[i] == 0]
        out = [(self.point_score(pos, i, color), i) for i in spots]
        out.sort(reverse=True)
        return out

    def choose(self, pos: Position, color: int) -> tuple[int, str]:
        ranked = self.ranked(pos, color)
        for value, idx in ranked:
            if legal(pos, idx, color):
                return idx, f"评估 {value:.2f}｜{_note(pos, idx, color)}"
        for i in range(CELLS):
            if pos.board[i] == 0:
                return i, "候选全是禁手点，退回空点"
        return 112, "无合法点"

    # ---------------------------------------------------------------- 展示

    def table(self) -> list[dict]:
        """手写分值与学到的分值并排。前端画那张对照表用。"""
        base = baseline_params()
        rows = []
        for r in range(RANKS):
            rows.append(
                {
                    "rank": r,
                    "label": shapes.SHAPE_NAMES[r],
                    "hand": WEIGHT[r],
                    "learned": round(_denorm(self.w[W_OWN + r]), 1),
                    "weight": round(self.w[W_OWN + r], 4),
                    "delta": round(self.w[W_OWN + r] - base[W_OWN + r], 4),
                }
            )
        return rows

    def describe(self) -> dict:
        base = baseline_params()
        drift = math.sqrt(sum((a - b) ** 2 for a, b in zip(self.w, base)))
        table = self.table()
        strongest = max(table[1:], key=lambda row: row["delta"])
        return {
            "trained": self.trained,
            "parameters": PARAMS,
            "features": FEATURES,
            "alpha": self.alpha,
            "alpha_hand": round(_alpha(base), 3),
            "drift": round(drift, 4),
            "weights": [round(v, 4) for v in self.w],
            "table": table,
            "shift": {"label": strongest["label"], "delta": strongest["delta"]},
            **self.meta,
        }


def _denorm(w: float) -> float:
    """把归一化权重还原成「棋形分值」，只为对照展示。"""
    return max(0.0, math.expm1(max(0.0, w) * SCALE))


def _note(pos: Position, idx: int, color: int) -> str:
    ranks = pos.shapes_if_placed(idx, color)
    best = max(ranks)
    if best == shapes.NONE:
        return "无成形"
    return f"形成{shapes.SHAPE_NAMES[best]}"


_MODEL: Model | None = None
_MODEL_LOCK = threading.Lock()


def get_model() -> Model:
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = Model()
        return _MODEL


# --------------------------------------------------------------------- 训练


def _project(p: list[float]) -> None:
    """把领域约束投影回可行域。

    三条都是下棋的常识，不是调参技巧：空棋形不值分；活四必须比冲四值钱、
    冲四比活三值钱；防守系数不能为负、也不能比进攻还重。样本只有几百条时，
    光靠梯度会把「眠二」压成负数去拟合噪声，投影一下既稳住泛化，
    也让那张对照表能读。
    """
    p[W_OWN] = 0.0
    for r in range(2, RANKS):
        if p[W_OWN + r] < p[W_OWN + r - 1]:
            p[W_OWN + r] = p[W_OWN + r - 1]
    p[W_EXTRA] = max(0.0, p[W_EXTRA])
    p[W_EXTRA + 1] = max(0.0, p[W_EXTRA + 1])
    # sigmoid 里留一点余量，别让 α 卡在边界上导致梯度恒为零
    lo = math.log(1e-4)
    hi = math.log(1 / 1e-4 - 1)
    p[W_ALPHA] = min(hi, max(lo, p[W_ALPHA]))


def _sample(pos: Position, color: int, target: int, width: int = WIDTH) -> dict | None:
    spots = [i for i in pos.candidate_list() if pos.board[i] == 0]
    if target not in spots:
        spots.append(target)
    if len(spots) < 3:
        return None
    feats = [features(pos, i, color) for i in spots]
    base = baseline_params()
    keep = sorted(range(len(spots)), key=lambda k: score(base, feats[k]), reverse=True)[:width]
    want = spots.index(target)
    if want not in keep:
        # 棋谱走的那一手必须在候选里，否则这一条样本学不到任何东西
        keep[-1] = want
    return {"feats": [feats[k] for k in keep], "target": keep.index(want)}


def build_samples(records: list[dict], *, rule: str = rules.RENJU, per_game: int = PER_GAME) -> list[dict]:
    """把棋谱重放成 (局面, 实际落点) 样本。"""
    samples: list[dict] = []
    for rec in records:
        moves = rec.get("moves") or []
        if len(moves) < 6:
            continue
        step = max(1, len(moves) // max(1, per_game))
        pos = Position(rule)
        for i, mv in enumerate(moves):
            x, y = int(mv[0]), int(mv[1])
            if not (0 <= x < 15 and 0 <= y < 15):
                break
            idx = y * 15 + x
            if pos.board[idx] != 0:
                break
            color = 1 if i % 2 == 0 else 2
            if i >= 2 and i % step == 0:
                item = _sample(pos, color, idx)
                if item:
                    item["ply"] = i
                    samples.append(item)
            pos.place(idx, color)
            if rules.judge(pos.board, idx, color, rule).finished:
                break
    return samples


def _softmax(logits: list[float]) -> list[float]:
    top = max(logits)
    exps = [math.exp(v - top) for v in logits]
    total = sum(exps)
    return [v / total for v in exps]


def _fit(p: list[float], samples: list[dict], *, epochs: int, lr: float, on_event=None) -> dict:
    """Adam + 交叉熵。样本量在几千级别，纯 Python 跑十几轮不到一秒。"""
    base = baseline_params()
    m = [0.0] * PARAMS
    v = [0.0] * PARAMS
    b1, b2, eps = 0.9, 0.999, 1e-8
    step = 0
    curve: list[dict] = []

    for epoch in range(1, epochs + 1):
        order = list(range(len(samples)))
        random.shuffle(order)
        total_loss = 0.0
        hits = 0
        grad = [0.0] * PARAMS
        alpha = _alpha(p)

        for k in order:
            item = samples[k]
            feats, target = item["feats"], item["target"]
            logits = [score(p, f) for f in feats]
            probs = _softmax(logits)
            total_loss -= math.log(max(probs[target], 1e-12))
            hits += int(max(range(len(probs)), key=probs.__getitem__) == target)

            for j, f in enumerate(feats):
                coef = probs[j] - (1.0 if j == target else 0.0)
                if coef == 0.0:
                    continue
                opp = 0.0
                for r in range(1, RANKS):
                    grad[W_OWN + r] += coef * (f[r] + alpha * f[W_EXTRA + r])
                    opp += p[W_OWN + r] * f[W_EXTRA + r]
                grad[W_EXTRA] += coef * f[16]
                grad[W_EXTRA + 1] += coef * f[17]
                grad[W_EXTRA + 2] += coef * f[18]
                # α 走 sigmoid 参数化，链式法则要乘上导数
                grad[W_ALPHA] += coef * opp * alpha * (1.0 - alpha)

        n = max(1, len(order))
        step += 1
        for d in range(PARAMS):
            g = grad[d] / n
            if d != W_ALPHA:
                g += L2 * (p[d] - base[d])
            m[d] = b1 * m[d] + (1 - b1) * g
            v[d] = b2 * v[d] + (1 - b2) * g * g
            mh = m[d] / (1 - b1**step)
            vh = v[d] / (1 - b2**step)
            p[d] -= lr * mh / (math.sqrt(vh) + eps)
        _project(p)
        alpha = _alpha(p)
        point = {"epoch": epoch, "loss": round(total_loss / n, 4), "acc": round(hits / n, 4)}
        curve.append(point)
        if on_event:
            on_event({"type": "epoch", **point})

    return {"curve": curve}


def evaluate(p: list[float], samples: list[dict]) -> dict:
    """在验证局面上的三项指标。

    主指标是 top-1：模型的第一选择正好是棋谱走的那一手。这是训练目标本身，
    所以它最能说明「学到没有」。top-3 和平均排名是它的两条旁证——分数被拉近
    但没排到第一时，top-1 不动而排名会动。
    """
    if not samples:
        return {"top1": 0.0, "top3": 0.0, "rank": 0.0, "n": 0}
    top1 = top3 = 0
    ranks = 0.0
    for item in samples:
        logits = [score(p, f) for f in item["feats"]]
        order = sorted(range(len(logits)), key=logits.__getitem__, reverse=True)
        pos = order.index(item["target"]) + 1
        ranks += pos
        top1 += int(pos == 1)
        top3 += int(pos <= 3)
    n = len(samples)
    return {
        "top1": round(top1 / n, 4),
        "top3": round(top3 / n, 4),
        "rank": round(ranks / n, 2),
        "n": n,
    }


def train(records: list[dict], *, epochs: int = EPOCHS, lr: float = LR, on_event=None) -> dict:
    """从棋谱训练一个模型，返回指标；模型由这里负责落盘。"""
    started = time.time()

    def emit(ev: dict) -> None:
        if on_event:
            on_event(ev)

    emit({"type": "stage", "stage": "samples", "label": "重放棋谱，构建样本"})
    samples = build_samples(records)
    if len(samples) < 8:
        raise ValueError("有效样本太少：至少需要 8 个可用局面，多喂几盘棋谱或换长一点的棋谱")

    # 留出最后 1/5 的样本做验证。按顺序切而不是随机切：同一局相邻局面的特征高度相似，
    # 随机切会让验证集里混进「训练时见过几步之后」的局面，命中率虚高。
    cut = max(1, len(samples) // 5)
    train_set, eval_set = samples[:-cut], samples[-cut:]

    w = baseline_params()
    _project(w)
    before = evaluate(w, eval_set)
    emit(
        {
            "type": "stage",
            "stage": "fit",
            "label": f"{len(samples)} 个局面，开始训练",
            "samples": len(samples),
        }
    )
    result = _fit(w, train_set, epochs=epochs, lr=lr, on_event=emit)

    emit({"type": "stage", "stage": "eval", "label": "验证集评估"})
    after = evaluate(w, eval_set)

    model = get_model()
    model.w = w
    model.meta = {
        "trained_at": time.time(),
        "epochs": epochs,
        "samples": len(samples),
        "train_samples": len(train_set),
        "eval_samples": len(eval_set),
        "games": len([r for r in records if len(r.get("moves") or []) >= 6]),
        "train": evaluate(w, train_set),
        "eval": after,
        "eval_before": before,
        "loss": result["curve"][-1]["loss"] if result["curve"] else None,
        "curve": result["curve"],
        "alpha": model.alpha,
    }

    model.meta["seconds"] = round(time.time() - started, 2)
    model.trained = True
    model.save()
    return {"curve": result["curve"], **model.meta}
