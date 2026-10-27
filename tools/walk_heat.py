"""验证「网络视角」：登录、落子、打开热力图、截图。

    .venv/Scripts/python.exe tools/walk_heat.py
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
from cdp import Page  # noqa: E402

BASE = "http://127.0.0.1:8000"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "walk"
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""), flush=True)


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main() -> int:
    name = "heat" + "".join(random.choices(string.ascii_lowercase, k=6))
    token = post("/api/auth/register", {"name": name, "password": "Heat12345"})["token"]

    p = Page.open(BASE + "/")
    p.send(
        "Emulation.setDeviceMetricsOverride",
        width=1440,
        height=1100,
        deviceScaleFactor=1,
        mobile=False,
    )
    time.sleep(2.0)
    p.eval(f"localStorage.setItem('gomoku.token', {json.dumps(token)})")

    # 直接开目标路由，而不是改 hash —— 改 hash 的时机太早会被随后的页面加载覆盖掉
    p.close()
    p = Page.open(BASE + "/#/play")
    p.send(
        "Emulation.setDeviceMetricsOverride",
        width=1440,
        height=1100,
        deviceScaleFactor=1,
        mobile=False,
    )
    time.sleep(3.5)

    has_board = p.eval('!!document.querySelector(".board-canvas")')
    check("进入对弈页", has_board, f"board-canvas={has_board}")

    # 下一手，让棋盘上有子，热力图才有东西可看
    p.eval(
        """
      (() => {
        const c = document.querySelector('.board-canvas');
        if (!c) return false;
        const r = c.getBoundingClientRect();
        const pad = r.width * 0.055;
        const step = (r.width - pad * 2) / 14;
        const ev = (x, y) => {
          const opts = {clientX: r.left + pad + x * step, clientY: r.top + pad + y * step, bubbles: true};
          c.dispatchEvent(new MouseEvent('click', opts));
        };
        ev(7, 8);
        return true;
      })()
    """
    )
    time.sleep(2.5)

    btn = p.eval(
        "(() => { const b = document.querySelector('[data-tool=\"heat\"]'); return b ? b.textContent : ''; })()"
    )
    check("出现网络视角按钮", btn.strip() == "网络视角", repr(btn))

    p.eval("document.querySelector('[data-tool=\"heat\"]').click()")
    time.sleep(2.5)

    legend = p.eval(
        "(() => { const el = document.querySelector('[data-legend=\"heat\"]'); return el ? el.textContent : ''; })()"
    )
    check("热力图给出前五点", "网络最看好的五个点" in legend, legend[:80])

    chips = p.eval("document.querySelectorAll('[data-legend=\"heat\"] b').length")
    check("列表正好 5 项", chips == 5, f"{chips} 项")

    p.shot(str(SHOTS / "12-neural-heat.png"))
    print(f"  截图 {SHOTS / '12-neural-heat.png'}")

    # 再点一次应该收起
    p.eval("document.querySelector('[data-tool=\"heat\"]').click()")
    time.sleep(1.2)
    gone = p.eval("document.querySelectorAll('[data-legend=\"heat\"]').length")
    check("再点一次收起", gone == 0, f"{gone} 个图例")

    p.close()

    bad = [r for r in results if not r[1]]
    print()
    print(f"通过 {len(results) - len(bad)} 项，失败 {len(bad)} 项")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
