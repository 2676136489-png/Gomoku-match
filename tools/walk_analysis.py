"""验证分析功能：胜率、落点预测、数据面板、单步讲解。

    python tools/walk_analysis.py

真浏览器（CDP）走一遍用户路径，不直接调接口——接口测试已经覆盖了后端，
这里要证明的是「界面上真的能看见、能点、点了有反应」。
棋盘画在 canvas 上，查不到 DOM 节点，所以断言分两类：
能从 DOM 读的就读 DOM，读不到的读 Play.jsx 挂在 window 上的快照。
"""

from __future__ import annotations

import json
import random
import string
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp import Page, ensure_browser  # noqa: E402
from walk_online import ensure_server  # noqa: E402

BASE = "http://127.0.0.1:8000"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "walk"
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""), flush=True)


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def click_board(p, x: float, y: float) -> None:
    """棋盘是 canvas，只能按坐标派发鼠标事件。几何参数和 Board.jsx 保持一致。"""
    p.eval(
        f"""
      (() => {{
        const c = document.querySelector('.board-canvas');
        if (!c) return false;
        const r = c.getBoundingClientRect();
        const pad = r.width * 0.055;
        const step = (r.width - pad * 2) / 14;
        const opts = {{clientX: r.left + pad + {x} * step, clientY: r.top + pad + {y} * step, bubbles: true}};
        c.dispatchEvent(new MouseEvent('click', opts));
        return true;
      }})()
    """
    )


