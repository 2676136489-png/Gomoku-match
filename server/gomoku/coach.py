"""学习模式的教练。

只在人类落子前调用一次，回答一个问题：**这一手有没有把对手的威胁当回事。**

不是给「好坏」评分。评分数值在没有职业棋力的前提下都是编的，
这里只报可验证的事实：你这一手形成了什么、对手原本有什么、你挡住没有。
"""

from __future__ import annotations

from . import rules, shapes
from .position import OPPONENT, WEIGHT, Position

THREAT_LEVEL = WEIGHT[shapes.OPEN_THREE]


def _label(pos: Position, idx: int, color: int) -> str:
    ranks = pos.shapes_if_placed(idx, color)
    best = max(ranks) if ranks else shapes.NONE
    return shapes.SHAPE_NAMES[best]


def analyze(pos: Position, idx: int, color: int) -> dict:
    opp = OPPONENT[color]

    attack = pos.point_value(idx, color)
    defence = pos.point_value(idx, opp)
    attack_label = _label(pos, idx, color)
    defence_label = _label(pos, idx, opp)

    # 对手当前能做出活三及以上的点。用棋形分判断，跟搜索里同一套标准。
    threats = [i for i in pos.candidate_list() if pos.point_value(i, opp) >= THREAT_LEVEL]
    threat_here = defence >= THREAT_LEVEL
    threat_resolved = attack >= WEIGHT[shapes.FOUR] or defence >= WEIGHT[shapes.FOUR]

    if attack_label in ("连五", "活四"):
        return {
            "grade": "win",
            "title": f"这一手是{attack_label}",
            "detail": "对手无法同时防守。",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": len(threats)},
        }

    if threat_here:
        extra = "" if not threats or len(threats) == 1 else f"另有 {len(threats) - 1} 个威胁点。"
        return {
            "grade": "good",
            "title": f"挡住了对手的{defence_label}",
            "detail": f"占{defence_label}，威胁解除。{extra}",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": len(threats)},
        }

    if threats:
        points = "、".join(f"({i % 15},{i // 15})" for i in threats[:3])
        return {
            "grade": "warn",
            "title": "对手活三未处理",
            "detail": f"对手在 {points} 可成活三以上。",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": len(threats)},
        }

    if attack_label in ("活三", "冲四"):
        return {
            "grade": "good",
            "title": f"主动做{attack_label}",
            "detail": f"对手无活三威胁，抢先形成{attack_label}。",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": 0},
        }

    if threat_resolved:
        return {
            "grade": "good",
            "title": "形成强威胁",
            "detail": f"形成{attack_label}，对手必须应。",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": len(threats)},
        }

    if attack_label == "无":
        return {
            "grade": "neutral",
            "title": "布局手",
            "detail": "双方均无活三以上威胁。",
            "facts": {"attack": attack_label, "defence": defence_label, "threats": len(threats)},
        }

    return {
        "grade": "neutral",
        "title": f"这一手形成{attack_label}",
        "detail": "双方均无活三以上威胁。",
        "facts": {"attack": attack_label, "defence": defence_label, "threats": 0},
    }


def tactic_notes(pos: Position, color: int) -> dict:
    """给学习面板用的局面摘要：双方的强威胁点各在哪里。"""
    opp = OPPONENT[color]
    mine = [(i, pos.point_value(i, color)) for i in pos.candidate_list()]
    theirs = [(i, pos.point_value(i, opp)) for i in pos.candidate_list()]
    mine.sort(key=lambda t: t[1], reverse=True)
    theirs.sort(key=lambda t: t[1], reverse=True)

    return {
        "my_threats": [
            {"x": idx % 15, "y": idx // 15, "shape": _label(pos, idx, color)}
            for idx, value in mine[:3]
            if value >= THREAT_LEVEL
        ],
        "opp_threats": [
            {"x": idx % 15, "y": idx // 15, "shape": _label(pos, idx, opp)}
            for idx, value in theirs[:3]
            if value >= THREAT_LEVEL
        ],
        "rule": pos.rule,
        "rule_label": "国际连珠" if pos.rule == rules.RENJU else "自由规则",
    }
