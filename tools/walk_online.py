"""在线对战与棋谱的端到端走查（CDP 驱动真浏览器）。

    python tools/walk_online.py

流程：走接口注册两个账户并开桌 → 两个页面各自登录进同一局 → 交替落子 →
认输结算 → 检查段位分变了 → 截屏。最后把大厅、棋谱库、回放也各截一张。

只断言「真人能看到的东西」：棋盘 DOM 上的手数、计时文本、结算卡片、段位数字。
接口测试证明不了这些——按钮点下去有没有反应、结算卡片长什么样，只有真浏览器知道。

前置只有一步：先 npm run build 把前端构建出来（服务托管的是 web/dist）。
服务和浏览器脚本自己起、跑完自己关。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.cdp import Page, ensure_browser  # noqa: E402

BASE = os.environ.get("GOMOKU_TEST_BASE", "http://127.0.0.1:8000")
# 第二个浏览器实例。两个账号不能共用一个 localStorage。
CDP_A = os.environ.get("CDP_BASE_A", "http://127.0.0.1:9222")
CDP_B = os.environ.get("CDP_BASE_B", "http://127.0.0.1:9223")
# 截图尺寸固定成 1440×900，谁在跑、浏览器窗口多大都不影响
VIEW = (1440, 900)
SHOTS = ROOT / "docs" / "walk"
PASS = 0
FAIL = 0


def ensure_server():
    """8000 上没服务就自己拉一个，跑完关掉。

    走查要连真服务，但把服务挂在外面很容易中途被回收，脚本就砸在一堆连接拒绝上。
    """
    if healthy():
        return None
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "serve.py")],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(160):
        if healthy():
            return proc
        if proc.poll() is not None:
            raise RuntimeError(f"serve.py 起不来，退出码 {proc.returncode}")
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"{BASE} 一直没就绪")


def healthy() -> bool:
    try:
        with httpx.Client(base_url=BASE, timeout=2, trust_env=False) as c:
            return c.get("/api/health").status_code == 200
    except Exception:
        return False


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def api(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=BASE, timeout=30, trust_env=False, headers=headers)


CLICK_CELL = """
((x, y) => {
  const canvas = document.querySelector('.board-canvas');
  if (!canvas) return 'no-canvas';
  const rect = canvas.getBoundingClientRect();
  const size = rect.width;
  const pad = size * 0.055;
  const step = (size - pad * 2) / 14;
  const ev = new MouseEvent('click', {
    bubbles: true,
    clientX: rect.left + pad + x * step,
    clientY: rect.top + pad + y * step,
  });
  canvas.dispatchEvent(ev);
  return 'ok';
})(%d, %d)
"""


# 元素下沿要落在视口里，留 8px 余量（滚动条、半像素取整都能吃掉一点）
FIT = """
((sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  return el.getBoundingClientRect().bottom <= innerHeight - 8;
})(%s)
"""

NO_HSCROLL = "document.documentElement.scrollWidth <= innerWidth + 1"

# 受控 input 必须走原生 setter，否则 React 认为值没变，onChange 不触发
SET_RANGE = """
((sel, value) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(el, String(value));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})(%s, %d)
"""


def open_page(base: str) -> Page:
    page = Page.open(f"{BASE}/", base=base)
    page.viewport(*VIEW)
    return page


def login(page: Page, token: str, path: str) -> None:
    """先落地、再写 token、然后整页重载。

    只改 hash 不会重新加载模块，token 在 api.js 里是模块级变量，读一次就定了。
    所以必须真的 reload，否则页面还拿着上一个账号的状态。
    """
    page.goto(f"{BASE}/", wait=0.8)
    page.eval(f"localStorage.setItem('gomoku.token', {json.dumps(token)})")
    page.goto(f"{BASE}/#{path}", wait=0.6)
    page.send("Page.reload", ignoreCache=True)
    time.sleep(1.6)


def main() -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    # 服务与浏览器都由脚本自己管：两个账号要落在两个浏览器里（DevTools 两个端口）
    server = ensure_server()
    spawned = [p for p in (ensure_browser(CDP_A), ensure_browser(CDP_B)) if p]
    stamp = int(time.time()) % 100000
    alice = f"walkA{stamp}"
    bob = f"walkB{stamp}"

    with api() as c:
        pa = c.post("/api/auth/register", json={"name": alice, "password": "gomoku-2026"}).json()
        pb = c.post("/api/auth/register", json={"name": bob, "password": "gomoku-2026"}).json()
    ta, tb = pa["token"], pb["token"]

    with api(ta) as c:
        room = c.post(
            "/api/rooms", json={"rule": "renju", "clock_base": 300, "clock_inc": 5, "rated": True}
        ).json()["room"]
    with api(tb) as c:
        joined = c.post(f"/api/rooms/{room['code']}/join").json()
    game_id = joined["game_id"]
    seat_b = joined["seat"]
    seat_a = 3 - seat_b
    print(f"对局 {game_id}  A 执 {'黑' if seat_a == 1 else '白'}，B 执 {'黑' if seat_b == 1 else '白'}")

    page_a = open_page(CDP_A)
    page_b = open_page(CDP_B)
    login(page_a, ta, f"/room/{game_id}")
    login(page_b, tb, f"/room/{game_id}")

    time.sleep(1.2)
    check("对局页没有崩", page_a.eval("!!document.querySelector('.room') && !document.querySelector('.crash')"))
    check("棋盘渲染出来了", page_a.eval("!!document.querySelector('.board-canvas')"))
    check("双方名字出现在对手栏",
          page_a.eval(f"document.body.innerText.includes({json.dumps(bob)})"))
    check("计时器有读数", page_a.eval("!!document.querySelector('.room-clock').innerText.match(/\\d/)"))
    # 棋盘是正方形，只按列宽算会把下沿顶出视口——这条断言是那次的回归保护
    check("棋盘和说明都在首屏内", page_a.eval(FIT % json.dumps(".board-caption")))
    check("对局页没有横向滚动", page_a.eval(NO_HSCROLL))

    page_a.shot(str(SHOTS / "20-online-room.png"))

    # 交替落子：谁执黑谁先下
    black_page, white_page = (page_a, page_b) if seat_a == 1 else (page_b, page_a)
    moves = [(7, 7), (7, 8), (8, 8), (6, 6), (6, 7), (9, 9)]
    for i, (x, y) in enumerate(moves):
        actor = black_page if i % 2 == 0 else white_page
        actor.eval(CLICK_CELL % (x, y))
        time.sleep(0.45)

    time.sleep(0.8)
    count = page_a.eval("document.querySelectorAll('.game-row').length")
    check("六手都落到盘上", count == 6, str(count))
    check("最后一手有朱砂标记", page_a.eval("!!document.querySelector('.game-row')"))

    page_a.shot(str(SHOTS / "21-online-mid.png"))

    # 认输：先接管 confirm，再点按钮
    black_page.eval("window.confirm = () => true")
    black_page.eval("document.querySelector('.btn-danger').click()")
    time.sleep(1.4)

    check("结算卡片出现", white_page.eval("!!document.querySelector('.result-card')"), )
    check("胜方看到「你赢了」", white_page.eval("document.querySelector('.result-card h3')?.innerText === '你赢了'"))
    check("负方看到「你输了」", black_page.eval("document.querySelector('.result-card h3')?.innerText === '你输了'"))
    check("结算带分数变化", white_page.eval("!!document.querySelector('.result-delta')"))
    white_page.shot(str(SHOTS / "22-online-result.png"))

    # 结算之后段位分要真的变
    with api(tb) as c:
        me_white = c.get("/api/auth/me").json()["user"]["stats"]
    with api(ta) as c:
        me_a = c.get("/api/auth/me").json()["user"]["stats"]
    winner_stats = me_white if seat_b != 1 else me_a
    check("胜方分数上涨", winner_stats["rating"] > 1200, str(winner_stats["rating"]))
    check("胜方记一胜", winner_stats["wins"] == 1, str(winner_stats))

    # 大厅
    page_c = open_page(CDP_A)
    login(page_c, ta, "/online")
    time.sleep(1.0)
    check("大厅没有崩", page_c.eval("!!document.querySelector('.online') && !document.querySelector('.crash')"))
    check("大厅显示在线人数", page_c.eval("document.body.innerText.includes('人在线')"))
    check("大厅能看到段位分", page_c.eval("!!document.querySelector('.online-me b')"))
    page_c.shot(str(SHOTS / "23-online-lobby.png"))

    # 棋谱库
    page_d = open_page(CDP_A)
    login(page_d, ta, "/replay")
    time.sleep(1.0)
    rows = page_d.eval("document.querySelectorAll('.replay-table tbody tr').length")
    check("棋谱库里有刚下完的一局", (rows or 0) >= 1, str(rows))
    page_d.shot(str(SHOTS / "24-replay-list.png"))

    # 回放
    page_d.goto(f"{BASE}/#/replay/{game_id}", wait=1.3)
    check("回放页没有崩", page_d.eval("!!document.querySelector('.replay-player') && !document.querySelector('.crash')"))
    check("回放有进度条", page_d.eval("!!document.querySelector('.replay-scrub')"))
    check("控制条和进度条都在首屏内", page_d.eval(FIT % json.dumps(".replay-scrub")))
    check("回放页没有横向滚动", page_d.eval(NO_HSCROLL))
    page_d.eval(SET_RANGE % (json.dumps(".replay-scrub"), 3))
    time.sleep(0.4)
    # 只把 value 赋上去 React 是不认的，它有自己的 value 追踪器；
    # 断言一下手数真的跳到第 4 手，免得截了张「第一手」当封面还以为拖动了
    check("拖进度条真的换了手数", (page_d.eval("document.querySelector('.replay-count').innerText") or "").startswith("4 /"),
          str(page_d.eval("document.querySelector('.replay-count').innerText")))
    time.sleep(0.5)
    page_d.shot(str(SHOTS / "25-replay.png"))

    # 大厅首屏（带段位条与七张卡）
    page_e = open_page(CDP_A)
    login(page_e, ta, "/hub")
    time.sleep(1.2)
    check("大厅有段位进度条", page_e.eval("!!document.querySelector('.rank-track')"))
    check("模块卡是七张", page_e.eval("document.querySelectorAll('.module-card').length") == 7,
          str(page_e.eval("document.querySelectorAll('.module-card').length")))
    page_e.shot(str(SHOTS / "26-hub-v4.png"))

    for page in (page_a, page_b, page_c, page_d, page_e):
        page.close()

    for proc in spawned:
        proc.terminate()
    if server:
        server.terminate()

    print("\n" + "=" * 46)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
