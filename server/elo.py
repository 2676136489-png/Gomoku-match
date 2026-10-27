"""等级分。

用的就是 Elo 本体，没有魔改：期望胜率 1/(1+10^((Rb-Ra)/400))，新分 = 旧分 +
K×(实际 − 期望)。所有「我们改进了 Elo」的说法最后都会退化成调参，这里不装。

只有两处工程取舍值得写：

1. K 因子按已下盘数衰减。新号前 15 盘 K=40，收敛快，避免一个人开局三连胜就
   飘到 1500；60 盘之后 K=16，排名稳定下来。这不是原创，FIDE 和国际棋联都用
   同一套思路——新人需要快速定位，老号需要抗抖动。
2. 分数下限 100，上限不设。下限防的是「连输到负数」这种荒谬展示；上限不设是
   因为前 1% 的区分度只能靠开放区间拉开。

天梯只统计 rated 对局：人机对局不计分。原因很直接——引擎难度是用户自己选的，
可以用「入门」难度刷 50 连胜，那样的分不代表任何东西。
"""

from __future__ import annotations

import math

START = 1200.0
FLOOR = 100.0

# (已下盘数上限, K)。顺序敏感，从上往下第一个命中的生效。
K_TABLE: tuple[tuple[int, float], ...] = ((15, 40.0), (60, 24.0), (10**9, 16.0))

# 段位映射。阈值从高到低，第一个 rating >= threshold 的生效。
TIERS: tuple[tuple[float, str], ...] = (
    (2400, "名人"),
    (2250, "七段"),
    (2100, "六段"),
    (1950, "五段"),
    (1800, "四段"),
    (1650, "三段"),
    (1500, "二段"),
    (1350, "初段"),
    (1200, "一级"),
    (1050, "二级"),
    (900, "三级"),
    (750, "四级"),
    (0, "五级"),
)


def k_factor(rated_games: int) -> float:
    for limit, k in K_TABLE:
        if rated_games < limit:
            return k
    return K_TABLE[-1][1]


def expected(rating: float, opponent: float) -> float:
    """A 方对 B 方的期望得分，取值 (0, 1)。"""
    return 1.0 / (1.0 + math.pow(10.0, (opponent - rating) / 400.0))


def settle(
    rating_a: float,
    rating_b: float,
    score_a: float,
    *,
    games_a: int = 0,
    games_b: int = 0,
) -> tuple[float, float, float]:
    """结算一盘。score_a 取 1 / 0.5 / 0，返回 (A 新分, B 新分, A 的变化量)。

    两边用各自的 K，但变化量互为相反数——不然总分不守恒，天梯会整体漂移。
    规则：用 A 的 K 算出 delta，B 反向拿同样的 delta。这比「各算各的」更公平，
    因为对局只有一个结果，涨跌必须对冲。
    """
    k = k_factor(games_a)
    delta = k * (score_a - expected(rating_a, rating_b))
    # 四舍五入到 0.1，避免浮点尾数在天梯榜上排出无意义的次序
    new_a = max(FLOOR, round(rating_a + delta, 1))
    new_b = max(FLOOR, round(rating_b - delta, 1))
    return new_a, new_b, round(new_a - rating_a, 1)


def tier(rating: float) -> dict:
    """段位、下一段门槛、进度。前端画一条进度条要的三个数。"""
    label = TIERS[-1][1]
    nxt: float | None = None
    for threshold, name in TIERS:
        if rating >= threshold:
            label = name
            break
    for threshold, name in reversed(TIERS):
        if threshold > rating:
            nxt = threshold
            break
    floor = 0.0
    for threshold, _ in TIERS:
        if rating >= threshold:
            floor = threshold
            break

    span = (nxt - floor) if nxt else 0.0
    return {
        "label": label,
        "rating": round(rating, 1),
        "next": nxt,
        "progress": round(min(1.0, max(0.0, (rating - floor) / span)), 3) if span else 1.0,
    }


def describe() -> dict:
    return {
        "start": START,
        "floor": FLOOR,
        "k": [{"until": limit, "k": k} for limit, k in K_TABLE],
        "tiers": [{"min": threshold, "label": name} for threshold, name in TIERS],
    }
