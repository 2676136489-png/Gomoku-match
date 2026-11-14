"""浏览器走查的公共脚手架。

几个 `walk_*.py` 做的是同一件事：把一个真服务拉起来、把真浏览器接上、注册个账号、
落地到某一页，然后断言「人在屏幕上看得见的东西」。这些动作每个脚本都要重写一遍
太浪费，抽到这里。

分两层：
- 服务层（`ensure_server` / `healthy` / `api`）：临时库、独立端口，跑完自己收。
- 页面层（`open_page` / `login` / 几段 eval 片段）：把 CDP 的原始调用包成
  「落在某个路由上、带着某个账号」这一个动作。

断言不用统一：`check` 只维护计数，各脚本自己决定量什么。
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

from tools.cdp import Page  # noqa: E402

# 走查固定用自己的端口，且每次运行随机取一个空闲端口：固定端口上如果还挂着
# 上一次没死透的服务，healthy() 会直接复用——一次性库没被重置，上一轮的
# 上传残留就会污染这一轮的断言（这个亏吃过一次）。
def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = int(os.environ.get("GOMOKU_TEST_PORT", "0")) or _free_port()
BASE = os.environ.get("GOMOKU_TEST_BASE", f"http://127.0.0.1:{PORT}")
# 截图尺寸固定成 1440×900，谁在跑、浏览器窗口多大都不影响
VIEW = (1440, 900)
SHOTS = ROOT / "docs" / "walk"
# 走查用一次性库，不碰 data/gomoku.db
WALK_DB = ROOT / ".tmp" / "walk.db"
# 截图里出现的名字，用正常昵称而不是 walkA12345
NICKNAMES = ("听雨", "松风", "竹影", "青崖", "石泉", "晚舟", "寒江", "暮云", "空山", "归鹤")

PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}", flush=True)
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}", flush=True)


def healthy() -> bool:
    """探针必须认得出「是我们这个服务」。

    只看 /api/health 返回 200 不够：别的项目也占 8000（比如 AI Research Workspace），
    它的 health 也返回 200，走查会一路打到人家身上，注册接口直接 405。
    本服务的 health 是扁平结构，且必带 uptime_s / languages 两个键。
    """
    try:
        with httpx.Client(base_url=BASE, timeout=2, trust_env=False) as c:
            r = c.get("/api/health")
            if r.status_code != 200:
                return False
            body = r.json()
            return body.get("status") == "ok" and "uptime_s" in body and "languages" in body
    except Exception:
        return False


def ensure_server(db_path=None):
    """BASE 上没自家的服务就自己拉一个，跑完关掉。

    走查要连真实服务，但把服务挂在外部容易中途被回收，脚本会因大量连接被拒绝而中断。

    传入 db_path 时使用独立的一次性库：走查会注册账号、下棋、上传棋谱，
    跑在开发库上会写入大量测试数据，并出现在截图里。
    """
    if healthy():
        return None
    env = dict(os.environ)
    env["PORT"] = str(PORT)
    if db_path:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        env["GOMOKU_DB"] = str(path)
        # 棋谱和模型权重也搬过去：走查会训练模型、上传棋谱，
        # 留在 data/ 里会把开发时训好的那一份盖掉。
        env["GOMOKU_DATA"] = str(path.parent)
        # 数据目录里还可能留着上一轮走查（或临时调试）的上传语料和权重：
        # 库是新的、语料还是旧的，断言就会拿到不该有的「上传」行。
        # 内置语料随仓库走（server/gomoku/corpus_seed.json），删这两个不影响。
        for junk in ("corpus.json", "greedy_model.json"):
            (path.parent / junk).unlink(missing_ok=True)
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "serve.py")],
        cwd=str(ROOT),
        env=env,
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


def api(token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=BASE, timeout=60, trust_env=False, headers=headers)


def new_account(client) -> tuple[str, str]:
    """注册一个账号，返回（昵称, token）。

    昵称从固定池里取，重复了就换一个——截图里出现「听雨」「松风」比出现
    「walkA48213」更像真实产品。
    """
    for name in NICKNAMES:
        r = client.post("/api/auth/register", json={"name": name, "password": "gomoku-2026"})
        if r.status_code < 400:
            return name, r.json()["token"]
    raise RuntimeError("昵称池已用尽，清理 .tmp/walk.db 后重跑")


def open_page(base: str = "http://127.0.0.1:9222") -> Page:
    page = Page.open(f"{BASE}/", base=base)
    # 浏览器是常驻复用的：上个会话缓存住的 JS 会让走查测到旧代码。
    # 发布沙箱不背这个锅——本地调试一律禁缓存，跟 smoke.py 同一约定。
    page.send("Network.enable")
    page.send("Network.setCacheDisabled", cacheDisabled=True)
    page.viewport(*VIEW)
    return page


def login(page: Page, token: str, path: str) -> None:
    """先落地、再写 token、然后整页跳到目标页。

    前端是静态多页（MPA），每次整页加载 api.js 都会重读 localStorage，
    所以不用再像 SPA 那样手动 reload 一次。
    """
    page.goto(f"{BASE}/", wait=0.6)
    page.eval(f"localStorage.setItem('gomoku.token', {json.dumps(token)})")
    page.goto(f"{BASE}/{path}", wait=1.4)


# 元素下沿要落在视口里，留 8px 余量（滚动条、半像素取整都能吃掉一点）
FIT = """
((sel) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  return el.getBoundingClientRect().bottom <= innerHeight - 8;
})(%s)
"""

NO_HSCROLL = "document.documentElement.scrollWidth <= innerWidth + 1"


def click(page: Page, sel: str, wait: float = 0.45) -> bool:
    """点一个选择器命中的元素。找不到返回 False，让断言去报错。"""
    ok = page.eval(
        "((sel) => { const el = document.querySelector(sel);"
        " if (!el) return false; el.click(); return true; })"
        f"({json.dumps(sel)})"
    )
    time.sleep(wait)
    return bool(ok)


def text_of(page: Page, sel: str) -> str:
    value = page.eval(
        "((sel) => { const el = document.querySelector(sel);"
        " return el ? el.textContent.trim() : ''; })"
        f"({json.dumps(sel)})"
    )
    return value or ""


def count(page: Page, sel: str) -> int:
    value = page.eval(f"document.querySelectorAll({json.dumps(sel)}).length")
    return int(value or 0)

# 受控 input 必须走原生 setter，否则 React 认为值没变，onChange 不触发
SET_INPUT = """
((sel, value) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, String(value));
  el.dispatchEvent(new Event('input', { bubbles: true }));
  return true;
})(%s, %s)
"""


def summarize() -> int:
    print("\n" + "=" * 46)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    return 1 if FAIL else 0
