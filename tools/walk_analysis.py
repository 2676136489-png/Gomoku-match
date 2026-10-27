"""验证「开局准备 → 对局 → 分析 → 复盘」这条新路径。

    python tools/walk_analysis.py

真浏览器（CDP）走一遍用户路径，不直接调接口——接口测试已经覆盖了后端，
这里要证明的是「界面上真的能看见、能点、点了有反应」。

改版后一屏只放一件事：落子在棋盘页，分析与棋谱各自待在右侧页签里。
所以断言分成三段：准备页的选项、对局页的页签切换、复盘页的分析面板。
棋盘画在 canvas 上，查不到 DOM 节点，棋盘状态读 Play.jsx 挂在 window 上的快照。
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


def tab(p, key: str) -> None:
    p.eval(f"document.querySelector('[data-tab=\"{key}\"]').click()")
    time.sleep(0.8)


def main() -> int:
    srv = ensure_server(SHOTS.parent.parent / ".tmp" / "walk-analysis.db")
    ensure_browser()
    name = "walk" + "".join(random.choices(string.ascii_lowercase, k=6))
    token = post("/api/auth/register", {"name": name, "password": "Walk12345"})["token"]

    p = Page.open(BASE + "/")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(1.8)
    p.eval(f"localStorage.setItem('gomoku.token', {json.dumps(token)})")
    # 引导层是给第一次来的人看的，走查要测的是它之下的内容
    p.eval("Object.keys(localStorage).forEach(k => { if (k.startsWith('gomoku.tip.')) localStorage.setItem(k, '1') })")
    p.close()

    p = Page.open(BASE + "/#/play")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1080, deviceScaleFactor=1, mobile=False)
    time.sleep(3.0)

    # --- 第一段：开局准备页 ---
    check("进入开局准备页", p.eval('!!document.querySelector(".setup")'))
    check("对局页不在这里", p.eval('!document.querySelector(".board-canvas")'))
    cards = p.eval("document.querySelectorAll('.setup-card').length")
    check("四种对手可选", cards == 4, f"{cards} 张")
    check("准备页说明了为什么、下一步", p.eval('!!document.querySelector(".setup-step-head p")'))

    # 整张表单比一屏高，提交条吸底才够得着。这条断言最初是红的：
    # 按钮在 y≈1178，比 1080 的视口还低 100px，第一屏根本看不到怎么开始。
    cta = p.eval("""
      (() => {
        const b = document.querySelector('.setup-foot .btn-primary');
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return {bottom: Math.round(r.bottom), vh: innerHeight,
                sticky: getComputedStyle(document.querySelector('.setup-foot')).position};
      })()
    """)
    check("提交按钮不用滚动就够得着", bool(cta) and cta["bottom"] <= cta["vh"], str(cta))
    check("提交条是吸底的", bool(cta) and cta["sticky"] == "sticky", str(cta and cta["sticky"]))

    # 视口截图，不用整页捕获：captureBeyondViewport 会把吸底提交条按扩展后的视口
    # 重新定位，拍出来停在页面中间，像是压在字段上。
    p.shot(str(SHOTS / "05b-setup.png"))
    print(f"  截图 {SHOTS / '05b-setup.png'}")

    # 执白 + 逐步确认，这样 Agent 先手，一开局就有可讲的一手
    p.eval("document.querySelector('[data-color=\"2\"]').click()")
    p.eval("document.querySelector('.setup-more').open = true")
    time.sleep(0.3)
    p.eval("document.querySelector('[data-setting=\"step\"]').click()")
    time.sleep(0.4)
    summary = p.eval("(() => { const el = document.querySelector('.setup-summary'); return el ? el.textContent : ''; })()")
    check("摘要跟随选择更新", "执白" in summary, summary)

    p.eval("document.querySelector('.setup-foot .btn-primary').click()")
    time.sleep(5.5)

    # --- 第二段：对局页 ---
    check("点开始对局进到棋盘", "/play/" in (p.eval("location.hash") or ""), p.eval("location.hash"))
    check("棋盘渲染出来了", p.eval('!!document.querySelector(".board-canvas")'))

    active = p.eval("(() => { const el = document.querySelector('.info-tab.is-active'); return el ? el.textContent : ''; })()")
    tabs = p.eval("document.querySelectorAll('.info-tab').length")
    check("右侧只有三个页签", tabs == 3, f"{tabs} 个")
    check("默认停在解说", active == "解说", active)
    check("对局页没有设置面板", p.eval('!document.querySelector(".seg-group")'))
    actions = p.eval("document.querySelectorAll('.play-actions .btn').length")
    check("顶部操作区只有四项", actions == 4, f"{actions} 个")

    state = p.eval("window.__gomoku ? {step: window.__gomoku.step, moves: window.__gomoku.moves.length, pending: !!window.__gomoku.pending} : null")
    check("开局进入逐步确认", bool(state) and state["step"] is True, str(state))
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

    # --- 第三段：分析页签 ---
    tab(p, "analysis")

    tabs_an = p.eval("document.querySelectorAll('.an-tab').length")
    labels = p.eval("[...document.querySelectorAll('.an-tab')].map(b => b.textContent).join('/')")
    check("分析面板三个页签", tabs_an == 3, labels)

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
    time.sleep(4.5)

    state = p.eval("window.__gomoku ? {moves: window.__gomoku.moves.length, pending: window.__gomoku.pending} : null")
    check("人类落子后 Agent 停住", bool(state) and state["pending"] is not None, str(state))
    check("待确认的一手没上盘", bool(state) and state["moves"] == 2, str(state))

    tab(p, "note")
    head = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
    check("解说卡切到待确认态", "预计算落子" in head, head)

    resume = p.eval("(() => { const b = document.querySelector('.ex-resume'); return b ? b.textContent : ''; })()")
    check("有确认落子的按钮", "确认" in resume, resume)

    narrative = p.eval("(() => { const el = document.querySelector('.ex-narrative'); return el ? el.textContent : ''; })()")
    check("待确认的一手有解说文字", len(narrative) > 20, narrative[:60])

    status = p.eval("(() => { const el = document.querySelector('.pill-wait'); return el ? el.textContent : ''; })()")
    check("状态栏提示等待确认", "等待确认" in status, status)

    p.shot(str(SHOTS / "14-step-pending.png"))
    print(f"  截图 {SHOTS / '14-step-pending.png'}")

    p.eval("document.querySelector('.ex-resume').click()")
    time.sleep(3.0)
    state = p.eval("window.__gomoku ? {moves: window.__gomoku.moves.length, pending: window.__gomoku.pending} : null")
    check("确认之后才落子", bool(state) and state["moves"] == 3, str(state))
    check("确认后清空待确认", bool(state) and state["pending"] is None, str(state))

    # --- 棋谱页签里回看任意一手：新的一手在上面，第一手在最后一行 ---
    tab(p, "moves")
    feed = p.eval("document.querySelectorAll('.feed-row').length")
    check("棋谱页签列出全部落子", feed == 3, f"{feed} 行")

    p.eval("[...document.querySelectorAll('.feed-row')].pop().click()")
    time.sleep(1.8)
    active = p.eval("(() => { const el = document.querySelector('.info-tab.is-active'); return el ? el.textContent : ''; })()")
    check("点某一手自动切回解说", active == "解说", active)

    head = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
    check("点棋谱最后一行能回看第一手", "第 1 手" in head, head)
    verdict = p.eval("(() => { const el = document.querySelector('.ex-verdict'); return el ? el.textContent : ''; })()")
    check("解说给出档位", verdict in ("胜着", "好手", "过渡", "失着", "禁手"), verdict)

    p.shot(str(SHOTS / "15-explain-history.png"))
    print(f"  截图 {SHOTS / '15-explain-history.png'}")

    # 曲线上的转折点按钮（手数太少时可能没有，有就必须跳到对应手）
    tab(p, "analysis")
    jump = p.eval("(() => { const b = document.querySelector('.wr-jump'); return b ? b.textContent : ''; })()")
    if jump:
        want = jump.replace(" ", "")
        p.eval("document.querySelector('.wr-jump').click()")
        time.sleep(1.8)
        head2 = p.eval("(() => { const el = document.querySelector('.panel-explain .panel-head h2'); return el ? el.textContent : ''; })()")
        check("点转折点跳到它说的那一手", head2.replace(" ", "") in want, f"按钮「{jump}」-> 面板「{head2}」")
    else:
        print("  （手数太少，没有转折点可跳）")

    # --- 棋盘上的两个浮层开关 ---
    p.eval("document.querySelector('[data-tool=\"predict\"]').click()")
    time.sleep(0.6)
    legend = p.eval("document.querySelectorAll('[data-legend=\"predict\"]').length")
    check("关掉预测后图例消失", legend == 0, f"{legend} 个图例")
    p.eval("document.querySelector('[data-tool=\"predict\"]').click()")
    time.sleep(0.6)
    legend = p.eval("document.querySelectorAll('[data-legend=\"predict\"] b').length")
    check("打开预测后图例回来", legend >= 3, f"{legend} 个点")

    # --- 第四段：复盘页 ---
    game_id = (p.eval("location.hash") or "").split("/play/")[-1]
    p.eval(f"location.hash = '#/replay/{game_id}'")
    time.sleep(3.0)

    head_text = p.eval("(() => { const el = document.querySelector('.page-head h2'); return el ? el.textContent : ''; })()")
    check("复盘页标题不再是「回放」", head_text == "复盘", head_text)
    check("复盘页有棋盘", p.eval('!!document.querySelector(".board-canvas")'))
    check("复盘页没有英文状态", "playing" not in (p.eval("document.querySelector('.page-head p').textContent") or ""))

    tab(p, "analysis")
    check("复盘页也有胜率曲线", p.eval("!!document.querySelector('.wr-svg')"))
    check("复盘页也有局面分析", p.eval("!!document.querySelector('.panel-analysis')"))
    check("复盘页给出当前手解说", p.eval("!!document.querySelector('.panel-explain')"))

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