def main() -> int:
    srv = ensure_server()
    ensure_browser()
    name = "walk" + "".join(random.choices(string.ascii_lowercase, k=6))
    token = post("/api/auth/register", {"name": name, "password": "Walk12345"})["token"]

    p = Page.open(BASE + "/")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1180, deviceScaleFactor=1, mobile=False)
    time.sleep(1.8)
    p.eval(f"localStorage.setItem('gomoku.token', {json.dumps(token)})")
    p.close()

    p = Page.open(BASE + "/#/play")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1180, deviceScaleFactor=1, mobile=False)
    time.sleep(3.5)

    check("进入对弈页", p.eval('!!document.querySelector(".board-canvas")'))

    # --- 开局：执白 + 讲解模式，这样 Agent 先手，一开局就有可讲的一手 ---
    p.eval("document.querySelector('[data-color=\"2\"]').click()")
    p.eval("document.querySelector('[data-setting=\"step\"]').click()")
    time.sleep(0.4)
    p.eval("document.querySelector('.actions .btn-primary').click()")
    time.sleep(5.0)

    state = p.eval("window.__gomoku ? {step: window.__gomoku.step, moves: window.__gomoku.moves.length, pending: !!window.__gomoku.pending} : null")
    check("开局进入讲解模式", bool(state) and state["step"] is True, str(state))
    check("开局那一手已经落地", bool(state) and state["moves"] == 1, str(state))
    check("开局不卡在待确认", bool(state) and state["pending"] is False, str(state))

    ins = p.eval("window.__gomokuInsight")
    check("分析数据到位", bool(ins) and "winrate" in ins, str(ins and list(ins)))
    if not ins:
        print("分析数据没取到，后面的断言没有意义")
        return 1

    check("分析接口报了耗时", 0 < ins["elapsed_ms"] < 3000, f"{ins['elapsed_ms']}ms")
    check("胜率是概率", 0 <= ins["winrate"]["human"] <= 1, str(ins["winrate"]["human"]))
    # 你执白，轮到你了，所以「下一步」指的是黑棋（Agent）会走哪
    check("预测的是对手而不是自己", ins["prediction"]["side"] == 1, str(ins["prediction"]["side"]))

    # --- 面板 ---
    tabs = p.eval("document.querySelectorAll('.an-tab').length")
    labels = p.eval("[...document.querySelectorAll('.an-tab')].map(b => b.textContent).join('/')")
    check("分析面板三个页签", tabs == 3, labels)

    hero = p.eval("(() => { const el = document.querySelector('.rate-hero b'); return el ? el.textContent : ''; })()")
    expect = f"{ins['winrate']['human'] * 100:.1f}"
    check("面板胜率与接口一致", expect in hero.replace('%', ''), f"面板 {hero} / 接口 {expect}")

    facts = p.eval("document.querySelectorAll('.facts li').length")
    check("可数项三条齐全", facts == 3, f"{facts} 条")

    p.eval("[...document.querySelectorAll('.an-tab')].find(b => b.textContent === '预测').click()")
    time.sleep(0.6)
    rows = p.eval("document.querySelectorAll('.pred-row').length")
    check("预测页签列出候选点", rows >= 3, f"{rows} 行")
    top = p.eval("(() => { const el = document.querySelector('.pred-row .pred-coord'); return el ? el.textContent.trim() : ''; })()")
    first = ins["prediction"]["points"][0]
    check("第一行就是份额最高的点", top == f"{first['x']},{first['y']}", f"{top} vs {first['x']},{first['y']}")

    p.eval("[...document.querySelectorAll('.an-tab')].find(b => b.textContent === '数据').click()")
    time.sleep(0.6)
    shapes = p.eval("document.querySelectorAll('.shape-row').length")
    check("棋形分布六档", shapes == 6, f"{shapes} 档")
    rel = p.eval("document.querySelectorAll('.rel-row').length")
    check("可靠性表十档", rel == 10, f"{rel} 行")

    chart = p.eval("!!document.querySelector('.wr-svg .chart-line')")
    check("胜率曲线画出来了", chart)
    check("曲线点数等于手数", len(ins["series"]) == 1, str(len(ins["series"])))

    # 截图前切回胜率页签：这是默认视图，也是用户第一眼看到的样子
    p.eval("[...document.querySelectorAll('.an-tab')].find(b => b.textContent === '胜率').click()")
    time.sleep(0.6)
    p.shot(str(SHOTS / "13-analysis.png"))
    print(f"  截图 {SHOTS / '13-analysis.png'}")

    # --- 落子：Agent 只算不落 ---
    click_board(p, 8, 8)
    time.sleep(4.0)

    state = p.eval("window.__gomoku ? {moves: window.__gomoku.moves.length, pending: window.__gomoku.pending} : null")
    check("人类落子后 Agent 停住", bool(state) and state["pending"] is not None, str(state))
    check("待确认的一手没上盘", bool(state) and state["moves"] == 2, str(state))

    head = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
    check("解说卡切成「它打算这么走」", "它打算这么走" in head, head)

    resume = p.eval("(() => { const b = document.querySelector('.ex-resume'); return b ? b.textContent : ''; })()")
    check("有确认落子的按钮", "继续" in resume, resume)

    narrative = p.eval("(() => { const el = document.querySelector('.ex-narrative'); return el ? el.textContent : ''; })()")
    check("待确认的一手有解说文字", len(narrative) > 20, narrative[:60])

    status = p.eval("(() => { const el = document.querySelector('.pill-wait'); return el ? el.textContent : ''; })()")
    check("状态栏提示等你看完", "等你看完" in status, status)

    p.shot(str(SHOTS / "14-step-pending.png"))
    print(f"  截图 {SHOTS / '14-step-pending.png'}")

    p.eval("document.querySelector('.ex-resume').click()")
    time.sleep(3.0)
    state = p.eval("window.__gomoku ? {moves: window.__gomoku.moves.length, pending: window.__gomoku.pending} : null")
    check("确认之后才落子", bool(state) and state["moves"] == 3, str(state))
    check("确认后清空待确认", bool(state) and state["pending"] is None, str(state))

    # --- 回看任意一手：棋谱是新的在上，所以第一手在最后一行 ---
    p.eval("[...document.querySelectorAll('.feed-row')].pop().click()")
    time.sleep(1.5)
    head = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
    check("点棋谱最后一行能回看第一手", "第 1 手" in head, head)
    verdict = p.eval("(() => { const el = document.querySelector('.ex-verdict'); return el ? el.textContent : ''; })()")
    check("解说给出档位", verdict in ("胜着", "好手", "过渡", "失着", "禁手"), verdict)
    active = p.eval("document.querySelectorAll('.feed-row.is-active').length")
    check("当前解说的那一手高亮", active == 1, f"{active} 行高亮")

    # 曲线上的转折点按钮（手数太少时可能没有，有就必须跳到对应手）
    jump = p.eval("(() => { const b = document.querySelector('.wr-jump'); return b ? b.textContent : ''; })()")
    if jump:
        want = jump.replace(" ", "")
        p.eval("document.querySelector('.wr-jump').click()")
        time.sleep(1.5)
        head2 = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
        check("点转折点跳到它说的那一手", head2.replace(" ", "") in want, f"按钮「{jump}」-> 面板「{head2}」")
    else:
        print("  （手数太少，没有转折点可跳）")

    p.shot(str(SHOTS / "15-explain-history.png"))
    print(f"  截图 {SHOTS / '15-explain-history.png'}")

    # --- 落点预测开关 ---
    p.eval("document.querySelector('[data-tool=\"predict\"]').click()")
    time.sleep(0.6)
    legend = p.eval("document.querySelectorAll('[data-legend=\"predict\"]').length")
    check("关掉预测后图例消失", legend == 0, f"{legend} 个图例")
    p.eval("document.querySelector('[data-tool=\"predict\"]').click()")
    time.sleep(0.6)
    legend = p.eval("document.querySelectorAll('[data-legend=\"predict\"] b').length")
    check("打开预测后图例回来", legend >= 3, f"{legend} 个点")

    p.close()

    if srv:
        srv.terminate()

    bad = [r for r in results if not r[1]]
    print()
    print(f"通过 {len(results) - len(bad)} 项，失败 {len(bad)} 项")
    for b in bad:
        print(f"  FAIL {b[0]} {b[2]}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
