"""断点溢出复检：五档宽度 × 六个页面，横向都不能出滚动条。

    python tools/walk_layout.py

前面的走查只看了一档宽度（1440），窄屏的横向溢出它一概发现不了。这个脚本把
每个页面在五档宽度下都量一遍。

只看 documentElement.scrollWidth 是不够的——单个元素溢出但父级 overflow:hidden
时页面不会出滚动条，元素却被切掉了。所以两边都看：页面的 scrollWidth，
以及所有超出视口右边缘的元素清单。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from cdp import Page  # noqa: E402

BASE = "http://127.0.0.1:8000"
WIDTHS = [1440, 1100, 900, 760, 414]
PAGES = [("#/hub", "大厅"), ("#/tutorial", "教程"), ("#/play", "对弈"),
         ("#/learn", "学习"), ("#/code", "代码"), ("#/arena", "竞技场")]

REGISTER = """
(async () => {
  const name = 'ovf' + Date.now().toString(36).slice(-7)
  const r = await fetch('/api/auth/register', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, password: 'Ovf12345'}),
  })
  const j = await r.json()
  if (!j.token) return 'ERR ' + JSON.stringify(j)
  localStorage.setItem('gomoku.token', j.token)
  return name
})()
"""

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


def main():
    p = Page.open(BASE + "/")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
           deviceScaleFactor=1, mobile=False)
    time.sleep(1.0)
    print("login:", p.eval(REGISTER))
    p.send("Page.reload", ignoreCache=True)
    time.sleep(1.8)

    bad_total = 0
    for w in WIDTHS:
        p.send("Emulation.setDeviceMetricsOverride", width=w, height=900,
               deviceScaleFactor=1, mobile=False)
        time.sleep(0.5)
        for path, label in PAGES:
            p.eval("location.hash = " + json.dumps(path))
            time.sleep(1.1)
            raw = p.eval(PROBE)
            d = json.loads(raw)
            flag = "OK " if d["doc"] <= d["vw"] + 1 and d["n"] == 0 else "BAD"
            if flag == "BAD":
                bad_total += 1
            print(f"{flag} {w:>5} {label:<4} doc={d['doc']} vw={d['vw']} 溢出元素={d['n']}")
            if d["n"]:
                for b in d["bad"]:
                    print("      ", b)

    p.close()
    print(f"\n合计异常组合 {bad_total} / {len(WIDTHS)*len(PAGES)}")
    return 0 if bad_total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
