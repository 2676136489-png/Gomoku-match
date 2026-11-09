"""贪心学习实验室：棋谱库与训练。

一个页面要的东西全在这里：看棋谱、传棋谱、用棋谱训练、看训练出来的权重表。
训练走 SSE——它要跑一秒多，前端得看到「到第几轮了」而不是一个转圈。
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import corpus
from ..gomoku import algorithms, tuning
from ..observability import metrics
from .deps import require_user

router = APIRouter(prefix="/api", tags=["greedy"])

_TRAIN_LOCK = threading.Lock()


class UploadRequest(BaseModel):
    name: str = Field(default="", max_length=60)
    text: str = Field(min_length=1)


def _sse(events: Iterator[dict]) -> StreamingResponse:
    def gen() -> Iterator[str]:
        for ev in events:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.get("/greedy/status")
def status(request: Request) -> dict:
    require_user(request)
    model = tuning.get_model()
    return {
        "model": model.describe(),
        "corpus": corpus.summary(),
        "algorithms": [a for a in algorithms.describe() if a["key"] in ("greedy", "tuned")],
        "params": {
            "epochs": tuning.EPOCHS,
            "width": tuning.WIDTH,
            "per_game": tuning.PER_GAME,
            "features": tuning.FEATURES,
        },
    }


@router.get("/greedy/corpus")
def list_corpus(request: Request, preview: int = 48) -> dict:
    require_user(request)
    preview = max(0, min(preview, 225))
    return {
        "items": corpus.items(preview=preview),
        "summary": corpus.summary(),
    }


@router.get("/greedy/corpus/{item_id}")
def get_corpus(item_id: str, request: Request) -> dict:
    require_user(request)
    item = corpus.get(item_id)
    if item is None:
        raise HTTPException(404, "棋谱不存在")
    return {"item": item}


@router.post("/greedy/corpus")
def upload_corpus(req: UploadRequest, request: Request) -> dict:
    user = require_user(request)
    try:
        item = corpus.add(req.name or "上传棋谱", req.text, author=user["name"])
    except corpus.CorpusError as exc:
        raise HTTPException(400, str(exc)) from exc
    metrics.inc("gomoku_corpus_uploads_total")
    return {"item": item, "summary": corpus.summary()}


@router.delete("/greedy/corpus/{item_id}")
def delete_corpus(item_id: str, request: Request) -> dict:
    require_user(request)
    try:
        ok = corpus.delete(item_id)
    except corpus.CorpusError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "棋谱不存在")
    return {"ok": True, "summary": corpus.summary()}


@router.post("/greedy/reset")
def reset(request: Request) -> dict:
    require_user(request)
    if _TRAIN_LOCK.locked():
        raise HTTPException(409, "训练正在进行，等它结束再重置")
    tuning.get_model().reset()
    return {"model": tuning.get_model().describe()}


@router.post("/greedy/train")
def train(request: Request) -> StreamingResponse:
    require_user(request)
    if not _TRAIN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有一次训练在跑，等它结束再试")

    records = corpus.training_set()
    if len(records) < 2:
        _TRAIN_LOCK.release()
        raise HTTPException(400, "棋谱太少：至少要有两盘可用棋谱")

    def events() -> Iterator[dict]:
        q: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                result = tuning.train(records, on_event=q.put)
                q.put({"type": "done", "result": result, "model": tuning.get_model().describe()})
            except Exception as exc:  # 训练失败要把原因送到前端，而不是留一个黑洞
                q.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                q.put(None)

        thread = threading.Thread(target=worker, name="greedy-train", daemon=True)
        thread.start()
        try:
            yield {"type": "start", "records": len(records), "samples_hint": len(records) * tuning.PER_GAME}
            while True:
                ev = q.get()
                if ev is None:
                    break
                yield ev
        finally:
            _TRAIN_LOCK.release()

    metrics.inc("gomoku_greedy_train_total")
    return _sse(events())
