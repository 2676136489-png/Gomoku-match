"""代码工坊、算法竞技场、网络扫描、比赛协议兼容层。

共同点是「一次调用跑一局或跑一次扫描」，都不落库——它们是实验，不是对局记录。
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import arena, botzone, lab
from ..gomoku import algorithms, net, rules
from ..gomoku.position import CELLS, Position, coord
from ..observability import metrics
from .deps import require_user

router = APIRouter(prefix="/api", tags=["lab"])


class BotzoneRequest(BaseModel):
    requests: list[dict] = Field(default_factory=list)
    responses: list[dict] = Field(default_factory=list)


class LabVerifyRequest(BaseModel):
    language: str = "python"
    source: str = ""
    timeout: float = 2.0


class LabRunRequest(BaseModel):
    language: str = "python"
    source: str = ""
    opponent: str = "minimax"
    difficulty: int = Field(default=3, ge=1, le=4)
    code_color: int = Field(default=1, ge=1, le=2)
    rule: str = Field(default=rules.RENJU, pattern="^(renju|freestyle)$")
    timeout: float = 2.0
    avoid_bans: bool = True


class ArenaRunRequest(BaseModel):
    left: dict
    right: dict
    rule: str = Field(default=rules.RENJU, pattern="^(renju|freestyle)$")
    difficulty: int = Field(default=3, ge=1, le=4)


class NeuralScanRequest(BaseModel):
    board: list[int] = Field(default_factory=list)
    color: int = 1
    rule: str = rules.RENJU


def _sse(events: Iterator[dict], kind: str) -> StreamingResponse:
    def gen() -> Iterator[str]:
        try:
            for ev in events:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except lab.LabError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': exc.message, 'detail': exc.detail}, ensure_ascii=False)}\n\n"
        except Exception as exc:  # 流一旦开始就只能用事件报错，不能改状态码
            metrics.inc("gomoku_stream_errors_total", kind=kind)
            yield f"data: {json.dumps({'type': 'error', 'message': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/lab/capabilities")
def lab_capabilities(request: Request) -> dict:
    require_user(request)
    return {"capabilities": lab.capabilities(), "net": net.get_net().describe()}


@router.get("/lab/samples")
def lab_samples(request: Request) -> dict:
    require_user(request)
    return lab.samples()


@router.post("/lab/verify")
def lab_verify(req: LabVerifyRequest, request: Request) -> dict:
    require_user(request)
    try:
        return arena.verify(
            {"kind": "code", "language": req.language, "source": req.source, "timeout": req.timeout}
        )
    except lab.LabError as exc:
        return {"ok": False, "error": exc.message, "detail": exc.detail}


@router.post("/lab/match")
def lab_match(req: LabRunRequest, request: Request) -> StreamingResponse:
    require_user(request)
    code = {
        "kind": "code",
        "language": req.language,
        "source": req.source,
        "timeout": max(0.3, min(req.timeout, 10.0)),
        "avoid_bans": req.avoid_bans,
        "label": "你的代码",
    }
    engine = {
        "kind": "algorithm",
        "algorithm": req.opponent,
        "difficulty": req.difficulty,
        "label": algorithms.ALGORITHMS.get(req.opponent, algorithms.ALGORITHMS["minimax"])["label"],
    }
    left, right = (code, engine) if req.code_color == 1 else (engine, code)
    try:
        events = arena.run(left, right, rule=req.rule, max_seconds=90.0)
    except lab.LabError as exc:
        raise HTTPException(400, f"{exc.message}{('：' + exc.detail) if exc.detail else ''}") from exc
    metrics.inc("gomoku_lab_matches_total", language=req.language)
    return _sse(events, "lab")


@router.post("/arena/match")
def arena_match(req: ArenaRunRequest, request: Request) -> StreamingResponse:
    require_user(request)
    try:
        events = arena.run(req.left, req.right, rule=req.rule, difficulty=req.difficulty, max_seconds=120.0)
    except lab.LabError as exc:
        raise HTTPException(400, f"{exc.message}{('：' + exc.detail) if exc.detail else ''}") from exc
    metrics.inc(
        "gomoku_arena_matches_total",
        left=str(req.left.get("algorithm", req.left.get("kind", "?")))[:24],
        right=str(req.right.get("algorithm", req.right.get("kind", "?")))[:24],
    )
    return _sse(events, "arena")


@router.post("/neural/scan")
def neural_scan(req: NeuralScanRequest, request: Request) -> dict:
    """把落点网络对当前每个空点的打分摊开给前端画热力图。"""
    require_user(request)
    model = net.get_net()
    if not model.ready:
        return {"ready": False, "points": [], "meta": model.describe()}

    if len(req.board) != CELLS:
        raise HTTPException(400, "board 必须是 225 长度的数组")

    pos = Position(rule=req.rule if req.rule in rules.RULE_MODES else rules.RENJU)
    for idx, value in enumerate(req.board):
        if value in (1, 2):
            pos.place(idx, value)

    color = 1 if req.color == 1 else 2
    spots = pos.candidate_list()
    if not spots:
        spots = [i for i in range(CELLS) if pos.board[i] == 0]

    feats = net.encode(pos, spots, color)
    points = []
    for f, idx in zip(feats, spots):
        if pos.board[idx] != 0:
            continue
        x, y = coord(idx)
        points.append({"x": x, "y": y, "score": round(model.score(f), 4)})
    points.sort(key=lambda p: p["score"], reverse=True)

    return {"ready": True, "color": color, "points": points, "top": points[:5], "meta": model.describe()}


@router.post("/botzone")
def botzone_endpoint(req: BotzoneRequest) -> dict:
    """保留原始比赛协议。把 Botzone 的 requests/responses 直接丢进来即可。"""
    return botzone.play(req.model_dump())
