"""对弈 Agent。

核心设计：搜索负责算，Agent 负责选。

LLM 不直接看棋盘吐坐标——那会输出非法点、漏看活四，棋力低于随机。
这里的做法是让引擎先把候选点算出来，附带每个点的进攻/防守棋形标签，
再让模型在 4~6 个合法候选里做取舍并给出理由。

于是模型做的是它擅长的事（在有限选项里按风格偏好取舍、用自然语言解释），
棋力下限由引擎兜住。没配 API Key 时，同一套候选池交给内置的人格权重打散，
流程完全一致，只是少了那句自然语言解释。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import rules, shapes
from .llm import LLMConfig, LLMError, chat, extract_json
from .position import BLACK, SIZE, WHITE, Position, coord
from .search import Searcher, describe_move

PERSONAS = {
    "aggressive": {
        "label": "锐攻",
        "attack": 1.0,
        "defence": 0.6,
        "brief": "主动挑起战斗，优先做活三、冲四，以局部交换换取主动权",
    },
    "balanced": {
        "label": "均衡",
        "attack": 1.0,
        "defence": 0.95,
        "brief": "攻守等价，先看对手的成五点，再找自己的进攻机会",
    },
    "defensive": {
        "label": "稳固",
        "attack": 0.8,
        "defence": 1.3,
        "brief": "先拆对手的棋形，等对方露出破绽再反击",
    },
}

DIFFICULTY = {
    1: {"label": "入门", "time": 0.35, "depth": 2, "topk": 4, "noise": 0.55},
    2: {"label": "进阶", "time": 0.9, "depth": 3, "topk": 4, "noise": 0.25},
    3: {"label": "困难", "time": 2.0, "depth": 4, "topk": 3, "noise": 0.08},
    4: {"label": "专家", "time": 3.5, "depth": 5, "topk": 1, "noise": 0.0},
}


@dataclass
class Candidate:
    idx: int
    x: int
    y: int
    attack: int
    defence: int
    attack_label: str
    defence_label: str
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "x": self.x,
            "y": self.y,
            "attack": self.attack_label,
            "defence": self.defence_label,
        }


@dataclass
class Decision:
    move: int
    source: str
    comment: str
    candidates: list[Candidate] = field(default_factory=list)
    think_ms: int = 0
    depth: int = 0
    nodes: int = 0
    llm_used: bool = False
    raw: str = ""


def _label(pos: Position, idx: int, color: int) -> str:
    ranks = pos.shapes_if_placed(idx, color)
    best = max(ranks) if ranks else shapes.NONE
    if best == shapes.NONE:
        return "无"
    return shapes.SHAPE_NAMES[best]


def build_candidates(pos: Position, color: int, searcher: Searcher, topk: int) -> list[Candidate]:
    opp = WHITE if color == BLACK else BLACK
    out = []
    for m in searcher.rank_moves(pos, color, max(topk, 1) + 3):
        x, y = coord(m.idx)
        out.append(
            Candidate(
                idx=m.idx,
                x=x,
                y=y,
                attack=m.attack,
                defence=m.defence,
                attack_label=_label(pos, m.idx, color),
                defence_label=_label(pos, m.idx, opp),
            )
        )
    return out[: max(topk, 1) + 2]


def _persona_pick(cands: list[Candidate], persona: dict, noise: float, rng: random.Random) -> Candidate:
    w_a, w_d = persona["attack"], persona["defence"]
    for c in cands:
        c.score = c.attack * w_a + c.defence * w_d

    ranked = sorted(cands, key=lambda c: c.score, reverse=True)
    if noise <= 0 or len(ranked) == 1:
        return ranked[0]

    # 低难度不是靠削弱搜索，而是靠在这几个像样的候选里随机挑。
    # 直接让弱引擎下臭棋，用户体验是「AI 很蠢」；这样是「AI 偶尔走偏」。
    best = ranked[0].score
    pool = [c for c in ranked if c.score >= best * (1 - noise)] or ranked[:1]
    return rng.choice(pool)


def _local_comment(pos: Position, cand: Candidate, color: int) -> str:
    """不调模型时的一句话解说，纯规则生成。"""
    mine = cand.attack_label
    theirs = cand.defence_label
    parts = []
    if mine in ("连五", "活四", "冲四"):
        parts.append(f"{mine}，直接压制")
    elif mine == "活三":
        parts.append("做活三抢攻")
    elif mine == "眠三":
        parts.append("做眠三试探")
    if theirs in ("连五", "活四", "冲四", "活三"):
        parts.append(f"同时化解对手的{theirs}")
    if not parts:
        return "先占中腹要点，保持棋形展开"
    return "，".join(parts)


def _board_text(pos: Position) -> str:
    glyph = {0: ".", BLACK: "B", WHITE: "W"}
    lines = ["    " + " ".join(f"{x:>2}" for x in range(SIZE))]
    for y in range(SIZE):
        row = " ".join(f"{glyph[pos.board[y * SIZE + x]]:>2}" for x in range(SIZE))
        lines.append(f"{y:>3} {row}")
    return "\n".join(lines)


def _llm_pick(
    pos: Position,
    color: int,
    cands: list[Candidate],
    persona_key: str,
    persona: dict,
) -> tuple[Candidate | None, str, str]:
    me = "黑棋" if color == BLACK else "白棋"
    rule_note = (
        "本局按国际连珠规则：黑棋的长连、四四、三三都是禁手，白棋没有禁手且长连也算胜。"
        "候选点里已经剔除了黑棋的禁手点。"
        if pos.rule == rules.RENJU
        else "本局为自由规则，没有禁手，五子及以上连珠即胜。"
    )
    lines = []
    for i, c in enumerate(cands):
        lines.append(
            f"{chr(65 + i)}. ({c.x},{c.y}) 己方落子会是「{c.attack_label}」，"
            f"若对手占此处则是「{c.defence_label}」"
        )
    prompt = f"""你在下 15x15 五子棋，执{me}。你的风格是「{persona['label']}」：{persona['brief']}。
{rule_note}

