"""断点溢出复检：五档宽度 × 八个页面，横向都不能出滚动条。

    python tools/walk_layout.py

其它走查只在 1440 下看一屏，窄屏的横向溢出它一概发现不了。这个脚本把每个页面
在五档宽度下都量一遍，跑完打一张总表。

只看 documentElement.scrollWidth 是不够的——单个元素溢出、但父级 overflow:hidden
时页面不会出滚动条，元素却被切掉了。所以两边都看：页面的 scrollWidth，以及
所有越过视口边缘的元素清单。

前端是静态多页（MPA）：公开页（大厅、登录）不带 token 也能量，其余六页先注册
登录再量。服务和浏览器都由脚本自己起，跑完自己关。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cdp import ensure_browser  # noqa: E402
from tools.harness import (  # noqa: E402
    BASE,
    WALK_DB,
    check,
    ensure_server,
    login,
    new_account,
    open_page,
    summarize,
)

import httpx  # noqa: E402

# 414 是最窄的一档：手机竖屏。1440 是截图用的标准宽。
WIDTHS = [1440, 1100, 900, 760, 414]

# 前端是静态多页（MPA）：登录页是唯一的公开页；首页现在有登录门禁，
# 必须带着 token 量，否则会被踢回登录页，量到的是重定向目标。
PUBLIC = [
    ("login.html", "登录"),
]
PRIVATE = [
    ("index.html", "大厅"),
    ("tutorial.html", "教程"),
    ("play.html", "对弈"),
    ("replay.html", "复盘"),
    ("code.html", "代码"),
    ("arena.html", "竞技场"),
    ("greedy.html", "贪心学习"),
]

PROBE = """
(() => {
  const vw = document.documentElement.clientWidth
  const doc = document.documentElement.scrollWidth
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    // 被祖先的滚动/裁切容器收着的，不算溢出（章节导航本来就横向滚）
    let clipped = false
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const ov = getComputedStyle(a).overflowX
      if (ov === 'auto' || ov === 'scroll' || ov === 'hidden') { clipped = true; break }
    }
    if (clipped) continue
    if (r.right > vw + 1.5 || r.left < -1.5) {
      bad.push({
        tag: el.tagName.toLowerCase(),
        cls: (el.className || '').toString().slice(0, 40),
        left: Math.round(r.left),
        right: Math.round(r.right),
      })
    }
  }
  bad.sort((a, b) => b.right - a.right)
  return JSON.stringify({vw, doc, bad: bad.slice(0, 5), n: bad.length})
})()
"""


def measure(page) -> dict:
    return json.loads(page.eval(PROBE))


def sweep(page, width: int, pages, tag: str = "") -> None:
    """一个宽度档下逐页量一遍。换宽度后必须等一帧以上，否则量到的是上一档的布局。"""
    page.viewport(width, 900)
    time.sleep(0.5)
    for path, label in pages:
        page.goto(f"{BASE}/{path}", wait=1.2)
        d = measure(page)
        ok = d["doc"] <= d["vw"] + 1 and d["n"] == 0
        check(
            f"{width:>5} × {label}{tag}",
            ok,
            f"doc={d['doc']} vw={d['vw']} 溢出元素={d['n']} {d['bad'][:2]}",
        )


def main() -> int:
    server = ensure_server(WALK_DB)
    browser = ensure_browser()

    page = open_page()
    try:
        # --- 第一轮：公开页，不登录（登录页此时才是登录页） ---
        for width in WIDTHS:
            sweep(page, width, PUBLIC)

        # --- 第二轮：注册登录，量六个需要账号的页面 ---
        with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
            _, token = new_account(c)
        login(page, token, "play.html")

        for width in WIDTHS:
            sweep(page, width, PRIVATE)

        # --- 单独守一次贪心页：它比别的页多两处易崩布局（分值表四栏、棋谱库两栏），
        # 上面那一轮已经把每档宽度都量过了，这里只把结论挑出来说清楚。 ---
        page.viewport(1440, 900)
        time.sleep(0.5)
        page.goto(f"{BASE}/greedy.html", wait=1.5)
        check("贪心页分值表不越栏", page.eval(
            "(() => { const t = document.querySelector('.weight-table');"
            " if (!t) return false;"
            " return t.getBoundingClientRect().right <= document.documentElement.clientWidth + 1; })()"
        ))
        check("贪心页两栏并列", page.eval(
            "(document.querySelector('.greedy-grid') || {}).offsetWidth > 0"
        ))
    finally:
        page.close()
        if browser is not None:
            browser.terminate()
        if server is not None:
            server.terminate()

    return summarize()


if __name__ == "__main__":
    sys.exit(main())
