"""在线对战端到端测试。需要服务已在 127.0.0.1:8000 运行，且装了 websockets。

    pip install websockets
    python serve.py &
    python tests/test_online.py

覆盖房间码开桌、双向实时落子、轮次与禁手拒绝、认输结算、Elo 变动、
匹配队列撮合、断线重连拿到全量快照、棋谱落库可回放。
"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings

import httpx

warnings.filterwarnings("ignore", category=DeprecationWarning)

BASE = os.environ.get("GOMOKU_TEST_BASE", "http://127.0.0.1:8000")
WS = os.environ.get("GOMOKU_TEST_WS", BASE.replace("http://", "ws://"))
PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def client(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=BASE, timeout=30, trust_env=False, headers=headers)


def register(name: str) -> dict:
    with client() as c:
        res = c.post("/api/auth/register", json={"name": name, "password": "gomoku-2026"})
        res.raise_for_status()
        return res.json()


class Peer:
    """一条对局连接。所有断言都基于它收到的消息，不直接读服务端内存。"""

    def __init__(self, game_id: str, token: str) -> None:
        from websockets.sync.client import connect

        self.ws = connect(f"{WS}/ws/game/{game_id}?token={token}", open_timeout=10)
        self.queue: list[dict] = []

    def send(self, payload: dict) -> None:
        self.ws.send(json.dumps(payload))

    def wait(self, kind: str, timeout: float = 8.0) -> dict | None:
        deadline = time.time() + timeout
        for msg in self.queue:
            if msg.get("type") == kind:
                self.queue.remove(msg)
                return msg
        while time.time() < deadline:
            try:
                raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            except TimeoutError:
                break
            msg = json.loads(raw)
            if msg.get("type") == kind:
                return msg
            self.queue.append(msg)
        return None

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def wait_until(fn, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.1)
    return False


def main() -> None:
    stamp = int(time.time()) % 100000
    a = register(f"onlineA{stamp}")
    b = register(f"onlineB{stamp}")
    ta, tb = a["token"], b["token"]

    # ---------------------------------------------------------------- 房间
    with client(ta) as c:
        res = c.post("/api/rooms", json={"rule": "renju", "clock_base": 300, "clock_inc": 3, "rated": True})
        check("建房成功", res.status_code == 200, res.text[:160])
        room = res.json()["room"]
    code = room["code"]
    check("房间码是 6 位", len(code) == 6, code)

    with client(tb) as c:
        res = c.post(f"/api/rooms/{code}/join")
        check("加入房间成功", res.status_code == 200, res.text[:160])
        joined = res.json()
    game_id = joined["game_id"]
    seat_b = joined["seat"]
    seat_a = 3 - seat_b
    check("双方座位互斥", {seat_a, seat_b} == {1, 2}, f"{seat_a}/{seat_b}")

    with client(ta) as c:
        res = c.post(f"/api/rooms/{code}/join")
        check("房主不能加入自己的房间", res.status_code == 403, str(res.status_code))

    with client(ta) as c:
        res = c.get(f"/api/rooms/{code}")
        check("房间状态变为进行中", res.json()["started"] is True)

    # ---------------------------------------------------------------- 连接
    pa = Peer(game_id, ta)
    pb = Peer(game_id, tb)

    hello_a = pa.wait("hello")
    hello_b = pb.wait("hello")
    check("A 收到初始快照", hello_a is not None and hello_a["game"]["id"] == game_id)
    check("座位与服务端一致", hello_a["you"] == seat_a and hello_b["you"] == seat_b,
          f"{hello_a['you']}/{hello_b['you']}")
    check("快照带双方玩家名", set(hello_a["game"]["players"]) == {"black", "white"})
    check("快照带计时", hello_a["game"]["clock"] is not None)
    check("开局棋盘为空", hello_a["game"]["occupied"] == 0)

    black, white = (pa, pb) if seat_a == 1 else (pb, pa)
    black_seat = 1

    # ---------------------------------------------------------------- 规则
    white.send({"type": "move", "x": 7, "y": 7})
    err = white.wait("error")
    check("白棋抢先落子被拒", err is not None and "回合" in err["message"], str(err))

    black.send({"type": "move", "x": 99, "y": 0})
    err = black.wait("error")
    check("越界坐标被拒", err is not None and "越界" in err["message"], str(err))

    black.send({"type": "move", "x": 7, "y": 7})
    st_black = black.wait("state")
    st_white = white.wait("state")
    check("黑棋落子广播到双方", st_black is not None and st_white is not None)
    check("棋盘已更新", st_white["game"]["board"][7 * 15 + 7] == 1)
    check("轮到白棋", st_white["game"]["turn"] == 2)
    clk = st_white["game"]["clock"]
    check("计时按读秒结算", clk["base"] == 300 and clk["inc"] == 3)
    check("落子后手上加秒", clk["black"] >= 300, str(clk))

    black.send({"type": "move", "x": 6, "y": 6})
    err = black.wait("error")
    check("连下两手被拒", err is not None and "回合" in err["message"], str(err))

    white.send({"type": "move", "x": 7, "y": 8})
    check("白棋落子成功", white.wait("state") is not None)

    # ---------------------------------------------------------------- 聊天
    white.send({"type": "chat", "text": "承让"})
    chat = black.wait("chat")
    check("聊天送达对手", chat is not None and chat["text"] == "承让", str(chat))

    # ---------------------------------------------------------------- 非法占位
    black.send({"type": "move", "x": 7, "y": 8})
    err = black.wait("error")
    check("已有棋子的点被拒", err is not None and "已有棋子" in err["message"], str(err))

    # ---------------------------------------------------------------- 认输结算
    black.send({"type": "resign"})
    fin = black.wait("finished")
    check("认输广播终局", fin is not None and fin["winner"] == 2, str(fin))
    check("终局原因是认输", fin is not None and fin["reason"] == "认输")
    check("Elo 已结算", fin is not None and fin.get("rating", {}).get("rated") is True, str(fin))

    pa.close()
    pb.close()

    # ---------------------------------------------------------------- 战绩
    with client(ta) as c:
        me_a = c.get("/api/auth/me").json()["user"]
    with client(tb) as c:
        me_b = c.get("/api/auth/me").json()["user"]
    # 认输的是执黑那方，而黑白是随机分的。断言必须跟着实际座位走，
    # 不能假设 A 一定是黑——那样这个测试有一半时间在测错误的分支。
    loser, winner = (me_a, me_b) if seat_a == 1 else (me_b, me_a)
    check("认输方记一负", loser["stats"]["losses"] == 1, str(loser["stats"]))
    check("对手记一胜", winner["stats"]["wins"] == 1, str(winner["stats"]))
    check("认输方掉分", loser["stats"]["rating"] < 1200, str(loser["stats"]["rating"]))
    check("对手涨分", winner["stats"]["rating"] > 1200, str(winner["stats"]["rating"]))
    check("涨跌互抵", abs(loser["stats"]["rating"] + winner["stats"]["rating"] - 2400) < 0.2,
          f"{loser['stats']['rating']}/{winner['stats']['rating']}")
    check("段位字段存在", winner["stats"]["tier"]["label"] != "")

    # ---------------------------------------------------------------- 棋谱
    with client(ta) as c:
        hist = c.get("/api/history").json()["rows"]
    mine = [row for row in hist if row["id"] == game_id]
    check("棋谱进了历史", len(mine) == 1, str(hist[:2]))
    if mine:
        check("棋谱手数只算合法落子", mine[0]["moves"] == 2, str(mine[0]["moves"]))
        check("棋谱标了 rated", mine[0]["rated"] is True)
        check("棋谱记录了分数变化", mine[0]["rating_delta"] is not None)

    with client(ta) as c:
        replay = c.get(f"/api/games/{game_id}").json()["game"]
    check("落库后能重放整局", len(replay["moves"]) == 2 and replay["status"] == "finished",
          str(len(replay["moves"])))

    # ---------------------------------------------------------------- 越权
    with client() as c:
        res = c.get(f"/api/games/{game_id}")
        check("未登录不能读棋谱", res.status_code == 401, str(res.status_code))

    other = register(f"onlineC{stamp}")
    with client(other["token"]) as c:
        res = c.get(f"/api/games/{game_id}")
        check("旁观者不能读别人的棋谱", res.status_code == 403, str(res.status_code))

    # ---------------------------------------------------------------- 匹配
    from websockets.sync.client import connect

    la = connect(f"{WS}/ws/lobby?token={ta}", open_timeout=10)
    lb = connect(f"{WS}/ws/lobby?token={tb}", open_timeout=10)
    check("大厅连接就绪", json.loads(la.recv(timeout=5))["type"] == "ready")
    check("大厅连接就绪(B)", json.loads(lb.recv(timeout=5))["type"] == "ready")

    la.send(json.dumps({"type": "queue", "clock_base": 180, "clock_inc": 2}))
    check("排队回执", json.loads(la.recv(timeout=5))["type"] == "queued")
    lb.send(json.dumps({"type": "queue", "clock_base": 180, "clock_inc": 2}))

    matched_a = matched_b = None
    deadline = time.time() + 8
    while time.time() < deadline and not (matched_a and matched_b):
        for sock, tag in ((la, "a"), (lb, "b")):
            try:
                msg = json.loads(sock.recv(timeout=0.5))
            except TimeoutError:
                continue
            if msg.get("type") == "matched":
                if tag == "a":
                    matched_a = msg
                else:
                    matched_b = msg
    check("双边都收到匹配", matched_a is not None and matched_b is not None)
    if matched_a and matched_b:
        check("匹配进的是同一局", matched_a["game_id"] == matched_b["game_id"])
        check("匹配座位互斥", {matched_a["seat"], matched_b["seat"]} == {1, 2})
        check("匹配局是计分对局", matched_a["players"]["black"]["rating"] is not None)
    la.close()
    lb.close()

    # ---------------------------------------------------------------- 天梯
    with client() as c:
        board = c.get("/api/leaderboard?limit=50").json()
    check("天梯按分数降序", all(
        board["rows"][i]["rating"] >= board["rows"][i + 1]["rating"] for i in range(len(board["rows"]) - 1)
    ))
    check("天梯带段位", all(row.get("tier") for row in board["rows"]), str(board["rows"][:1]))

    # 榜单只取前 N，测试库里积累的账户会把它挤出去。查排名要走公开主页，
    # 那边按全量排序算 rank，不受 limit 影响。
    with client() as c:
        prof_a = c.get(f"/api/profile/onlineA{stamp}").json()
        prof_b = c.get(f"/api/profile/onlineB{stamp}").json()
    check("公开主页可读", prof_a["user"]["name"] == f"onlineA{stamp}")
    check("主页带排名", isinstance(prof_a["user"]["rank"], int), str(prof_a["user"]["rank"]))
    rank_a, rank_b = prof_a["user"]["rank"], prof_b["user"]["rank"]
    rate_a, rate_b = prof_a["user"]["stats"]["rating"], prof_b["user"]["stats"]["rating"]
    check("排名方向与分数一致", (rank_a < rank_b) == (rate_a > rate_b),
          f"{rank_a}/{rank_b} vs {rate_a}/{rate_b}")
    winner_rank, loser_rank = (rank_a, rank_b) if rate_a > rate_b else (rank_b, rank_a)
    check("胜方排名高于败方", winner_rank < loser_rank, f"{winner_rank}/{loser_rank}")

    with client(ta) as c:
        stats = c.get("/api/online/stats")
    check("在线统计可读", stats.status_code == 200 and "online_users" in stats.json(),
          f"{stats.status_code} {stats.text[:120]}")

    print("\n" + "=" * 46)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
