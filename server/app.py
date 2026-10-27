"""HTTP 服务装配。

这个文件只做四件事：建应用、挂中间件、挂路由、托管前端产物。具体逻辑都在
`api/` 下的路由模块里——上一版这里是 690 行，改一个接口要在里面翻半天。

启动时做三件事：跑数据库迁移、把 v2 的 accounts.json 导一次、起超时巡检线程。
三件都是幂等的，重启多少次都不会重复执行。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import accounts, db, realtime, repo
from .api import accounts as api_accounts
from .api import games as api_games
from .api import lab as api_lab
from .api import meta as api_meta
from .api import online as api_online
from .api import socket as api_socket
from .observability import Observability, metrics, setup_logging
from .runtime import accounts as account_store
from .runtime import store
from .version import VERSION

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "web" / "dist"

log = logging.getLogger("gomoku.app")


def _cors_origins() -> list[str]:
    raw = os.environ.get("GOMOKU_CORS", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    # 默认只放开发用的两个端口。线上是同源部署，根本不需要 CORS；
    # 用 ["*"] 会让任何站点都能拿着用户浏览器里的 token 打接口。
    return ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    db.connect()
    log.info("database ready", extra={"path": db.db_path(), "version": db.version()})

    if repo.count_users() == 0 and accounts.LEGACY_FILE.exists():
        result = accounts.migrate_legacy_json()
        log.info("legacy import: %s", result)

    repo.purge_sessions()
    realtime.start_ticker()
    log.info("service up", extra={"version": VERSION})
    yield
    log.info("service down")


app = FastAPI(
    title="Gomoku Match",
    version=VERSION,
    description="15×15 国际连珠：规则引擎、Alpha-Beta 搜索、学习型落点网络、在线对战与天梯。",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(Observability)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Request-Id", "X-RateLimit-Remaining"],
)

for module in (api_accounts, api_games, api_lab, api_meta, api_online, api_socket):
    app.include_router(module.router)


# --------------------------------------------------------------------- 指标

metrics.gauge("gomoku_accounts_total", account_store.count)
metrics.gauge("gomoku_online_users", realtime.hub.online_users)
metrics.gauge("gomoku_live_games", realtime.hub.online_games)
metrics.gauge("gomoku_match_queue", realtime.hub.queue_size)
metrics.gauge("gomoku_hot_games", lambda: len(store._games))


# --------------------------------------------------------------------- 错误


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """把 pydantic 的报错压成一句人话。

    默认结构是三层嵌套的 list，前端弹出来是一坨 JSON。取第一个字段名和原因就够了。
    """
    detail = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in detail.get("loc", ())[1:]) or "参数"
    message = f"{field}：{detail.get('msg', '格式不正确')}"
    return JSONResponse(
        {"detail": message, "request_id": getattr(request.state, "request_id", None)},
        status_code=422,
    )


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        {"detail": exc.detail, "request_id": getattr(request.state, "request_id", None)},
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log.error(
        "unhandled error", exc_info=exc, extra={"request_id": request_id, "path": request.url.path}
    )
    return JSONResponse(
        {
            "detail": "服务器内部错误",
            "request_id": request_id,
            "hint": "把这个 request_id 报给管理员，日志里能查到完整堆栈",
        },
        status_code=500,
    )


# --------------------------------------------------------------------- 静态站点


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise StarletteHTTPException(404)
        candidate = DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # index.html 不缓存，带 hash 的 assets 缓存一年
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
