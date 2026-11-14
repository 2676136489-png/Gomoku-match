"""元信息与运维端点。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Request, Response

from .. import db, lab, tutorial
from ..gomoku import agent as agent_mod
from ..gomoku import algorithms, net, rules
from ..gomoku.llm import LLMConfig
from ..gomoku.position import SIZE
from ..observability import metrics
from ..runtime import accounts, store
from ..version import VERSION
from .deps import AUTH_COOKIE

router = APIRouter(prefix="/api", tags=["meta"])
STARTED = time.time()


@router.get("/health")
def health(request: Request) -> dict:
    """存活探针。不碰磁盘、不查表——它只会被 k8s 每十秒打一次。

    `auth_seen` 是部署排障用的：反向代理是否把凭据转发过来了，只有在这里才看得见。
    它只回报「哪个通道带了 token」，不回报 token 本身。
    """
    return {
        "status": "ok",
        "version": VERSION,
        "uptime_s": round(time.time() - STARTED, 1),
        "size": SIZE,
        "accounts": accounts.count(),
        "persistent": accounts.persistent,
        "llm": LLMConfig().describe(),
        "languages": lab.capabilities()["languages"],
        "auth_seen": _auth_seen(request),
    }


def _auth_seen(request: Request) -> dict[str, bool]:
    header = request.headers.get("authorization", "")
    return {
        "header": header.lower().startswith("bearer ") and bool(header[7:].strip()),
        "alt_header": bool(request.headers.get("x-auth-token", "").strip()),
        "query": bool(request.query_params.get("token")),
        "cookie": bool(request.cookies.get(AUTH_COOKIE)),
    }


@router.get("/ready")
def ready() -> Response:
    """就绪探针。这里才真的验：数据库能写、前端静态站在。

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

    dist = db.WEB_DIST / "index.html"
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
        "modes": [
            {"key": "agent", "label": "AI 对弈", "brief": "三档共用一套搜索算法；本档由大模型在候选落点中择一，模型未启用时算法自选。"},
            {"key": "learn", "label": "学习模式", "brief": "对手决策与 AI 对弈相同，每手附一条按棋规生成的教练点评；点评不调用模型。"},
            {"key": "engine", "label": "纯引擎", "brief": "仅由搜索算法决策，全程不调用模型、无点评；作为对照组。"},
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
    }
