"""可观测性：请求 ID、结构化日志、Prometheus 指标、限流。

没有引 prometheus_client 或 structlog，理由和项目里其他「不引库」的决定一样：
需要的东西加起来不到 200 行，多一个依赖就多一处版本耦合和一份给部署环境
准备的依赖清单。自己实现的代价是得想清楚标签基数——这是唯一容易搞错的地方。

指标只按「路由模板 + 方法 + 状态码」打标签，绝不按实际路径。`/api/games/abc123`
如果原样进标签，每个对局都会长出三条新时间序列，一天下来内存就被拉爆了。
路由模板 `/api/games/{game_id}` 的基数由代码决定，与用户行为无关。

日志走 stdout 的单行 JSON。容器和云平台的日志采集器都认这个格式，
比给每个环境配一遍正则解析靠谱。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

# 限流档位：路径前缀 → (每分钟上限, 作用域)。
# 鉴权接口按 IP 限，其余按用户；未登录一律按 IP。
LIMITS: tuple[tuple[str, int, str], ...] = (
    ("/api/auth/login", 30, "ip"),
    ("/api/auth/register", 30, "ip"),
    ("/api/auth/password", 20, "user"),
    ("/api/lab/match", 120, "user"),
    ("/api/arena/match", 120, "user"),
    ("/api/games/", 900, "user"),
)
DEFAULT_LIMIT = 1200  # 每分钟
WINDOW = 60.0


# --------------------------------------------------------------------- 指标


@dataclass
class Metrics:
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(
        default_factory=lambda: defaultdict(float)
    )
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0] * (len(LATENCY_BUCKETS) + 2))
    )
    gauges: dict[str, Callable[[], float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def _key(name: str, labels: dict[str, str]) -> tuple[str, tuple[tuple[str, str], ...]]:
        return name, tuple(sorted(labels.items()))

    def inc(self, name: str, value: float = 1.0, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            self.counters[key] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        key = self._key(name, labels)
        with self._lock:
            row = self.histograms[key]
            for i, edge in enumerate(LATENCY_BUCKETS):
                if value <= edge:
                    row[i] += 1
            row[-2] += 1  # count
            row[-1] += value  # sum

    def gauge(self, name: str, fn: Callable[[], float]) -> None:
        self.gauges[name] = fn

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}{_fmt(labels)} {_num(value)}")
            for (name, labels), row in sorted(self.histograms.items()):
                for i, edge in enumerate(LATENCY_BUCKETS):
                    bucket_labels = dict(labels)
                    bucket_labels["le"] = str(edge)
                    lines.append(f"{name}_bucket{_fmt(bucket_labels)} {_num(row[i])}")
                plus = dict(labels)
                plus["le"] = "+Inf"
                lines.append(f"{name}_bucket{_fmt(plus)} {_num(row[-2])}")
                lines.append(f"{name}_count{_fmt(labels)} {_num(row[-2])}")
                lines.append(f"{name}_sum{_fmt(labels)} {_num(row[-1])}")
        for name, fn in self.gauges.items():
            try:
                value = fn()
            except Exception:
                continue
            lines.append(f"{name} {_num(value)}")
        return "\n".join(lines) + "\n"


metrics = Metrics()


def _fmt(labels) -> str:
    """标签渲染。dict 和 (key, value) 序列都要能接——直方图那边是临时拼的 dict。"""
    if not labels:
        return ""
    pairs = labels.items() if isinstance(labels, dict) else labels
    body = ",".join(f'{k}="{_escape(v)}"' for k, v in pairs)
    return "{" + body + "}"


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _num(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------- 限流


class RateLimiter:
    """固定窗口计数器。

    滑动窗口更精确，但要为每个身份存一个时间戳列表；这里防的是脚本硬撞，
    固定窗口的边界效应（跨窗口瞬时可打两倍）可以接受。
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], tuple[float, int]] = {}
        self._lock = threading.Lock()
        self._last_gc = time.time()

    def check(self, identity: str, limit: int) -> tuple[bool, int]:
        now = time.time()
        key = (identity, str(limit))
        with self._lock:
            start, count = self._hits.get(key, (now, 0))
            if now - start >= WINDOW:
                start, count = now, 0
            count += 1
            self._hits[key] = (start, count)
            if now - self._last_gc > WINDOW * 5:
                self._gc(now)
                self._last_gc = now
        remaining = max(0, limit - count)
        return count <= limit, remaining

    def _gc(self, now: float) -> None:
        stale = [k for k, (start, _) in self._hits.items() if now - start >= WINDOW]
        for key in stale:
            self._hits.pop(key, None)


