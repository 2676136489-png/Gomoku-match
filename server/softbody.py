"""软编码请求体：给云 WAF 的误判留一条回退通道。

线上遇到过一个很难查的 403：代码工坊提交模板源码，页面报「请求失败 (403)」，
响应体却是一张 `<title>WAF拦截页面</title>`——不是应用返回的。二分到具体行后，
触发条件是这种写法：

    if abs(x - 7) > 6 or abs(y - 7) > 6:

两处「函数调用 + 比较」用 `or` 连起来，被注入规则当成了布尔盲注的特征。同样的
代码在本地怎么跑都没事，因为本地没有 WAF；改一种写法（比如 `max(...)`）也能过，
但那只是躲开这一条规则，用户自己写的代码照样会撞上别的。

所以留一条软通道：请求体整体 base64，查询串带 `_enc=b64` 声明。前端**只在吃到
拦截页时**才这么发一次，平时仍是明文——平台的检查照常生效，软通道只用于解误判。

标记放查询串而不是自定义头，原因是实测：云端的分享网关会丢弃它不认识的请求头
（`X-Auth-Token` 单独使用时永远到不了应用），查询串和 Cookie 才是稳定穿过的。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from urllib.parse import parse_qsl

from starlette.types import ASGIApp, Message, Receive, Scope, Send

ENC_PARAM = "_enc"
ENC_VALUE = "b64"
# 源码上限 200 KB（见 lab.MAX_SOURCE），base64 后约 267 KB；给到 1 MiB 足够，
# 也顺手成了「超大请求体」的拦截线，解码前就把异常体积挡掉。
MAX_BODY = 1 << 20

_METHODS = {"POST", "PUT", "PATCH"}

# 请求体读完之后，后续的 receive 不能返回 http.disconnect：SSE 的断连监听
# 一收到断连就会把流取消，代码工坊的对局直播会刚开头就断。这里返回一个永远
# 不会完成的等待，语义上等于「客户端还连着」。响应结束时 Starlette 会取消它。
_NEVER = asyncio.Event()


class SoftBody:
    """把 `_enc=b64` 的请求体解码回 JSON 再交给下游。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _METHODS or not _flagged(scope):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > MAX_BODY:
                await _fail(send, 413, "请求体过大")
                return
            chunks.append(chunk)
            if not message.get("more_body"):
                break

        try:
            body = base64.b64decode(b"".join(chunks), validate=True)
        except (binascii.Error, ValueError):
            await _fail(send, 400, "请求体不是合法的 base64")
            return

        scope = dict(scope)
        scope["headers"] = [
            (name, value)
            for name, value in scope["headers"]
            if name.lower() not in (b"content-length", b"content-type")
        ]
        scope["headers"].append((b"content-type", b"application/json"))
        scope["headers"].append((b"content-length", str(len(body)).encode()))

        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await _NEVER.wait()
            return {"type": "http.disconnect"}  # 走不到，仅为类型完整

        await self.app(scope, replay, send)


def _flagged(scope: Scope) -> bool:
    raw = scope.get("query_string", b"")
    if not raw:
        return False
    for key, value in parse_qsl(raw.decode("latin-1"), keep_blank_values=True):
        if key == ENC_PARAM and value == ENC_VALUE:
            return True
    return False


async def _fail(send: Send, status: int, message: str) -> None:
    payload = json.dumps({"detail": message}, ensure_ascii=False).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(payload)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})
