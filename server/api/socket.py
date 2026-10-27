"""WebSocket 端点。

浏览器无法给 WebSocket 握手加自定义头，token 只能走 query。
代价是它会出现在某些代理的访问日志里，所以这里的日志只记 path、不记 query；
服务端也只把它当成一次性凭据用——会话本身是短期的，泄露窗口有限。
"""

from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import repo, settlement
from ..gomoku.position import BLACK, WHITE
from ..observability import metrics
from ..realtime import MoveRejected, apply_move, finish_and_broadcast, hub, view_for
from ..runtime import accounts, store

log = logging.getLogger("gomoku.ws")
router = APIRouter()

CHAT_MAX = 200
CHAT_INTERVAL = 1.0


def _user(token: str | None) -> dict | None:
    user_id = accounts.resolve_id(token)
    if user_id is None:
        return None
    return repo.user_by_id(user_id)


@router.websocket("/ws/game/{game_id}")
async def ws_game(ws: WebSocket, game_id: str) -> None:
    token = ws.query_params.get("token")
    user = _user(token)
    if user is None:
        await ws.close(code=4401, reason="unauthorized")
        return

    game = store.get(game_id)
    if game is None:
        await ws.close(code=4404, reason="not found")
        return

    seat = game.player_of(user["id"])
    if seat is None:
        await ws.close(code=4403, reason="forbidden")
        return

    hub.bind_loop()
    await ws.accept()
    await hub.join_game(game_id, user["id"], ws)
    metrics.inc("gomoku_ws_connections_total")

    await ws.send_json({"type": "hello", "you": seat, "game": view_for(game, user["id"])})
    await hub.broadcast(
        game_id,
        {"type": "presence", "online": hub.presence(game_id)},
        exclude=user["id"],
    )

    last_chat = 0.0
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _error(ws, "消息不是合法 JSON")
                continue
            kind = msg.get("type")

            if kind == "ping":
                await ws.send_json({"type": "pong", "at": time.time()})
                continue

            if kind == "sync":
                await ws.send_json({"type": "hello", "you": seat, "game": view_for(game, user["id"])})
                continue

            if kind == "chat":
                now = time.time()
                if now - last_chat < CHAT_INTERVAL:
                    await _error(ws, "说得太快了")
                    continue
                text = str(msg.get("text", ""))[:CHAT_MAX].strip()
                if not text:
                    continue
                last_chat = now
                await hub.broadcast(
                    game_id,
                    {"type": "chat", "from": user["name"], "seat": seat, "text": text, "at": now},
                )
                continue

            if kind == "resign":
                with game.lock:
                    if game.status != "playing":
                        await _error(ws, "对局已结束")
                        continue
                    game.resign(seat)
                    payload = finish_and_broadcast(game)
                    if payload:
                        payload["reason"] = "认输"
                await hub.broadcast(game_id, payload)
                continue

            if kind == "move":
                try:
                    x = int(msg.get("x"))
                    y = int(msg.get("y"))
                except (TypeError, ValueError):
                    await _error(ws, "落点坐标不是整数")
                    continue

                with game.lock:
                    try:
                        event = apply_move(game, user, x, y)
                    except MoveRejected as exc:
                        await _error(ws, str(exc))
                        continue
                    metrics.inc("gomoku_moves_total", mode="online")
                    finished = finish_and_broadcast(game) if game.status == "finished" else None
                    events = [event]
                    if finished:
                        events.append(finished)

                for ev in events:
                    if ev["type"] == "state":
                        # 每人的 seat 不同，快照要按人定制
                        await _send_state(game_id, ev)
                    else:
                        await hub.broadcast(game_id, ev)
                continue

            await _error(ws, f"未知消息类型 {kind}")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.warning("ws 异常 game=%s user=%s", game_id, user["id"], exc_info=True)
    finally:
        remaining = hub.leave_game(game_id, user["id"])
        if remaining:
            await hub.broadcast(game_id, {"type": "presence", "online": hub.presence(game_id)})


async def _send_state(game_id: str, event: dict) -> None:
    """state 事件按人发：同一个局面，双方看到的 seat 和 presence 不一样。"""
    game = store.get(game_id)
    if game is None:
        return
    for user_id in hub.presence(game_id):
        ws = hub._games.get(game_id, {}).get(user_id)
        if ws is None:
            continue
        try:
            await ws.send_json({**event, "game": view_for(game, user_id)})
        except Exception:
            hub.leave_game(game_id, user_id)


async def _error(ws: WebSocket, message: str) -> None:
    try:
        await ws.send_json({"type": "error", "message": message})
    except Exception:
        pass


# --------------------------------------------------------------------- 大厅


@router.websocket("/ws/lobby")
async def ws_lobby(ws: WebSocket) -> None:
    user = _user(ws.query_params.get("token"))
    if user is None:
        await ws.close(code=4401, reason="unauthorized")
        return

    hub.bind_loop()
    await ws.accept()
    await hub.lobby_connect(user["id"], ws)
    await ws.send_json(
        {
            "type": "ready",
            "you": {"name": user["name"], "rating": user["rating"]},
            "queue": hub.queue_size(),
        }
    )

    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue

            kind = msg.get("type")
            if kind == "ping":
                await ws.send_json({"type": "pong", "at": time.time()})
            elif kind == "queue":
                result = await hub.enqueue(
                    user,
                    rule=str(msg.get("rule", "renju")),
                    base=int(msg.get("clock_base", 300) or 300),
                    inc=int(msg.get("clock_inc", 5) or 0),
                )
                metrics.inc("gomoku_match_queue_joins_total")
                if not result["paired"]:
                    await ws.send_json({"type": "queued", "position": result["position"]})
            elif kind == "cancel":
                hub.dequeue(user["id"])
                await ws.send_json({"type": "cancelled"})
            else:
                await _error(ws, f"未知消息类型 {kind}")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.warning("lobby 异常 user=%s", user["id"], exc_info=True)
    finally:
        hub.lobby_disconnect(user["id"])
