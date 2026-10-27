"""人机对局路由。

在线对战走 WebSocket，不在这里。两套接口共用一个 `Game` 对象，
区别只在「谁有权落子」和「结果怎么广播」。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import analysis, settlement
from ..gomoku import agent as agent_mod
from ..gomoku import coach as coach_mod
from ..gomoku import rules
from ..gomoku.position import SIZE, coord
from ..runtime import store
from ..store import Game, opponent_of
from .deps import require_game, require_user

router = APIRouter(prefix="/api", tags=["game"])


class NewGameRequest(BaseModel):
    mode: str = Field(default="agent", pattern="^(agent|engine|pvp|learn)$")
    difficulty: int = Field(default=3, ge=1, le=4)
    persona: str = Field(default="balanced", pattern="^(aggressive|balanced|defensive)$")
    human_color: int = Field(default=1, ge=1, le=2)
    use_llm: bool = True
    rule: str = Field(default=rules.RENJU, pattern="^(renju|freestyle)$")
    strict_ban: bool = False
    step: bool = False


class MoveRequest(BaseModel):
    x: int
    y: int


class ExplainRequest(BaseModel):
    index: int | None = None


def _agent_side(game: Game) -> int | None:
    if game.status != "playing" or game.mode == "pvp":
        return None
    side = opponent_of(game.human_color)
    return side if game.turn == side else None


def _pending_view(pending: dict) -> dict:
    return {k: v for k, v in pending.items() if k != "idx"}


def play_agent(game: Game, *, force: bool = False) -> dict | None:
    """让 Agent 走一手。

    step 模式下不是不落子，而是「算完先停住」：决策已经出来了，放在 pending 里
    等用户点继续。这样「停下来解释」解释的是它真正要下的一手，而不是事后的猜测。
    """
    side = _agent_side(game)
    if side is None:
        return None
    if game.pending is not None:
        return {**_pending_view(game.pending), "pending": True}

    decision = agent_mod.decide(
        game.pos,
        side,
        persona_key=game.persona,
        difficulty=game.difficulty,
        use_llm=game.use_llm and game.mode in ("agent", "learn"),
    )
    if game.pos.board[decision.move] != 0:
        raise HTTPException(500, "引擎返回了非法落点")
    if game.pos.forbidden(decision.move, side):
        raise HTTPException(500, "引擎返回了禁手点")

    x, y = coord(decision.move)
    payload = {
        "x": x,
        "y": y,
        "color": side,
        "comment": decision.comment,
        "source": decision.source,
        "think_ms": decision.think_ms,
        "depth": decision.depth,
        "nodes": decision.nodes,
        "llm_used": decision.llm_used,
        "candidates": [c.to_dict() for c in decision.candidates],
    }

    if game.step and not force:
        game.pending = {**payload, "idx": decision.move}
        return {**payload, "pending": True}

    game.apply(
        decision.move,
        side,
        comment=decision.comment,
        source=decision.source,
        think_ms=decision.think_ms,
    )
    return payload


@router.post("/games")
def create_game(req: NewGameRequest, request: Request) -> dict:
    user = require_user(request)
    game = store.create(
        mode=req.mode,
        difficulty=req.difficulty,
        persona=req.persona,
        human_color=req.human_color,
        use_llm=req.use_llm,
        rule=req.rule,
        strict_ban=req.strict_ban,
        step=req.step,
        owner=user["name"],
        owner_id=user["id"],
    )
    with game.lock:
        opening = play_agent(game, force=True)
        settlement.settle(game)
        payload = {"game": game.snapshot(), "agent": opening}
        if game.step and game.moves:
            payload["explain"] = analysis.explain_move(game, 0)
    return payload


@router.get("/games/{game_id}")
def get_game(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        return {"game": game.snapshot(), "agent": None}


@router.post("/games/{game_id}/move")
def make_move(game_id: str, req: MoveRequest, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        if game.mode == "online":
            raise HTTPException(409, "联网对局请通过实时通道落子")
        if game.status != "playing":
            raise HTTPException(409, "对局已结束")
        if game.pending is not None:
            raise HTTPException(409, "请先确认待落子的一手，再继续落子")
        if not (0 <= req.x < SIZE and 0 <= req.y < SIZE):
            raise HTTPException(400, "坐标越界")
        side = game.turn
        if game.mode != "pvp" and side == opponent_of(game.human_color):
            raise HTTPException(409, "未到你的回合")

        idx = req.y * SIZE + req.x
        if game.pos.board[idx] != 0:
            raise HTTPException(400, "该点已有棋子")

        ban = game.pos.forbidden(idx, side)
        if ban and not game.strict_ban:
            raise HTTPException(400, f"{rules.BAN_LABELS[ban]}：该点黑棋不能落子")

        # 教练要在落子之前读局面，落完之后「对手原本有什么威胁」就没了
        lesson = coach_mod.analyze(game.pos, idx, side) if game.mode == "learn" else None

        game.apply(idx, side, ban=ban)
        reply = play_agent(game)
        settlement.settle(game)
        payload = {"game": game.snapshot(), "agent": reply}
        if game.step and game.moves:
            # 讲解模式下当场把人类这一手讲清楚，省一次往返。
            # Agent 的应对已经落下时最后两手是「人 + 机」，人类那手在倒数第二位；
            # 停在待确认状态或对局刚结束时，人类那手就是最后一手。
            applied = reply is not None and game.pending is None
            index = len(game.moves) - (2 if applied else 1)
            if index >= 0:
                payload["explain"] = analysis.explain_move(game, index)
        if game.mode == "learn":
            payload["coach"] = lesson
            payload["tactics"] = coach_mod.tactic_notes(game.pos, game.turn)
        return payload


@router.get("/games/{game_id}/tactics")
def get_tactics(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        return coach_mod.tactic_notes(game.pos, game.turn)


@router.post("/games/{game_id}/hint")
def get_hint(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        if game.status != "playing":
            raise HTTPException(409, "对局已结束")
        side = game.turn if game.mode == "pvp" else game.human_color
        return agent_mod.hint(game.pos, side, game.difficulty)


@router.post("/games/{game_id}/undo")
def undo(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        if game.mode == "online":
            raise HTTPException(409, "联网对局不支持悔棋")
        if game.pending is not None and not game.moves:
            game.pending = None
            return {"game": game.snapshot(), "agent": None}
        if not game.moves:
            raise HTTPException(409, "没有可撤销的棋步")
        steps = 2 if game.mode != "pvp" else 1
        if len(game.moves) < steps:
            steps = len(game.moves)
        if game.moves and game.moves[-1]["color"] == game.human_color and game.mode != "pvp":
            steps = 1
        for _ in range(steps):
            game.pos.undo()
            game.moves.pop()
        game.status = "playing"
        game.winner = None
        game.win_line = []
        game.result_reason = ""
        game.recorded = False
        game._ban_cache = None
        game.pending = None
        _trim_moves(game)

        reply = play_agent(game)
        return {"game": game.snapshot(), "agent": reply}


def _trim_moves(game: Game) -> None:
    """悔棋之后棋谱表也得跟着退。少这一步，重启重放会多出幽灵棋子。"""
    from .. import repo

    try:
        repo.trim_moves(game.id, len(game.moves))
    except Exception:
        pass


@router.post("/games/{game_id}/review")
def get_review(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        return {"review": agent_mod.review(game.moves, game.winner, game.difficulty)}


@router.get("/games/{game_id}/insight")
def get_insight(game_id: str, request: Request) -> dict:
    """胜率、点位预测、棋形普查、逐手胜率曲线，一次拿全。"""
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        return analysis.payload(game)


@router.post("/games/{game_id}/explain")
def explain_move(game_id: str, req: ExplainRequest, request: Request) -> dict:
    """解释某一手。不给 index 就解释最后一手。"""
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        if not game.moves:
            raise HTTPException(409, "对局尚无落子")
        if req.index is not None and not (0 <= req.index < len(game.moves)):
            raise HTTPException(400, "棋步索引越界")
        return {"explain": analysis.explain_move(game, req.index)}


@router.get("/games/{game_id}/pending")
def get_pending(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        preview = analysis.pending_preview(game)
        if preview is None:
            raise HTTPException(404, "当前没有待确认的一手")
        return {"preview": preview, "pending": game.snapshot()["pending"]}


@router.post("/games/{game_id}/resume")
def resume(game_id: str, request: Request) -> dict:
    user = require_user(request)
    game = require_game(game_id, user)
    with game.lock:
        if game.pending is None:
            raise HTTPException(409, "当前没有待确认的一手")
        pending = dict(game.pending)
        if game.status != "playing":
            game.pending = None
            raise HTTPException(409, "对局已结束")

        game.apply(
            pending["idx"],
            pending["color"],
            comment=pending.get("comment", ""),
            source=pending.get("source", ""),
            think_ms=pending.get("think_ms", 0),
        )
        settlement.settle(game)
        return {
            "game": game.snapshot(),
            "agent": _pending_view(pending),
            "explain": analysis.explain_move(game, len(game.moves) - 1),
        }
