"""六模块端到端走查：登录 → 大厅 → 教程 → 对弈 → 学习 → 代码工坊 → 竞技场。

每一步都断言关键 DOM 存在，失败立即抛出并截图留证。
"""
from __future__ import annotations

import json
import random
import string
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from cdp import Page  # noqa: E402

BASE = "http://127.0.0.1:8000"
DOCS = ROOT / "docs"
SHOTS = ROOT / "docs" / "walk"

ok = 0
fail = []

# 上传测试用的源码：故意跟内置模板长得不一样，好确认编辑器真的换了内容
UPLOAD_SRC = '''\
"""走查时上传的角上开局。"""

SPOTS = [(7, 7), (7, 8), (8, 7), (3, 3), (3, 11), (11, 3), (11, 11)]


def choose_move(board, color, move_no):
    for x, y in SPOTS:
        if board[y][x] == 0:
            return (x, y)
    return (7, 7)
'''


def check(name: str, cond: bool, detail: str = ""):
    global ok
    if cond:
        ok += 1
        print(f"  PASS  {name}")
    else:
        fail.append(f"{name} {detail}")
        print(f"  FAIL  {name} {detail}")


def set_file(p, path: Path):
    """把本地文件塞进 <input type=file>，等价于用户点「选择文件…」。"""
    p.send("DOM.enable")
    doc = p.send("DOM.getDocument", depth=1, pierce=True)
    node = p.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
    if not node.get("nodeId"):
        raise SystemExit("找不到 file input")
    p.send("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[str(path)])


def api(path: str, body=None, token=None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data else "GET")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload))
    except urllib.error.HTTPError as e:
        payload = e.read() or b"{}"
        return e.code, (payload if raw else json.loads(payload))


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    user = f"walk{suffix}"
    pw = "Walk12345"

    p = Page.open(BASE + "/")
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
           deviceScaleFactor=1, mobile=False)
    time.sleep(1.5)

    # ---------------------------------------------------------------- 登录
    print("\n[1] 登录页")
    p.eval("localStorage.clear()")
    p.goto(BASE + "/", 1.6)
    check("渲染登录表单", p.eval("!!document.querySelector('.auth-form')"))
    cols = p.eval("getComputedStyle(document.querySelector('.auth-form')).gridTemplateColumns")
    check("表单单列布局", len(cols.split(" ")) == 1, f"实际 {cols}")
    inputs = p.eval("document.querySelectorAll('.auth-form input').length")
    check("两个输入框", inputs == 2, f"实际 {inputs}")
    p.shot(str(SHOTS / "01-login.png"))

    # 切到注册 tab
    p.eval("""
      (() => {
        const t = [...document.querySelectorAll('.auth-tabs button, .auth-tab')];
        if (t.length) { t[t.length-1].click(); return 'tab'; }
        const b = [...document.querySelectorAll('button')].find(b => /注册/.test(b.textContent));
        if (b) { b.click(); return 'btn'; }
        return 'none';
      })()
    """)
    time.sleep(0.4)
    p.shot(str(SHOTS / "02-register.png"))

    def fill(user_value: str, pass_value: str):
        # 注册模式有第三个「确认密码」输入框，按数量全部填满
        p.eval(
            "(([u, pw]) => {"
            "  const ins = document.querySelectorAll('.auth-form input');"
            "  const set = (el, v) => {"
            "    const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');"
            "    d.set.call(el, v);"
            "    el.dispatchEvent(new Event('input', {bubbles: true}));"
            "  };"
            "  set(ins[0], u); set(ins[1], pw);"
            "  if (ins[2]) set(ins[2], pw);"
            "  return ins.length;"
            "})(" + json.dumps([user_value, pass_value]) + ")"
        )

    n = fill(user, pw)
    n = p.eval("document.querySelectorAll('.auth-form input').length")
    check("注册表单填了三格", n == 3, f"实际 {n} 格")
    p.eval("document.querySelector('.auth-form').requestSubmit()")
    time.sleep(2.2)
    token = p.eval("localStorage.getItem('gomoku.token')")
    err = p.eval("document.querySelector('.auth-error')?.innerText || ''")
    check("注册后拿到 token", bool(token), repr(token) + " err=" + repr(err))
    if not token:
        p.shot(str(SHOTS / "02b-register-fail.png"))
        print("!! 注册失败，后续中止")
        p.close()
        return

    # ---------------------------------------------------------------- 大厅
    print("\n[2] 大厅")
    check("渲染大厅卡片", p.eval("document.querySelectorAll('.module-card').length") >= 5,
          f"实际 {p.eval('document.querySelectorAll(\".module-card\").length')}")
    check("显示用户名", user in (p.eval("document.body.innerText") or ""))
    p.shot(str(SHOTS / "03-hub.png"))

    # ---------------------------------------------------------------- 教程
    print("\n[3] 教程")
    p.eval("location.hash = '#/tutorial'")
    time.sleep(1.2)
    chapters = p.eval("document.querySelectorAll('.tut-nav button').length")
    check("章节列表非空", chapters >= 5, f"实际 {chapters}")
    check("教程正文有内容", len((p.eval("document.querySelector('.tut-body')?.innerText") or "")) > 200)
    check("教程内嵌棋盘图", p.eval("document.querySelectorAll('.tut-demo svg').length") >= 1,
          f"{p.eval('document.querySelectorAll(\".tut-demo svg\").length')} 个")
    p.shot(str(SHOTS / "04-tutorial.png"))
    # 切到第二章
    p.eval("""
      (() => { const b = document.querySelectorAll('.tut-nav button, .tut-chapter'); if (b[1]) b[1].click(); })()
    """)
    time.sleep(0.6)
    p.shot(str(SHOTS / "04b-tutorial-ch2.png"))

    # ---------------------------------------------------------------- 对弈
    print("\n[4] AI 对弈")
    p.eval("location.hash = '#/play'")
    time.sleep(1.4)
    p.shot(str(SHOTS / "05-play.png"))
    check("出现棋盘 canvas", p.eval("!!document.querySelector('canvas')"))

    # 走一手：点棋盘中心
    rect = p.eval("""
      (() => {
        const c = document.querySelector('canvas');
        const r = c.getBoundingClientRect();
        return {x: r.x, y: r.y, w: r.width, h: r.height};
      })()
    """)
    if rect:
        cx = rect["x"] + rect["w"] / 2
        cy = rect["y"] + rect["h"] / 2
        for ev, extra in (("mousePressed", {"button": "left", "clickCount": 1}),
                          ("mouseReleased", {"button": "left", "clickCount": 1})):
            p.send("Input.dispatchMouseEvent", type=ev, x=cx, y=cy, **extra)
        time.sleep(3.0)
    moves = p.eval("(window.__gomoku && window.__gomoku.moves) ? window.__gomoku.moves.length : 0")
    check("落子后有棋谱", bool(moves), f"{moves} 手")
    p.shot(str(SHOTS / "06-play-move.png"))

    # ---------------------------------------------------------------- 学习
    print("\n[5] 学习模式")
    p.eval("location.hash = '#/learn'")
    time.sleep(1.6)
    check("渲染教练面板", p.eval("!!document.querySelector('.coach, .coach-panel')"))
    p.shot(str(SHOTS / "07-learn.png"))
    rect = p.eval("""
      (() => { const c = document.querySelector('canvas'); if (!c) return null;
        const r = c.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; })()
    """)
    if rect:
        for ev in ("mousePressed", "mouseReleased"):
            p.send("Input.dispatchMouseEvent", type=ev,
                   x=rect["x"] + rect["w"] / 2 + 30, y=rect["y"] + rect["h"] / 2 + 30,
                   button="left", clickCount=1)
        time.sleep(2.6)
    coach_txt = p.eval("document.querySelector('.coach, .coach-panel')?.innerText || ''")
    check("教练给出评语", len(coach_txt) > 10, coach_txt[:40])
    p.shot(str(SHOTS / "08-learn-coach.png"))

    # ---------------------------------------------------------------- 代码工坊
    print("\n[6] 代码工坊")
    p.eval("location.hash = '#/code'")
    time.sleep(1.8)
    check("渲染编辑器", p.eval("!!document.querySelector('.code-drop .editor textarea')"))
    check("有拖拽落区", p.eval("!!document.querySelector('.code-drop')"))
    caps = api("/api/lab/capabilities", token=token)[1]
    langs = (caps.get("capabilities") or caps).get("languages", [])
    check("能力探测含 Python 与 C++", {"python", "cpp"} <= set(langs), f"{langs}")
    check("初始标注内置模板",
          p.eval("(document.querySelector('.code-file')||{}).innerText?.includes('内置模板') === true"))
    p.shot(str(SHOTS / "09-code.png"))

    # 校验 Python 模板
    code = p.eval("document.querySelector('.editor textarea').value")
    check("编辑器有初始代码", len(code or "") > 50, f"{len(code or '')} 字符")
    v = api("/api/lab/verify", {"source": code, "language": "python"}, token=token)
    check("校验 Python 模板通过", v[1].get("ok") is True, json.dumps(v[1])[:160])

    # 上传一份本地 .py：塞进 <input type=file>，跟点「选择文件…」同一条路
    upload = Path(tempfile.gettempdir()) / "walk-corner.py"
    upload.write_text(UPLOAD_SRC, encoding="utf-8")
    set_file(p, upload)
    got = False
    for _ in range(20):
        time.sleep(0.3)
        if p.eval("(document.querySelector('.code-file')||{}).innerText?.includes('walk-corner.py')"):
            got = True
            break
    check("上传后工具栏显示文件名", got)
    check("上传后行数跟着变",
          p.eval("document.querySelector('.code-lines').innerText") ==
          f"{UPLOAD_SRC.count(chr(10)) + 1} 行",
          p.eval("document.querySelector('.code-lines').innerText"))
    check("上传后编辑器换成文件内容",
          p.eval("document.querySelector('.editor textarea').value") == UPLOAD_SRC)
    v2 = api("/api/lab/verify", {"source": UPLOAD_SRC, "language": "python"}, token=token)
    check("上传的代码能过校验", v2[1].get("ok") is True, json.dumps(v2[1])[:160])

    # 拖拽悬停要出提示（React 的 setState 是异步的，等两帧再看）
    hint = p.eval("""(async () => {
      const tick = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
      const drop = document.querySelector('.code-drop')
      const dt = new DataTransfer()
      drop.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt}))
      await tick()
      const on = !!document.querySelector('.code-drop-hint')
      drop.dispatchEvent(new DragEvent('dragleave', {bubbles: true, cancelable: true, dataTransfer: dt}))
      await tick()
      return JSON.stringify({on, off: !document.querySelector('.code-drop-hint')})
    })()""")
    check("拖拽悬停出提示、移开收起", hint == '{"on":true,"off":true}', hint)

    # 跑一局：按上传的那份代码跑，看棋盘和过程记录都长出来
    clicked = p.eval("""
      (() => {
        const b = [...document.querySelectorAll('.code-actions button')]
          .find(x => /开始对弈/.test(x.textContent));
        if (b) { b.click(); return true; }
        return false;
      })()
    """)
    check("找到开始对弈按钮", bool(clicked))
    for _ in range(30):
        time.sleep(1.0)
        if p.eval("document.querySelectorAll('.lab-moves li').length") > 0:
            break
    rows = p.eval("document.querySelectorAll('.lab-moves li').length")
    check("代码工坊产生对局记录", rows > 0, f"{rows} 手")
    check("棋盘渲染出来", p.eval("!!document.querySelector('.code-run .board-frame canvas')"))
    check("过程行是单行不折",
          p.eval("document.querySelectorAll('.lab-moves li')[0].getBoundingClientRect().height < 40"),
          p.eval("Math.round(document.querySelectorAll('.lab-moves li')[0].getBoundingClientRect().height)"))
    p.shot(str(SHOTS / "10-code-match.png"))

    # ---------------------------------------------------------------- 竞技场
    print("\n[7] 算法竞技场")
    p.eval("location.hash = '#/arena'")
    time.sleep(1.6)
    check("渲染对阵选择器", p.eval("document.querySelectorAll('.picker').length") >= 2,
          f"{p.eval('document.querySelectorAll(\".picker\").length')} 个")

    # 换成神经 vs 贪心，跑得快；这一段要验的是开局那一手和手数统计
    p.eval("""
      (() => {
        const b = [...document.querySelectorAll('.arena-presets .chip')]
          .find(x => /神经 vs 贪心/.test(x.textContent));
        if (b) { b.click(); return true; }
        return false;
      })()
    """)
    time.sleep(0.3)
    clicked = p.eval("""
      (() => {
        const b = [...document.querySelectorAll('.arena-buttons button')]
          .find(x => /开始对局/.test(x.textContent));
        if (b) { b.click(); return true; }
        return false;
      })()
    """)
    check("找到开始对局按钮", bool(clicked))
    for _ in range(80):
        time.sleep(0.15)
        if p.eval("document.querySelectorAll('.arena-moves li').length") >= 1:
            break
    first = p.eval("""
      (() => {
        const li = document.querySelector('.arena-moves li');
        if (!li) return null;
        const q = (s) => li.querySelector(s)?.innerText.trim() || '';
        return [q('.lab-move-n'), q('.lab-move-xy'), q('.lab-move-ms')];
      })()
    """)
    check("落子记录首行是开局手", bool(first) and first[0] == "1", repr(first))
    check("开局手不显示耗时", bool(first) and first[2] == "开局", repr(first))

    # 对局一开跑就停掉：这一帧最能说明问题——记分牌还是 0 手，落子记录第一行写着
    # 「开局」。跑完再截就只能看到最后 12 手，开局那一行早被顶掉了。
    p.eval("""
      (() => {
        const b = [...document.querySelectorAll('.arena-buttons button')]
          .find(x => /中止/.test(x.textContent));
        if (b) { b.click(); return true; }
        return false;
      })()
    """)
    for _ in range(40):
        time.sleep(0.2)
        if not p.eval("!!document.querySelector('.board-overlay')"):
            break
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
           deviceScaleFactor=1, mobile=False)
    time.sleep(0.4)
    # 整页截图：落子记录在 1000 高视口下正好卡在折线以下，只截视口会把它切掉
    p.shot(str(SHOTS / "11-arena.png"), full=True)

    # 再完整跑一局，验手数统计没把开局那一手摊给任何一方
    p.eval("""
      (() => {
        const b = [...document.querySelectorAll('.arena-buttons button')]
          .find(x => /开始对局/.test(x.textContent));
        if (b) { b.click(); return true; }
        return false;
      })()
    """)
    for _ in range(120):
        time.sleep(0.3)
        if p.eval("!!document.querySelector('.lab-result') && !document.querySelector('.board-overlay')"):
            break
    score = p.eval("(() => [...document.querySelectorAll('.score em')].map(e => e.innerText.trim()))()")
    total = p.eval("""
      (() => {
        const t = document.querySelector('.lab-result span')?.innerText || '';
        const m = t.match(/共\\s*(\\d+)\\s*手/);
        return m ? Number(m[1]) : 0;
      })()
    """)
    nums = [int("".join(ch for ch in s.split("·")[0] if ch.isdigit()) or 0) for s in score]
    check("双方手数不含开局那一手", len(nums) == 2 and nums[0] + nums[1] == total - 1,
          f"{score} 共 {total} 手")

    st, body = api("/api/arena/match", {
        "left": {"type": "algorithm", "algorithm": "greedy"},
        "right": {"type": "algorithm", "algorithm": "random"},
        "rule": "renju", "difficulty": 3,
    }, token=token, raw=True)
    text = body.decode("utf-8", "replace")
    check("竞技场 SSE 可用", st == 200 and "event: move" in text or '"type": "move"' in text,
          f"{st} {text[:120]}")

    # ---------------------------------------------------------------- 战绩
    print("\n[8] 战绩与榜单")
    st, me = api("/api/auth/me", token=token)
    user_obj = (me.get("user") or {}) if isinstance(me, dict) else {}
    check("me 返回战绩", st == 200 and "stats" in user_obj, json.dumps(me)[:140])
    check("战绩字段完整", {"games", "wins", "losses", "draws", "win_rate"} <= set(
        (user_obj.get("stats") or {}).keys()), str((user_obj.get("stats") or {}).keys()))
    st, lb = api("/api/leaderboard", token=token)
    check("榜单可用", st == 200, f"{st}")

    p.close()

    print(f"\n{'='*48}\n通过 {ok} 项，失败 {len(fail)} 项")
    for f in fail:
        print("  -", f)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