当前棋盘（B=黑棋 W=白棋 .=空点）：
{_board_text(pos)}

搜索引擎已经算出下面这些合法候选点，你只能从里面选一个：
{chr(10).join(lines)}

要求：
1. 先判断对手是否有活三及以上的威胁，有就必须优先处理。
2. 在有威胁的前提下，再结合你的风格选点。
3. 只输出 JSON，不要任何额外文字：{{"choice":"A","comment":"20 字以内的中文说明"}}
"""
    try:
        raw = chat(
            [
                {"role": "system", "content": "你是五子棋棋手，回复必须是严格 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            max_tokens=200,
        )
    except LLMError as exc:
        return None, str(exc), ""

    data = extract_json(raw)
    if not data:
        return None, "模型返回的不是 JSON", raw

    choice = str(data.get("choice", "")).strip().upper()[:1]
    if not choice or not ("A" <= choice <= "Z"):
        return None, "模型没有给出有效选项", raw
    index = ord(choice) - 65
    if not (0 <= index < len(cands)):
        return None, "模型选了范围外的选项", raw

    comment = str(data.get("comment", "")).strip()[:60]
    return cands[index], comment, raw


def decide(
    pos: Position,
    color: int,
    *,
    persona_key: str = "balanced",
    difficulty: int = 3,
    use_llm: bool = True,
    seed: int | None = None,
) -> Decision:
    import time

    started = time.perf_counter()
    persona_key = persona_key if persona_key in PERSONAS else "balanced"
    persona = PERSONAS[persona_key]
    level = DIFFICULTY.get(difficulty, DIFFICULTY[3])

    searcher = Searcher(time_limit=level["time"], max_depth=level["depth"])
    result = searcher.search(pos, color)
    cands = build_candidates(pos, color, searcher, level["topk"])

    if not cands:
        # 空盘时「已有棋子周围两格」这个候选集是空的，搜索会直接给天元。
        # 这时候说「无可下的点」是自相矛盾的，换一句和实际动作一致的话。
        if pos.occupied == 0:
            return Decision(result.move, "opening", "空盘开局，先占天元", [], 0, 0, 0)
        return Decision(result.move, "fallback", "没有合适的候选点，按搜索首选落子", [], 0, 0, 0)

    engine_move = result.move
    # 让引擎的首选一定出现在候选池里，否则 Agent 可能选不到正解
    if all(c.idx != engine_move for c in cands):
        x, y = coord(engine_move)
        cands.insert(
            0,
            Candidate(
                idx=engine_move,
                x=x,
                y=y,
                attack=pos.point_value(engine_move, color),
                defence=pos.point_value(engine_move, WHITE if color == BLACK else BLACK),
                attack_label=_label(pos, engine_move, color),
                defence_label=_label(pos, engine_move, WHITE if color == BLACK else BLACK),
            ),
        )

    rng = random.Random(seed)
    chosen: Candidate | None = None
    comment = ""
    source = "persona"
    llm_used = False
    raw = ""

    if use_llm and LLMConfig().enabled and level["noise"] < 0.5:
        chosen, llm_comment, raw = _llm_pick(pos, color, cands, persona_key, persona)
        if chosen is not None:
            llm_used = True
            source = "llm"
            comment = llm_comment

    if chosen is None:
        chosen = _persona_pick(cands, persona, level["noise"], rng)
        if source == "llm":
            source = "persona-fallback"

    if not comment:
        comment = _local_comment(pos, chosen, color)

    return Decision(
        move=chosen.idx,
        source=source,
        comment=comment,
        candidates=cands,
        think_ms=int((time.perf_counter() - started) * 1000),
        depth=result.depth,
        nodes=result.nodes,
        llm_used=llm_used,
        raw=raw,
    )


def hint(pos: Position, color: int, difficulty: int = 3) -> dict:
    """给人类玩家的提示，走完整搜索，返回推荐点和理由。"""
    level = DIFFICULTY.get(difficulty, DIFFICULTY[3])
    searcher = Searcher(time_limit=min(level["time"], 2.5), max_depth=level["depth"])
    result = searcher.search(pos, color)
    x, y = coord(result.move)
    return {
        "x": x,
        "y": y,
        "reason": describe_move(pos, result.move, color),
        "depth": result.depth,
        "think_ms": int(result.elapsed * 1000),
    }


def review(moves: list[dict], winner: int | None, difficulty: int) -> str:
    """赛后复盘。有 Key 用模型写，没有就用评估曲线里的事实说话。"""
    if not moves:
        return "对局还没有开始。"
    total = len(moves)
    black_moves = sum(1 for m in moves if m["color"] == BLACK)
    white_moves = total - black_moves

    best = max(moves, key=lambda m: abs(m.get("eval", 0)))
    swing = abs(best.get("eval", 0))

    verdict = {BLACK: "黑棋获胜", WHITE: "白棋获胜"}.get(winner, "和棋")

    if LLMConfig().enabled:
        history = "；".join(
            f"{i + 1}.{'黑' if m['color'] == BLACK else '白'}({m['x']},{m['y']})" for i, m in enumerate(moves)
        )
        prompt = f"""下面是一盘 15x15 五子棋的完整落子记录，结果是{verdict}。

{history}

请用 150 字以内复盘这盘棋：开局取向、中盘的转折点、胜负手。用中文，像棋友聊天，不要罗列坐标。"""
        try:
            return chat(
                [
                    {"role": "system", "content": "你是五子棋教练，讲话直接、具体。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.6,
                max_tokens=400,
            ).strip()
        except LLMError:
            pass

    return (
        f"共 {total} 手，黑棋 {black_moves} 手、白棋 {white_moves} 手，{verdict}。"
        f"局面波动最大的一手是第 {moves.index(best) + 1} 手"
        f"（{'黑' if best['color'] == BLACK else '白'}方 {best['x']},{best['y']}），"
        f"评估从均势摆动了约 {swing}。"
        "整体看，胜负主要取决于谁先在边路形成双活三。"
    )
