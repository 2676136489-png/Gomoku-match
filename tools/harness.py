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

BASE = os.environ.get("GOMOKU_TEST_BASE", "http://127.0.0.1:8000")
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
    try:
        with httpx.Client(base_url=BASE, timeout=2, trust_env=False) as c:
            return c.get("/api/health").status_code == 200
    except Exception:
        return False


def ensure_server(db_path=None):
    """8000 上没服务就自己拉一个，跑完关掉。

    走查要连真实服务，但把服务挂在外部容易中途被回收，脚本会因大量连接被拒绝而中断。

    传入 db_path 时使用独立的一次性库：走查会注册账号、下棋、上传棋谱，
    跑在开发库上会写入大量测试数据，并出现在截图里。
    """
    if healthy():
        return None
    env = dict(os.environ)
    if db_path:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        env["GOMOKU_DB"] = str(path)
        # 棋谱和模型权重也搬过去：走查会训练模型、上传棋谱，
        # 留在 data/ 里会把开发时训好的那一份盖掉。
        env["GOMOKU_DATA"] = str(path.parent)
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
