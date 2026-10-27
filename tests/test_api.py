"""HTTP 端到端测试。需要服务已在 127.0.0.1:8000 运行。

    python serve.py &
    python tests/test_api.py

覆盖注册登录鉴权、对局与战绩记账、学习模式教练、实验室 SSE、竞技场 SSE。
SSE 用真实的事件流解析，不绕开传输层直接调函数。
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("GOMOKU_TEST_BASE", "http://127.0.0.1:8000")
PASS = 0
FAIL = 0


def new_client(**kwargs) -> httpx.Client:
    """显式关掉环境变量里的代理。

    开发机上经常挂着 HTTP_PROXY，httpx 默认 trust_env=True 会把 127.0.0.1
    的请求也丢给代理，于是拿到一个不是本服务返回的 404。这个坑不该让每个
    跑测试的人重新踩一遍。
    """
    return httpx.Client(base_url=BASE, trust_env=False, **kwargs)


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def read_sse(client: httpx.Client, path: str, payload: dict, timeout: float = 120.0) -> list[dict]:
    events: list[dict] = []
    buffer = ""
    with client.stream("POST", path, json=payload, timeout=timeout) as res:
        if res.status_code != 200:
            res.read()
            raise RuntimeError(f"{res.status_code}: {res.text[:200]}")
        for chunk in res.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                for line in frame.split("\n"):
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
    return events


def main() -> None:
    name = f"tester{int(time.time()) % 100000}"
    password = "gomoku-2026"

    public = new_client(timeout=30)

    # --- 鉴权 ---
    r = public.post("/api/games", json={})
    check("未登录开新局被拒", r.status_code == 401, str(r.status_code))

    r = public.post("/api/auth/register", json={"name": name, "password": password})
    check("注册成功", r.status_code == 200, r.text[:200])
    token = r.json()["token"]
    user = r.json()["user"]
    check("注册返回用户", user["name"] == name)

    r = public.post("/api/auth/register", json={"name": name, "password": password})
    check("重复用户名被拒", r.status_code == 409, str(r.status_code))

    r = public.post("/api/auth/register", json={"name": "a", "password": "123"})
    check("弱凭据被拒", r.status_code == 400, str(r.status_code))

    r = public.post("/api/auth/login", json={"name": name, "password": "wrong-pass"})
    check("错误密码被拒", r.status_code == 401, str(r.status_code))

    r = public.post("/api/auth/login", json={"name": name, "password": password})
    check("登录成功", r.status_code == 200)
    token = r.json()["token"]

    client = new_client(timeout=90, headers={"Authorization": f"Bearer {token}"})
    r = client.get("/api/auth/me")
    check("me 返回本人", r.json()["user"]["name"] == name)
    check("账户落盘", r.json()["persistent"] is True)

    # --- 元信息 ---
    cfg = client.get("/api/config").json()
    check("算法清单四项", len(cfg["algorithms"]) == 4, str([a["key"] for a in cfg["algorithms"]]))
    check("规则两种", len(cfg["rules"]) == 2)
    tut = client.get("/api/tutorial").json()
    check("教程五章", len(tut["chapters"]) == 5, str(len(tut["chapters"])))
    check("教程含示例棋盘", any(s.get("demo") for c in tut["chapters"] for s in c["sections"]))

    # --- 对局（AI 对弈） ---
    r = client.post(
        "/api/games",
        json={"mode": "agent", "difficulty": 2, "persona": "balanced", "human_color": 1, "rule": "renju"},
    )
    check("新局创建", r.status_code == 200, r.text[:200])
    game = r.json()["game"]
    gid = game["id"]

    r = client.post(f"/api/games/{gid}/move", json={"x": 7, "y": 7})
    body = r.json()
    check("人类落子后 Agent 应手", body["agent"] is not None)
    check("应手落点合法", body["game"]["board"][body["agent"]["y"] * 15 + body["agent"]["x"]] in (1, 2))
    print(f"       Agent 走 ({body['agent']['x']},{body['agent']['y']}) {body['agent']['think_ms']}ms")

    r = client.post(f"/api/games/{gid}/hint")
    check("提示接口可用", "x" in r.json())

    r = client.post(f"/api/games/{gid}/undo")
    check("悔棋可用", r.status_code == 200)

    # --- 学习模式 ---
    r = client.post(
        "/api/games",
        json={"mode": "learn", "difficulty": 2, "persona": "balanced", "human_color": 1, "rule": "renju"},
    )
    lgid = r.json()["game"]["id"]
    r = client.post(f"/api/games/{lgid}/move", json={"x": 7, "y": 7})
    body = r.json()
    check("学习模式返回教练点评", body.get("coach") is not None, str(body.keys()))
    check("教练有等级与说明", body["coach"]["grade"] and body["coach"]["detail"], str(body.get("coach")))
    check("学习模式返回威胁摘要", body.get("tactics") is not None)
    print(f"       教练：{body['coach']['title']} — {body['coach']['detail'][:46]}")

    # --- 代码实验室 ---
    samples = client.get("/api/lab/samples").json()
    check("实验室模板可用", "python" in samples and samples["python"]["code"])
    check("包含历史存档", "archive" in samples)

    r = client.post("/api/lab/verify", json={"language": "python", "source": samples["python"]["code"]})
    check("Python 模板校验通过", r.json().get("ok") is True, r.text[:200])

    r = client.post(
        "/api/lab/verify", json={"language": "python", "source": "import os\ndef choose_move(b,c,n): return (7,7)"}
    )
    check("越权代码被拦", r.json().get("ok") is False, r.text[:200])

    events = read_sse(
        client,
        "/api/lab/match",
        {
            "language": "python",
            "source": samples["python"]["code"],
            "opponent": "greedy",
            "difficulty": 2,
            "code_color": 1,
            "rule": "renju",
            "timeout": 2,
        },
    )
    moves = [e for e in events if e["type"] == "move"]
    last = events[-1] if events else {}
    check("实验室对局流出完整事件", last.get("type") == "end", str(last)[:200])
    check("实验室对局有落子", len(moves) >= 5, str(len(moves)))
    print(f"       实验室：{len(moves)} 手，{last.get('reason')}")

    # --- 竞技场 ---
    events = read_sse(
        client,
        "/api/arena/match",
        {
            "left": {"kind": "algorithm", "algorithm": "greedy", "difficulty": 1},
            "right": {"kind": "algorithm", "algorithm": "random", "difficulty": 1},
            "rule": "renju",
            "difficulty": 1,
        },
    )
    moves = [e for e in events if e["type"] == "move"]
    last = events[-1] if events else {}
    check("竞技场对局完成", last.get("type") == "end", str(last)[:200])
    check("竞技场有落子", len(moves) >= 5, str(len(moves)))
    print(f"       竞技场：贪心 vs 随机 {len(moves)} 手，胜者 {last.get('winner')}")

    # --- 分析：胜率 / 落点预测 / 棋形普查 / 讲解模式 ---
    r = client.post(
        "/api/games",
        json={
            "mode": "agent",
            "difficulty": 2,
            "persona": "balanced",
            "human_color": 2,
            "rule": "renju",
            "step": True,
        },
    )
    check("讲解模式开局成功", r.status_code == 200, r.text[:200])
    sgid = r.json()["game"]["id"]
    check("讲解模式开局那一手照常落地", len(r.json()["game"]["moves"]) == 1, str(len(r.json()["game"]["moves"])))
    check("开局那一手附带解说", r.json().get("explain") is not None, str(list(r.json())))

    r = client.get(f"/api/games/{sgid}/insight")
    ins = r.json()
    check("分析接口返回完整字段", {"winrate", "prediction", "census", "series", "model"} <= set(ins), str(list(ins)))
    check("双方胜率互补", abs(ins["winrate"]["human"] + ins["winrate"]["opponent"] - 1.0) < 1e-6,
          f"{ins['winrate']}")
    check("胜率带结论与理由", bool(ins["winrate"]["reason"]) and bool(ins["winrate"]["confidence"]))
    check("可数项带原始数", len(ins["winrate"]["facts"]) == 3, str(ins["winrate"]["facts"]))
    check("落点预测给出候选", len(ins["prediction"]["points"]) >= 3, str(len(ins["prediction"]["points"])))
    snap_board = client.get(f"/api/games/{sgid}").json()["game"]["board"]
    check("预测点都在盘内且为空", all(
        snap_board[p["y"] * 15 + p["x"]] == 0 and 0 <= p["x"] < 15 and 0 <= p["y"] < 15
        for p in ins["prediction"]["points"]
    ))
    check("预测的是 Agent 那一方", ins["prediction"]["side"] == 1, str(ins["prediction"]["side"]))
    check("预测份额递减", all(
        a["share"] >= b["share"] for a, b in zip(ins["prediction"]["points"], ins["prediction"]["points"][1:])
    ))
    check("普查有棋形分布", len(ins["census"]["shapes"]["black"]) == 6, str(ins["census"]["shapes"]["black"]))
    check("胜率曲线长度等于手数", len(ins["series"]) == len(client.get(f"/api/games/{sgid}").json()["game"]["moves"]),
          str(len(ins["series"])))
    check("接口报出自己的耗时", 0 < ins["elapsed_ms"] < 3000, f"{ins['elapsed_ms']}ms")
    check("模型来源可追溯", ins["model"]["source"] in ("selfplay", "default"), str(ins["model"]["source"]))
    check("模型自述带样本量", isinstance(ins["model"].get("samples"), int) or ins["model"]["source"] == "default",
          str(ins["model"].get("samples")))
    print(f"       分析：{ins['elapsed_ms']}ms，胜率 {ins['winrate']['human']:.1%}，"
          f"模型 AUC {ins['model'].get('auc', '-')}")

    # 人类落子 → Agent 只算不落。注意它开局占了天元，别往那儿下
    r = client.post(f"/api/games/{sgid}/move", json={"x": 8, "y": 8})
    body = r.json()
    check("讲解模式人类落子成功", r.status_code == 200, r.text[:200])
    check("Agent 停在待确认状态", body["agent"] is not None and body["agent"].get("pending") is True,
          str(body.get("agent")))
    check("待确认的一手还没上盘", len(body["game"]["moves"]) == 2, str(len(body["game"]["moves"])))
    check("人类那一手当场被解说", body.get("explain") is not None, str(list(body)))
    check("快照暴露待确认落点", body["game"]["pending"] is not None, str(body["game"]["pending"]))

    r = client.post(f"/api/games/{sgid}/move", json={"x": 8, "y": 8})
    check("待确认期间不允许抢着落子", r.status_code == 409, str(r.status_code))

    r = client.get(f"/api/games/{sgid}/pending")
    check("能取到待确认解说的坐标", r.json()["preview"]["x"] == body["game"]["pending"]["x"], r.text[:200])
    check("待确认解说带理由", bool(r.json()["preview"]["headline"]))

    r = client.post(f"/api/games/{sgid}/resume")
    check("确认之后这一手才落地", len(r.json()["game"]["moves"]) == 3, str(len(r.json()["game"]["moves"])))
    check("确认后清空待确认状态", r.json()["game"]["pending"] is None)
    check("确认的一手也有解说", r.json().get("explain") is not None)

    r = client.get(f"/api/games/{sgid}/pending")
    check("没有待确认时返回 404", r.status_code == 404, str(r.status_code))
    r = client.post(f"/api/games/{sgid}/resume")
    check("没有待确认时确认被拒", r.status_code == 409, str(r.status_code))

    r = client.post(f"/api/games/{sgid}/explain", json={"index": 0})
    check("能回看第一手的解说", r.json()["explain"]["index"] == 0, r.text[:200])
    r = client.post(f"/api/games/{sgid}/explain", json={})
    check("不给索引时解释最后一手", r.json()["explain"]["index"] == 2, r.text[:200])
    r = client.post(f"/api/games/{sgid}/explain", json={"index": 99})
    check("解说索引越界被拒", r.status_code == 400, str(r.status_code))

    r = client.post(f"/api/games/{sgid}/undo")
    check("讲解模式悔棋清空待确认", r.json()["game"]["pending"] is None, str(r.json()["game"]["pending"]))

    # --- 战绩 ---
    r = client.get("/api/leaderboard")
    check("排行榜可读", "rows" in r.json())

    r = client.post(
        "/api/games",
        json={"mode": "engine", "difficulty": 1, "persona": "balanced", "human_color": 2, "rule": "renju"},
    )
    quick = r.json()["game"]["id"]
    # 执白，Agent 已先手；随便下到终局太慢，这里只确认快照字段齐全
    snap = client.get(f"/api/games/{quick}").json()["game"]
    check("快照含规则与禁手字段", "rule_label" in snap and "banned" in snap)

    # --- 改密码 ---
    r = client.post("/api/auth/password", json={"old": password, "new": "new-pass-2026"})
    check("改密码成功", r.status_code == 200, r.text[:200])
    new_token = r.json()["token"]
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    check("新 token 可用", r.status_code == 200)
    r = client.get("/api/auth/me")
    check("旧 token 失效", r.status_code == 401, str(r.status_code))

    client.post("/api/auth/logout")
    r = client.get("/api/auth/me")
    check("注销后失效", r.status_code == 401, str(r.status_code))

    # --- 限流 ---
    # 放在最后：限流状态在服务端进程里，前面跑它就轮到别人吃 429 了。
    # 用改密码这条规则（按用户 20 次/分钟），专门开一个新号，不影响别的用例。
    stamp = int(time.time()) % 100000
    with new_client() as c:
        lucky = c.post("/api/auth/register", json={"name": f"limit{stamp}", "password": "gomoku-2026"}).json()
    lm = new_client(headers={"Authorization": f"Bearer {lucky['token']}"})
    codes = [
        lm.post("/api/auth/password", json={"old": "wrong-pass", "new": "another-2026"}).status_code
        for _ in range(21)
    ]
    check("超限前是业务错误而不是限流", codes[0] in (400, 401), str(codes[0]))
    check("第 21 次被限流", codes[-1] == 429, f"{codes[-3:]}")
    lm.close()

    r = new_client().get("/api/metrics")
    body = r.text if r.status_code == 200 else ""
    check("限流计数器带规则分桶",
          'gomoku_ratelimit_blocked_total{bucket="/api/auth/password"}' in body,
          body[:120])
    # 有路径参数的路由必须按模板打标签，否则每开一局就多一条时间序列
    check("请求计数器按路由模板分桶", 'path="/api/games/{game_id}"' in body, body[:200])

    print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
