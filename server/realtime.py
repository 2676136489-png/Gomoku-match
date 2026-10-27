"""实时通道：对局 WebSocket、匹配队列、超时裁决。

一条连接的生命周期比一次 HTTP 请求长得多，所以这里的失败模式也完全不同：
消息可能乱序、对手可能直接关掉浏览器、同一局可能同时挂上来两个标签页。
处理办法都是同一个原则——服务端状态是唯一真相，客户端每次重连先拿全量快照。

关于计时：不在服务端每秒广播剩余时间。每手落子时把结算后的剩余秒数打进
快照，前端自己在本地倒计时，到零之前不会有任何对时开销。服务端只在 1 秒一次
的巡检里判断「这一方是不是真的超了」——判罚用服务端时钟，展示用本地时钟。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

from fastapi import WebSocket

from . import repo, settlement
from .gomoku import rules
from .gomoku.position import BLACK, OPPONENT, SIZE, WHITE
from .runtime import accounts, store

log = logging.getLogger("gomoku.realtime")

# 匹配窗口：初始 ±150，每等 5 秒放宽 100，上限 ±800。
# 放太宽会配到实力悬殊的对手，不放宽则深夜没人能配上。
MATCH_BASE = 150
MATCH_GROWTH = 100
MATCH_MAX = 800


class MoveRejected(Exception):
    """落子被规则拒绝。这类错误回给发起的那个客户端，不广播。"""


# --------------------------------------------------------------------- Hub


class Hub:
    def __init__(self) -> None:
        self._games: dict[str, dict[int, WebSocket]] = {}
        self._lobby: dict[int, WebSocket] = {}
        self._queue: list[dict] = []
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # -- 连接管理

    async def join_game(self, game_id: str, user_id: int, ws: WebSocket) -> int:
        with self._lock:
            peers = self._games.setdefault(game_id, {})
            peers[user_id] = ws
            return len(peers)

    def leave_game(self, game_id: str, user_id: int) -> int:
        with self._lock:
            peers = self._games.get(game_id)
            if not peers:
                return 0
            peers.pop(user_id, None)
            if not peers:
                self._games.pop(game_id, None)
                return 0
            return len(peers)

    def presence(self, game_id: str) -> list[int]:
        with self._lock:
            return sorted(self._games.get(game_id, {}).keys())

    def online_games(self) -> int:
        with self._lock:
            return len(self._games)

    def online_users(self) -> int:
        with self._lock:
            return len(self._lobby) + len({u for peers in self._games.values() for u in peers})

    # -- 广播

    async def broadcast(self, game_id: str, payload: dict, *, exclude: int | None = None) -> None:
        with self._lock:
            targets = list((self._games.get(game_id) or {}).items())
        dead: list[int] = []
        for user_id, ws in targets:
            if user_id == exclude:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(user_id)
        for user_id in dead:
            self.leave_game(game_id, user_id)

    def broadcast_threadsafe(self, game_id: str, payload: dict) -> None:
        """给非 async 上下文（超时巡检线程）用的广播入口。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(game_id, payload), loop)
        except RuntimeError:
            pass

    def bind_loop(self) -> None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                pass

    # -- 大厅 / 匹配

    async def lobby_connect(self, user_id: int, ws: WebSocket) -> None:
        with self._lock:
            self._lobby[user_id] = ws

    def lobby_disconnect(self, user_id: int) -> None:
        with self._lock:
            self._lobby.pop(user_id, None)
            self._queue = [q for q in self._queue if q["user_id"] != user_id]

    def queue_size(self) -> int:
        with self._lock:
            return len(self._queue)

    async def enqueue(self, user: dict, *, rule: str, base: int, inc: int) -> dict:
        with self._lock:
            self._queue = [q for q in self._queue if q["user_id"] != user["id"]]
            self._queue.append(
                {
                    "user_id": user["id"],
                    "name": user["name"],
                    "rating": user["rating"],
                    "rule": rule,
                    "base": base,
                    "inc": inc,
                    "at": time.time(),
                }
            )
        paired = await self._try_pair()
        return {"queued": True, "position": self.queue_size(), "paired": paired}

    def dequeue(self, user_id: int) -> bool:
        with self._lock:
            before = len(self._queue)
            self._queue = [q for q in self._queue if q["user_id"] != user_id]
            return len(self._queue) != before

    async def _try_pair(self) -> int:
        """扫描队列尝试配对。队列很短（个位数），O(n²) 完全够用。"""
        paired = 0
        with self._lock:
            pending = list(self._queue)

        used: set[int] = set()
        for i, a in enumerate(pending):
            if a["user_id"] in used:
                continue
            for b in pending[i + 1 :]:
                if b["user_id"] in used:
                    continue
                if a["rule"] != b["rule"]:
                    continue
                waited = max(0.0, time.time() - min(a["at"], b["at"]))
                window = min(MATCH_MAX, MATCH_BASE + MATCH_GROWTH * (waited / 5.0))
                if abs(a["rating"] - b["rating"]) > window:
                    continue
                ua = repo.user_by_id(a["user_id"])
                ub = repo.user_by_id(b["user_id"])
                if ua is None or ub is None:
                    continue
                from . import online

                match = online.create_match(
                    ua, ub, rule=a["rule"], base=a["base"], inc=a["inc"]
                )
                used.update({a["user_id"], b["user_id"]})
                await self._notify_matched(match, (a["user_id"], b["user_id"]))
                paired += 1
                break

        with self._lock:
            self._queue = [q for q in self._queue if q["user_id"] not in used]
        return paired

    async def _notify_matched(self, match: dict, users: tuple[int, int]) -> None:
        for user_id in users:
            with self._lock:
                ws = self._lobby.get(user_id)
            if ws is None:
                continue
            seat = BLACK if match["players"][BLACK]["id"] == user_id else WHITE
            try:
                await ws.send_json(
                    {
                        "type": "matched",
                        "game_id": match["game_id"],
                        "seat": seat,
                        "players": {
                            "black": {k: v for k, v in match["players"][BLACK].items() if k != "id"},
                            "white": {k: v for k, v in match["players"][WHITE].items() if k != "id"},
                        },
                    }
                )
            except Exception:
                log.warning("匹配通知发送失败 user=%s", user_id, exc_info=True)


