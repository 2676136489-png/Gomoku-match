"""断点溢出复检：五档宽度 × 七个页面，横向都不能出滚动条。

    python tools/walk_layout.py

其它走查只在 1440 下看一屏，窄屏的横向溢出它一概发现不了。这个脚本把每个页面
在五档宽度下都量一遍，跑完打一张总表。

只看 documentElement.scrollWidth 是不够的——单个元素溢出、但父级 overflow:hidden
时页面不会出滚动条，元素却被切掉了。所以两边都看：页面的 scrollWidth，以及
所有越过视口边缘的元素清单。

服务和浏览器都由脚本自己起，跑完自己关。前置只有一步：先 npm run build。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cdp import ensure_browser  # noqa: E402
from tools.harness import (  # noqa: E402
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

# 覆盖导航上每一个入口，外加两个带参数的二级页面。
# /play 不带参数是准备页（选难度、选执哪边），带参数才是真的棋盘。
PAGES = [
    ("#/hub", "大厅"),
    ("#/tutorial", "教程"),
    ("#/play", "对弈"),
    ("#/replay", "复盘"),
    ("#/code", "代码"),
    ("#/arena", "竞技场"),
    ("#/greedy", "贪心学习"),
]

PROBE = """
(() => {
  const vw = document.documentElement.clientWidth
  const doc = document.documentElement.scrollWidth
  const bad = []
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.landscape')) continue   // 山水层自带 overflow:hidden，量它没意义
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


TIP_DISPLAY = """
((sel) => {
  const el = document.querySelector(sel);
  return el ? getComputedStyle(el, '::after').display : null;
})(%s)
"""

HOVER = """
((sel, inside) => {
  const el = document.querySelector(sel);
  if (!el) return false;
  const r = el.getBoundingClientRect();
  const x = inside ? r.left + r.width / 2 : 4;
  const y = inside ? r.top + r.height / 2 : 4;
  return [Math.round(x), Math.round(y)];
})(%s, %s)
"""


def measure(page) -> dict:
    return json.loads(page.eval(PROBE))


def hover(page, sel: str, inside: bool) -> None:
    """把鼠标真的移过去。

    React 的 onMouseEnter 由 mouseover 合成，dispatch 一个 'mouseenter' 收不到；
    这里走 CDP 的 Input，让浏览器自己发真事件。
    """
    xy = page.eval(HOVER % (json.dumps(sel), "true" if inside else "false"))
    if not xy:
        return
    page.send("Input.dispatchMouseEvent", type="mouseMoved", x=xy[0], y=xy[1], buttons=0)
    time.sleep(0.25)


def main() -> int:
    server = ensure_server(WALK_DB)
    browser = ensure_browser()

    page = open_page()
    try:
        with httpx.Client(base_url="http://127.0.0.1:8000", timeout=30, trust_env=False) as c:
            _, token = new_account(c)
        login(page, token, "/hub")

        # 逐个宽度扫。换宽度后必须等一帧以上，否则量到的是上一档的布局。
        for width in WIDTHS:
            page.viewport(width, 900)
            time.sleep(0.5)
            for path, label in PAGES:
                page.eval("location.hash = " + json.dumps(path))
                time.sleep(0.9)
                d = measure(page)
                ok = d["doc"] <= d["vw"] + 1 and d["n"] == 0
                check(
                    f"{width:>5} × {label}",
                    ok,
                    f"doc={d['doc']} vw={d['vw']} 溢出元素={d['n']} {d['bad'][:2]}",
                )

        # 术语提示框：收起时必须 display:none。
        # 这一条是这次修出来的——原来用 opacity:0 藏，绝对定位的框照样算进滚动溢出，
        # 414 下对弈页凭空多出一条横向滚动条（doc 473 / vw 414）。
        # 术语挂在准备页的强度小签与规则说明上，教程页反而没有。
        page.viewport(1440, 900)
        time.sleep(0.5)
        page.eval("location.hash = '#/play'")
        time.sleep(0.9)
        check("准备页带术语标记", page.eval("document.querySelectorAll('.term').length") >= 2,
              str(page.eval("document.querySelectorAll('.term').length")))
        check("术语提示框收起时不占版面", page.eval(TIP_DISPLAY % json.dumps(".term")) == "none",
              str(page.eval(TIP_DISPLAY % json.dumps(".term"))))
        hover(page, ".term", inside=True)
        check("悬停时提示框展开", page.eval(TIP_DISPLAY % json.dumps(".term")) == "block",
              str(page.eval(TIP_DISPLAY % json.dumps(".term"))))
        hover(page, ".term", inside=False)
        check("移开后提示框收起", page.eval(TIP_DISPLAY % json.dumps(".term")) == "none")

        # 单独守一次贪心页：它比别的页多两处易崩布局（分值表四栏、棋谱库两栏），
        # 上面那一轮已经把每档宽度都量过了，这里只把结论挑出来说清楚。
        page.viewport(1440, 900)
        time.sleep(0.5)
        page.eval("location.hash = '#/greedy'")
        time.sleep(0.9)
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