limiter = RateLimiter()


def _limit_for(path: str) -> tuple[int, str]:
    for prefix, limit, scope in LIMITS:
        if path.startswith(prefix):
            return limit, scope
    return DEFAULT_LIMIT, "user"


def _limit_bucket(path: str) -> str:
    """被限流的请求按「命中了哪条规则」打标签，不按真实路径。

    真实路径是外部可控的——扫描器随便造 URL 就能把时间序列造到几万条，
    这类标签一旦进 Prometheus 是清不掉的。
    """
    for prefix, _limit, _scope in LIMITS:
        if path.startswith(prefix):
            return prefix
    return "default"


def _identity(request: Request, scope: str) -> str:
    if scope == "user":
        token = request.headers.get("authorization", "")
        if token:
            # 只取 token 的后 8 位做键：完整 token 进内存字典没必要，
            # 而且日志/转储里出现完整凭据是明确的坏味道。
            return "u:" + token[-8:]
    client = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # 取最后一跳。前面的是客户端自己填的，可以伪造。
        client = forwarded.split(",")[-1].strip() or client
    return "ip:" + client


# --------------------------------------------------------------------- 中间件


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path
    return "unmatched"


class Observability(BaseHTTPMiddleware):
    """一次请求做四件事：发号、计时、记指标、写日志。

    404 和 500 也走这里——异常路径上没日志，线上排查就只能靠猜。
    """

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()

        if request.url.path.startswith("/api/"):
            limit, scope = _limit_for(request.url.path)
            allowed, remaining = limiter.check(_identity(request, scope), limit)
            if not allowed:
                metrics.inc("gomoku_ratelimit_blocked_total", bucket=_limit_bucket(request.url.path))
                response = Response(
                    json.dumps({"detail": "请求过于频繁，请稍后再试"}, ensure_ascii=False),
                    status_code=429,
                    media_type="application/json",
                )
                _decorate(response, request_id, started, request)
                return response
        else:
            remaining = -1

        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - started
            metrics.inc(
                "gomoku_http_requests_total", method=request.method, path=_route_label(request), status="500"
            )
            metrics.observe(
                "gomoku_http_request_duration_seconds",
                elapsed,
                method=request.method,
                path=_route_label(request),
            )
            log_json(
                logging.ERROR,
                "request failed",
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                ms=round(elapsed * 1000, 1),
            )
            raise

        _decorate(response, request_id, started, request)
        if remaining >= 0:
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


def _decorate(response: Response, request_id: str, started: float, request: Request) -> None:
    elapsed = time.perf_counter() - started
    route = _route_label(request)
    metrics.inc("gomoku_http_requests_total", method=request.method, path=route, status=str(response.status_code))
    metrics.observe("gomoku_http_request_duration_seconds", elapsed, method=request.method, path=route)

    response.headers["X-Request-Id"] = request_id
    # 安全响应头。没有 CDN 兜底的时候这些只能自己加。
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")

    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/metrics"):
        lvl = logging.WARNING if response.status_code >= 500 else logging.INFO
        if response.status_code < 500:
            lvl = logging.DEBUG if response.status_code < 400 else logging.INFO
        log_json(
            lvl,
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round(elapsed * 1000, 1),
            route=route,
        )


# --------------------------------------------------------------------- 日志


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "ms", "route", "game", "user"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(payload, ensure_ascii=False)


def log_json(level: int, message: str, **fields) -> None:
    logger = logging.getLogger("gomoku.http")
    if not logger.isEnabledFor(level):
        return
    record = logger.makeRecord(
        logger.name, level, __file__, 0, message, None, None
    )
    for key, value in fields.items():
        setattr(record, key, value)
    logger.handle(record)


def setup_logging() -> None:
    level_name = os.environ.get("GOMOKU_LOG", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # uvicorn 自带的 access log 和这里的结构化日志重复，关掉
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