hub = Hub()


# --------------------------------------------------------------------- 出牌


def view_for(game, user_id: int) -> dict:
    """一个客户端看到的一局。seat 是「你执什么」——同一份快照对不同人不同。"""
    snap = game.snapshot()
    snap["seat"] = game.player_of(user_id)
    players = {}
    for seat, uid in ((BLACK, game.black_id), (WHITE, game.white_id)):
        user = repo.user_by_id(uid) if uid else None
        players["black" if seat == BLACK else "white"] = {
            "name": user["name"] if user else "—",
            "rating": user["rating"] if user else None,
            "online": uid in hub.presence(game.id) if uid else False,
        }
    snap["players"] = players
    return snap


def apply_move(game, user: dict, x: int, y: int) -> dict:
    """校验并落子。返回广播用的事件体。

    所有拒绝路径都抛 MoveRejected，由调用方回给发起者。用异常而不是返回
    (ok, msg) 是因为这里的分支全是错误分支，正常路径只有一条。
    """
    seat = game.player_of(user["id"])
    if seat is None:
        raise MoveRejected("无权访问该对局")
    if game.status != "playing":
        raise MoveRejected("对局已结束")
    if seat != game.turn:
        raise MoveRejected("未到你的回合")
    if not (0 <= x < SIZE and 0 <= y < SIZE):
        raise MoveRejected("坐标越界")

    idx = y * SIZE + x
    if game.pos.board[idx] != 0:
        raise MoveRejected("该点已有棋子")

    ban = game.pos.forbidden(idx, seat)
    if ban:
        # 联网对局不做「判负」，直接拒绝并说明。规则里禁手判负是赛制，
        # 但这是休闲平台，让一个没学过连珠的人第 16 手突然输掉不合理。
        raise MoveRejected(f"{rules.BAN_LABELS[ban]}：该点不可落子")

    game.apply(idx, seat)
    return {"type": "state", "move": {"x": x, "y": y, "color": seat}, "reason": "move"}


def finish_and_broadcast(game) -> dict | None:
    """终局结算 + 返回要广播的事件。"""
    rating = settlement.settle(game)
    payload = {
        "type": "finished",
        "game": game.snapshot(),
        "winner": game.winner,
        "reason": game.result_reason,
        "rating": rating,
    }
    return payload


# --------------------------------------------------------------------- 超时巡检


def _tick_once() -> int:
    """扫一遍所有在下的联网局，把超时的判掉。返回处理了几个。"""
    killed = 0
    for game in list(store._games.values()):
        if game.mode != "online" or not game.timed:
            continue
        if game.status != "playing":
            continue
        if not game.lock.acquire(blocking=False):
            continue
        try:
            loser = game.flagged()
            if loser is None:
                continue
            game.status = "finished"
            game.winner = OPPONENT[loser]
            game.result_reason = "超时判负"
            game.turn_started = None
            payload = finish_and_broadcast(game)
            if payload:
                hub.broadcast_threadsafe(game.id, payload)
            killed += 1
            log.info("超时判负 game=%s loser=%s", game.id, loser)
        finally:
            game.lock.release()
    return killed


_ticker: threading.Thread | None = None


def start_ticker() -> None:
    """守护线程，1 秒一次。

    用线程而不是 asyncio 任务：判罚逻辑要拿 game.lock（同步锁），
    在事件循环里阻塞等锁会把整个服务卡住。
    """
    global _ticker
    if _ticker is not None and _ticker.is_alive():
        return

    def loop() -> None:
        while True:
            try:
                _tick_once()
            except Exception:
                log.warning("超时巡检异常", exc_info=True)
            time.sleep(1.0)

    _ticker = threading.Thread(target=loop, name="gomoku-clock", daemon=True)
    _ticker.start()


def stats() -> dict:
    return {
        "online_users": hub.online_users(),
        "live_games": hub.online_games(),
        "queue": hub.queue_size(),
        "clocks": _ticker.is_alive() if _ticker else False,
    }
