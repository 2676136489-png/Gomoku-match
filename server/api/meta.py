"""元信息与运维端点。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Response

from .. import db, elo, lab, tutorial
from ..gomoku import agent as agent_mod
from ..gomoku import algorithms, net, rules
from ..gomoku.llm import LLMConfig
from ..gomoku.position import SIZE
from ..observability import metrics
from ..runtime import accounts, store
from ..version import VERSION

router = APIRouter(prefix="/api", tags=["meta"])
STARTED = time.time()


@router.get("/health")
def health() -> dict:
    """存活探针。不碰磁盘、不查表——它只会被 k8s 每十秒打一次。"""
    return {
        "status": "ok",
        "version": VERSION,
        "uptime_s": round(time.time() - STARTED, 1),
        "size": SIZE,
        "accounts": accounts.count(),
        "persistent": accounts.persistent,
        "llm": LLMConfig().describe(),
        "languages": lab.capabilities()["languages"],
    }


@router.get("/ready")
def ready() -> Response:
    """就绪探针。这里才真的验：数据库能写、前端构建产物在。

    和 /health 分开是标准做法：liveness 失败该重启进程，readiness 失败只该把
    流量摘走。把两件事合成一个端点，数据库抖一下就会把健康进程反复重启。
    """
    checks: dict[str, dict] = {}
    ok = True

    try:
        with db.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = {"ok": True, **db.info()}
    except Exception as exc:
        ok = False
        checks["database"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    dist = (db.ROOT / "web" / "dist" / "index.html")
    checks["frontend"] = {"ok": dist.exists(), "path": str(dist)}
    ok = ok and dist.exists()

    body = {"status": "ready" if ok else "degraded", "checks": checks}
    return Response(
        content=__import__("json").dumps(body, ensure_ascii=False),
        status_code=200 if ok else 503,
        media_type="application/json",
    )


@router.get("/metrics", include_in_schema=False)
def prometheus() -> Response:
    """Prometheus 文本格式。抓取频率通常 15–60 秒一次，渲染开销可以忽略。"""
    return Response(content=metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


@router.get("/config")
def config() -> dict:
    return {
        "version": VERSION,
        "personas": [
            {"key": k, "label": v["label"], "brief": v["brief"]} for k, v in agent_mod.PERSONAS.items()
        ],
        "difficulties": [
            {"level": k, "label": v["label"], "depth": v["depth"]}
            for k, v in sorted(agent_mod.DIFFICULTY.items())
        ],
        "llm": LLMConfig().describe(),
        "rules": rules.RULE_MODES,
        "ban_labels": rules.BAN_LABELS,
        "algorithms": algorithms.describe(),
        "capabilities": lab.capabilities(),
        "net": net.get_net().describe(),
        "elo": elo.describe(),
        "modes": [
            {"key": "agent", "label": "AI 对弈", "brief": "跟引擎 Agent 下棋，可选执黑或执白"},
            {"key": "learn", "label": "学习模式", "brief": "每手给出教练点评：你形成了什么、对手威胁在哪"},
            {"key": "pvp", "label": "双人对下", "brief": "同一个屏幕轮流落子"},
            {"key": "engine", "label": "纯引擎", "brief": "关掉 Agent 层，只跑搜索"},
        ],
    }


@router.get("/tutorial")
def get_tutorial() -> dict:
    return tutorial.payload()


@router.get("/algorithms")
def get_algorithms() -> dict:
    return {"algorithms": algorithms.describe(), "capabilities": lab.capabilities()}


@router.get("/system")
def system() -> dict:
    """给自己看的运行现场。线上排障第一眼看这里，比翻日志快。"""
    counts = store.stats()
    return {
        "version": VERSION,
        "uptime_s": round(time.time() - STARTED, 1),
        "database": db.info(),
        "games": counts,
        "accounts": accounts.count(),
        "elo": elo.describe(),
    }
