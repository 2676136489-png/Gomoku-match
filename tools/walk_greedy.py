"""贪心学习页的浏览器走查（CDP 驱动真浏览器）。

    python tools/walk_greedy.py

这一页的验证难点不在接口——棋谱解析和训练在 tests/test_greedy.py 里已经量得很细。
真正需要拿眼睛确认的是三件接口测不出来的事：

1. 轮播真的在动。静态截图看不出「每盘从头摆一遍」，所以这里等两秒再读手数。
2. 训练跑起来时界面有没有反应：训练态、交叉熵曲线、指标从「—」变成数字。
3. 分值表在两侧色条都画出来的情况下还能读——条子溢出栏位、数字列对不齐，
   只有真浏览器知道。

顺带守住横向不溢出：贪心页有两处最容易崩的布局，分值表的四栏和棋谱库的两栏。
窄屏量一次，`scrollWidth` 超过视口就是溢出。

前端是静态多页，直接访问 greedy.html。服务和浏览器都由脚本自己起，跑完自己关。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cdp import ensure_browser  # noqa: E402
from tools.harness import (  # noqa: E402
    BASE,
    NO_HSCROLL,
    SET_INPUT,
    SHOTS,
    WALK_DB,
    check,
    click,
    count,
    ensure_server,
    login,
    new_account,
    open_page,
    summarize,
    text_of,
)

UPLOAD_NAME = "走查用的棋谱"
UPLOAD_SGF = "(;GM[1]FF[4]SZ[15];B[hh];W[aa];B[ih];W[ba];B[jh];W[ca];B[kh];W[da];B[lh])"

COUNT_RE = (
    "(() => { const el = document.querySelector('.carousel-count');"
    " if (!el) return null;"
    " const m = el.textContent.match(/(\\d+)\\s*\\/\\s*(\\d+)/);"
    " return m ? [m[1], m[2]] : null; })()"
)


def hover(page, sel: str, inside: bool) -> None:
    """把鼠标真的移到元素上/移开。

    mouseenter/mouseleave 监听器收不到合成的 enter 事件——必须走 CDP 的 Input，
    让浏览器自己发真事件。
    """
    box = page.eval(
        "((sel) => { const el = document.querySelector(sel); if (!el) return null;"
        " const r = el.getBoundingClientRect();"
        " return { x: r.left + r.width / 2, y: r.top + Math.min(60, r.height / 2) }; })"
        f"({json.dumps(sel)})"
    )
    if not box:
        return
    x, y = box["x"], box["y"]
    if not inside:
        x, y = 4, 4
    page.send("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, buttons=0)
    time.sleep(0.2)


def main() -> int:
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = ensure_server(WALK_DB)
    browser = ensure_browser()

    page = open_page()
    try:
        with httpx.Client(base_url=BASE, timeout=30, trust_env=False) as c:
            _, token = new_account(c)

        login(page, token, "greedy.html")

        # ------------------------------------------------------------ 落地
        # 错误横幅是常驻节点，没错误时靠 hidden 收起——只查「可见的错误」
        check("页面没崩", page.eval("!document.querySelector('.banner-error:not([hidden])')"))
        check("渲染出贪心页", page.eval("!!document.querySelector('.greedy')"))
        check("标题是贪心学习", text_of(page, ".page-head h2") == "贪心学习", text_of(page, ".page-head h2"))
        check("页头三个小签", count(page, ".greedy-head-badges .badge") == 3,
              str(count(page, ".greedy-head-badges .badge")))
        check("初始状态是未训练", "未训练" in text_of(page, ".greedy-head-badges"),
              text_of(page, ".greedy-head-badges"))
        check("初始 1440 下不横向溢出", page.eval(NO_HSCROLL))

        # ------------------------------------------------------------ 棋谱展台
        check("展台在页面上", page.eval("!!document.querySelector('.carousel')"))
        check("展台里有棋盘", count(page, ".carousel-board .board-canvas") >= 1,
              str(count(page, ".carousel-board .board-canvas")))
        dots = count(page, ".carousel-dots .carousel-dot")
        check("圆点数等于棋谱数", dots >= 5, str(dots))
        total = page.eval(COUNT_RE)
        check("展台报出第几盘/共几盘", bool(total) and int(total[1]) == dots, str(total))

        # 轮播是这页的核心：手数只增不减，说明棋盘在一手一手摆。
        # 不能只等一次——一盘摆完会有 2.4 秒的停留，正好落在停留期就白等。
        first = _ply(text_of(page, ".carousel-facts dd"))
        grew = False
        for _ in range(30):
            time.sleep(0.25)
            if _ply(text_of(page, ".carousel-facts dd")) > first:
                grew = True
                break
        check("棋盘自己会摆子", grew, f"起点 {first}")

        # 悬停要停住：用户盯着某一手看时，轮播不能把棋盘换走
        hover(page, ".carousel .board-frame", inside=True)
        time.sleep(0.4)
        held = text_of(page, ".carousel-facts dd")
        time.sleep(1.3)
        check("悬停时停住", _ply(text_of(page, ".carousel-facts dd")) == _ply(held), held)
        hover(page, ".carousel .board-frame", inside=False)

        check("展台有三个控制键", count(page, ".carousel-btn") == 3, str(count(page, ".carousel-btn")))
        page.shot(str(SHOTS / "greedy-01-gallery.png"))

        before = page.eval(COUNT_RE)
        was = _ply(text_of(page, ".carousel-facts dd"))
        click(page, ".carousel-controls .carousel-btn:last-of-type", wait=0.6)
        after = page.eval(COUNT_RE)
        check("能翻到下一盘", bool(after) and after != before, f"{before} → {after}")
        if not after:
            print("       页面状态：", page.eval("document.body.innerText.slice(0, 200)").replace("\n", " / "))
        # 切过去应该还在头几手；原来那盘可能已经摆了二三十手
        now = _ply(text_of(page, ".carousel-facts dd"))
        check("翻盘后从头摆", now <= 5 and (was <= 5 or now < was), f"{was} → {now}")

        # ------------------------------------------------------------ 分值表
        rows = count(page, ".weight-table .weight-row")
        check("分值表八档加一行表头", rows == 9, str(rows))
        head = text_of(page, ".weight-row.is-head")
        check("表头标了两列", "手写" in head and "学到" in head, head)
        check("每档两条色条", count(page, ".weight-bar") == 16, str(count(page, ".weight-bar")))
        spill = page.eval(
            "(() => { const out = [];"
            " document.querySelectorAll('.weight-bars').forEach(box => {"
            "   const right = box.getBoundingClientRect().right;"
            "   box.querySelectorAll('.weight-bar').forEach(bar => {"
            "     if (bar.getBoundingClientRect().right > right + 1) out.push(bar.className);"
            "   });"
            " }); return out.length; })()"
        )
        check("色条不越过栏位", spill == 0, str(spill))
        widths = page.eval(
            "[...document.querySelectorAll('.weight-bars')].map(b => Math.round(b.getBoundingClientRect().width))"
        )
        check("色条栏位拿到真实宽度", bool(widths) and min(widths) > 100, str(widths[:4]))

        # ------------------------------------------------------------ 训练台
        check("训练台有四格指标", count(page, ".metric-grid .metric") == 4,
              str(count(page, ".metric-grid .metric")))
        check("没训练时指标是空的", "—" in text_of(page, ".metric-grid"),
              text_of(page, ".metric-grid")[:40])
        check("训练前 α 显示 0.90", "0.90" in text_of(page, ".metric-grid"),
              text_of(page, ".metric-grid"))

        started = time.time()
        click(page, ".greedy-train .btn-primary", wait=0.0)
        # 训练不到一秒，点完立刻读一次是在赌运气：慢一点训练已经结束、按钮变回「重新学习」，
        # 快一点 SSE 的 start 帧还没到。连续读一小段，中途见过「训练中」或进入 live 态就算数。
        seen_busy = False
        for _ in range(70):
            if "训练中" in text_of(page, ".greedy-train") or page.eval(
                "!!document.querySelector('.greedy-stage.is-live')"
            ):
                seen_busy = True
                break
            time.sleep(0.03)
        check("训练状态变成训练中", seen_busy or bool(text_of(page, ".greedy-done")),
              text_of(page, ".greedy-stage"))
        # 曲线要等 epoch 事件进来才会出现。训练分两段：先把棋谱重放成样本，再逐
        # epoch 下降，前一段没有曲线。按「见过曲线」或「训练已经结束」两个出口判断。
        saw_curve = False
        for _ in range(250):
            if page.eval("!!document.querySelector('.greedy-curve svg path')"):
                saw_curve = True
                break
            if text_of(page, ".greedy-done"):
                break
            time.sleep(0.04)
        check("训练过程中出现了交叉熵曲线", bool(saw_curve) or bool(text_of(page, ".greedy-done")),
              text_of(page, ".greedy-stage"))

        # 曲线与结论的余量读取
        curve = False
        for _ in range(50):
            time.sleep(0.3)
            if page.eval("!!document.querySelector('.greedy-curve svg path')"):
                curve = True
                break
        check("交叉熵曲线画出来了", curve)
        check("曲线带首尾读数", len(text_of(page, ".greedy-curve-foot")) > 6,
              text_of(page, ".greedy-curve-foot"))

        done = ""
        for _ in range(60):
            time.sleep(0.3)
            done = text_of(page, ".greedy-done")
            if done:
                break
        check("训练跑完给出结论", bool(done), done)
        check("训练后标记已训练", "已训练" in text_of(page, ".greedy-head-badges"))
        check("训练耗时合理", time.time() - started < 60, f"{time.time() - started:.1f}s")
        metrics = text_of(page, ".metric-grid")
        check("指标变成百分比", "%" in metrics, metrics[:60])
        check("指标里写了训练前", "训练前" in metrics, metrics[:80])
        print(f"       训练结果：{done}")
        page.shot(str(SHOTS / "greedy-02-trained.png"))

        # 分值表在训练后要真的变了：变化量列不该还有一堆 0.00
        shifted = page.eval(
            "[...document.querySelectorAll('.weight-num.is-learn')]"
            ".filter(el => el.classList.contains('is-up') || el.classList.contains('is-down')).length"
        )
        check("训练后有档位被标成上调或下调", shifted >= 1, str(shifted))

        # 还原手写分值表：这是「看差在哪」的另一半
        click(page, ".greedy-train .btn:last-of-type", wait=1.2)
        check("能还原成手写", "未训练" in text_of(page, ".greedy-head-badges"),
              text_of(page, ".greedy-head-badges"))

        # ------------------------------------------------------------ 上传
        before_total = count(page, ".corpus-list li")
        check("棋谱库列出内置棋谱", before_total >= 5, str(before_total))
        check("内置棋谱没有删除按钮", count(page, ".corpus-del") == 0, str(count(page, ".corpus-del")))

        click(page, ".upload-zone .linkish", wait=0.5)
        check("粘贴区展开", page.eval("!!document.querySelector('.upload-paste')"))
        filled = page.eval(SET_INPUT % (json.dumps(".upload-paste .input"), json.dumps(UPLOAD_NAME)))
        check("能填名字", bool(filled))
        filled = page.eval(SET_INPUT % (json.dumps(".upload-paste .textarea"), json.dumps(UPLOAD_SGF)))
        check("能粘棋谱", bool(filled))
        click(page, ".upload-paste .btn-primary", wait=1.4)

        after_total = count(page, ".corpus-list li")
        check("上传后列表多一行", after_total == before_total + 1, f"{before_total} → {after_total}")
        check("上传的那行带删除按钮", count(page, ".corpus-del") == 1, str(count(page, ".corpus-del")))
        check("上传的那盘被选中", page.eval(
            "!!document.querySelector('.corpus-list li.is-active .corpus-tag.is-upload')"
        ))
        check("展台跟着切过去", text_of(page, ".carousel-head h3") == UPLOAD_NAME,
              text_of(page, ".carousel-head h3"))
        page.shot(str(SHOTS / "greedy-03-corpus.png"))

        # 删掉刚上传的那一盘：它此刻是选中行，定位到选中行里的删除键，
        # 免得同名残留行抢先被点
        click(page, ".corpus-list li.is-active .corpus-del", wait=1.2)
        check("删掉之后列表回原样", count(page, ".corpus-list li") == before_total,
              str(count(page, ".corpus-list li")))

        # ------------------------------------------------------------ 断点
        page.viewport(1024, 900)
        time.sleep(0.7)
        check("1024 下不横向溢出", page.eval(NO_HSCROLL))
        check("1024 下两栏收成一栏", page.eval(
            "getComputedStyle(document.querySelector('.greedy-grid'))"
            ".gridTemplateColumns.split(' ').length === 1"
        ))
        page.shot(str(SHOTS / "greedy-04-narrow.png"))

        page.viewport(1440, 900)
        time.sleep(0.5)
        check("回到宽屏仍不溢出", page.eval(NO_HSCROLL))
        page.shot(str(SHOTS / "greedy-05-full.png"), full=True)
    finally:
        page.close()
        if browser is not None:
            browser.terminate()
        if server is not None:
            server.terminate()

    return summarize()


def _ply(text: str) -> int:
    """「37/ 100」这种文本里把当前手数抠出来。"""
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits or 0)


if __name__ == "__main__":
    sys.exit(main())
