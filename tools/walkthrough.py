"""全站端到端走查：大厅 → 登录 → 对弈 → 学习 → 代码工坊 → 竞技场 → 战绩。

每一步都断言关键 DOM 存在，失败立即记下并截图留证。
前端是静态多页：大厅（index.html）是公开的视频落地页，功能页都要先登录。
服务与浏览器都由脚本自己起（一次性库在 .tmp/），跑完自己关，不碰开发库。
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
sys.path.insert(0, str(ROOT))
from tools.cdp import Page, ensure_browser  # noqa: E402
from tools.harness import BASE, WALK_DB, ensure_server  # noqa: E402

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


def pick_empty_spot(p):
    """从局面快照里挑一个空点：AI 的开局手有随机性，写死坐标会撞上它。"""
    return p.eval("""
      (() => {
        const g = window.__gomoku || {};
        const board = g.board || [];
        for (const [x, y] of [[8, 8], [7, 8], [8, 7], [6, 6], [8, 6], [6, 8], [7, 6], [6, 7]]) {
          if (!board[y * 15 + x]) return [x, y];
        }
        return null;
      })()
    """)


def click_board(p, spot):
    """棋盘是 canvas，只能按坐标派发鼠标事件。几何参数和 app.js 的 GomokuBoard 一致。"""
    x, y = spot
    p.eval(f"""
      (() => {{
        const c = document.querySelector('.board-canvas');
        if (!c) return false;
        const r = c.getBoundingClientRect();
        const pad = r.width * 0.055;
        const step = (r.width - pad * 2) / 14;
        c.dispatchEvent(new MouseEvent('click', {{
          clientX: r.left + pad + {x} * step, clientY: r.top + pad + {y} * step, bubbles: true}}));
        return true;
      }})()
    """)


def wait_moves(p, minimum: int, timeout_s: float):
    for _ in range(int(timeout_s / 0.25)):
        n = p.eval("(window.__gomoku && window.__gomoku.moves) ? window.__gomoku.moves.length : 0")
        if n >= minimum:
            return n
        time.sleep(0.25)
    return p.eval("(window.__gomoku && window.__gomoku.moves) ? window.__gomoku.moves.length : 0")


def start_game(p, mode_selector: str | None = None):
    """在准备页（可选对手模式）提交设置，等跳到对局页。"""
    if mode_selector:
        p.eval(f"document.querySelector('{mode_selector}').click()")
        time.sleep(0.3)
    p.eval("document.querySelector('.setup-foot .btn-primary').click()")
    for _ in range(20):
        time.sleep(0.5)
        if "game=" in (p.eval("location.search") or ""):
            break
    time.sleep(1.5)


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    user = f"walk{suffix}"
    pw = "Walk12345"

    server = ensure_server(WALK_DB)
    browser = ensure_browser()
    p = Page.open(BASE + "/")
    p.send("Network.enable")
    p.send("Network.setCacheDisabled", cacheDisabled=True)
    p.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000,
           deviceScaleFactor=1, mobile=False)
    time.sleep(1.8)

    try:
        # ---------------------------------------------------------------- 登录
        # 首页现在有登录门禁，先注册拿到会话，才看得到落地页
        print("\n[1] 登录页")
        p.goto(BASE + "/login.html", wait=1.6)
        check("渲染登录表单", p.eval("!!document.querySelector('.auth-form')"))
        check("登录态两个输入框",
              p.eval("document.querySelectorAll('.auth-form input').length") == 2,
              str(p.eval("document.querySelectorAll('.auth-form input').length")))
        p.shot(str(SHOTS / "02-login.png"))

        # 切到注册 tab
        p.eval("""
          (() => {
            const t = [...document.querySelectorAll('.auth-tab')];
            if (t.length) { t[t.length-1].click(); return 'tab'; }
            const b = [...document.querySelectorAll('button')].find(b => /注册/.test(b.textContent));
            if (b) { b.click(); return 'btn'; }
            return 'none';
          })()
        """)
        time.sleep(0.4)
        n = p.eval("document.querySelectorAll('.auth-form input').length")
        check("注册表单三格", n == 3, f"实际 {n} 格")
        p.shot(str(SHOTS / "02b-register.png"))

        p.eval("""
          (() => {
            const ins = document.querySelectorAll('.auth-form input');
            const set = (el, v) => {
              const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value');
              d.set.call(el, v);
              el.dispatchEvent(new Event('input', {bubbles: true}));
            };
            set(ins[0], '%s'); set(ins[1], '%s');
            if (ins[2]) set(ins[2], '%s');
          })()
        """ % (user, pw, pw))
        p.eval("document.querySelector('.auth-form').requestSubmit()")
        time.sleep(2.2)
        token = p.eval("localStorage.getItem('gomoku.token')")
        err = p.eval("document.querySelector('.banner-error, .auth-error')?.innerText || ''")
        check("注册后拿到 token", bool(token), repr(token) + " err=" + repr(err))
        if not token:
            p.shot(str(SHOTS / "02c-register-fail.png"))
            print("!! 注册失败，后续中止")
            return 1

        # ---------------------------------------------------------------- 大厅
        print("\n[2] 大厅落地页")
        p.goto(BASE + "/index.html", wait=2.5)
        check("登录后落在首页", "index.html" in (p.eval("location.pathname") or ""), p.eval("location.pathname"))
        check("背景视频在", p.eval("!!document.querySelector('.bg-video video, video.bg-video')"))
        check("点阵标题两行", p.eval("document.querySelectorAll('.hl-line').length") == 2)
        check("信任环三个", p.eval("document.querySelectorAll('.trust-ring').length") == 3)
        check("数据脚注四项", p.eval("document.querySelectorAll('.stat').length") == 4)
        check("导航六个功能入口", p.eval("document.querySelectorAll('.nav-pill .nav-link').length") == 6)
        check("退出入口指向 login", p.eval(
            "!!document.querySelector('a[href*=\"login.html\"]')"))
        check("开始按钮指向 play", p.eval(
            "!!document.querySelector('a[href*=\"play.html\"]')"))
        p.shot(str(SHOTS / "01-landing.png"))

        # ---------------------------------------------------------------- 对弈
        print("\n[3] AI 对弈")
        p.goto(BASE + "/play.html", wait=2.0)
        check("准备页三张对手卡", p.eval("document.querySelectorAll('.setup-card').length") == 3)
        check("强度四档", p.eval("document.querySelectorAll('.chip[data-level]').length") == 4)
        check("执子两项", p.eval("document.querySelectorAll('.chip[data-color]').length") == 2)
        before = p.eval("(() => { const el = document.querySelector('.setup-summary-text'); return el ? el.textContent : ''; })()")
        p.eval("document.querySelector('.chip[data-level=\"4\"]').click()")
        time.sleep(0.3)
        after = p.eval("(() => { const el = document.querySelector('.setup-summary-text'); return el ? el.textContent : ''; })()")
        check("摘要跟随强度档更新", before != after, f"{before} → {after}")
        p.shot(str(SHOTS / "03-setup.png"))

        start_game(p)
        check("提交设置后进到棋盘", "game=" in (p.eval("location.search") or ""), p.eval("location.search"))
        check("出现棋盘 canvas", p.eval("!!document.querySelector('.board-canvas')"))
        check("顶栏状态药丸", p.eval("!!document.querySelector('.play-meta .pill')"))

        spot = pick_empty_spot(p)
        if spot:
            click_board(p, spot)
        moves = wait_moves(p, 2, 12)
        check("落子后 AI 有应手", moves >= 2, f"{moves} 手 / 点 {spot}")
        p.shot(str(SHOTS / "04-play-move.png"))

        # ---------------------------------------------------------------- 学习
        print("\n[4] 学习模式")
        p.goto(BASE + "/play.html", wait=2.0)
        start_game(p, mode_selector='[data-mode="learn"]')
        check("学习模式进到棋盘", "game=" in (p.eval("location.search") or ""), p.eval("location.search"))
        spot = pick_empty_spot(p)
        if spot:
            click_board(p, spot)
        wait_moves(p, 2, 12)
        time.sleep(1.0)
        coach_txt = p.eval("document.querySelector('.coach')?.innerText || ''")
        check("教练给出评语", len(coach_txt) > 10, coach_txt[:40])
        p.shot(str(SHOTS / "05-learn-coach.png"))

        # ---------------------------------------------------------------- 代码工坊
        print("\n[5] 代码工坊")
        p.goto(BASE + "/code.html", wait=2.0)
        check("渲染编辑器", p.eval("!!document.querySelector('.code-drop textarea')"))
        check("有拖拽落区", p.eval("!!document.querySelector('.code-drop')"))
        caps = api("/api/lab/capabilities", token=token)[1]
        langs = (caps.get("capabilities") or caps).get("languages", [])
        check("能力探测含 Python 与 C++", {"python", "cpp"} <= set(langs), f"{langs}")
        check("初始标注内置模板",
              p.eval("(document.querySelector('.code-file')||{textContent:''}).textContent.includes('内置模板') === true"))
        p.shot(str(SHOTS / "06-code.png"))

        code = p.eval("document.querySelector('.code-drop textarea').value")
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
            if p.eval("(document.querySelector('.code-file')||{textContent:''}).textContent.includes('walk-corner.py')"):
                got = True
                break
        check("上传后工具栏显示文件名", got)
        check("上传后行数跟着变",
              p.eval("document.querySelector('.code-lines').textContent") ==
              f"{UPLOAD_SRC.count(chr(10)) + 1} 行",
              p.eval("document.querySelector('.code-lines').textContent"))
        check("上传后编辑器换成文件内容",
              p.eval("document.querySelector('.code-drop textarea').value") == UPLOAD_SRC)
        v2 = api("/api/lab/verify", {"source": UPLOAD_SRC, "language": "python"}, token=token)
        check("上传的代码能过校验", v2[1].get("ok") is True, json.dumps(v2[1])[:160])

        # 拖拽悬停要出提示
        hint = p.eval("""(async () => {
          const drop = document.querySelector('.code-drop')
          const dt = new DataTransfer()
          drop.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt}))
          await new Promise((r) => setTimeout(r, 120))
          const on = drop.classList.contains('is-over')
          drop.dispatchEvent(new DragEvent('dragleave', {bubbles: true, cancelable: true, dataTransfer: dt}))
          await new Promise((r) => setTimeout(r, 120))
          return JSON.stringify({on, off: !drop.classList.contains('is-over')})
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
        rows = 0
        for _ in range(30):
            time.sleep(1.0)
            rows = p.eval("document.querySelectorAll('.arena-moves li, .lab-moves li').length")
            if rows > 0:
                break
        check("代码工坊产生对局记录", rows > 0, f"{rows} 手")
        check("棋盘渲染出来", p.eval("!!document.querySelector('.code-run .board-frame canvas')"))
        p.shot(str(SHOTS / "07-code-match.png"))

        # ---------------------------------------------------------------- 竞技场
        print("\n[6] 算法竞技场")
        p.goto(BASE + "/arena.html", wait=1.0)
        # 页面要先拉 /api/arena/config 才渲染，冷启动下 2 秒未必够，轮询等它
        for _ in range(40):
            if p.eval("document.querySelectorAll('.arena-presets .chip').length") >= 4:
                break
            time.sleep(0.25)
        pickers = p.eval("document.querySelectorAll('.picker').length")
        check("渲染对阵选择器", pickers == 2, f"{pickers} 个")
        presets = p.eval("document.querySelectorAll('.arena-presets .chip').length")
        check("预设对阵至少四组", presets >= 4, f"{presets} 组")

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
            const q = (s) => li.querySelector(s)?.textContent.trim() || '';
            return [q('.lab-move-n'), q('.lab-move-xy'), q('.lab-move-ms')];
          })()
        """)
        check("落子记录首行是开局手", bool(first) and first[0] == "1", repr(first))
        check("开局手不显示耗时", bool(first) and first[2] == "开局", repr(first))

        # 对局一开跑就停掉：记分牌还是 0 手，落子记录第一行写着「开局」
        p.eval("""
          (() => {
            const b = [...document.querySelectorAll('.arena-buttons button')]
              .find(x => /中止/.test(x.textContent));
            if (b) { b.click(); return true; }
            return false;
          })()
        """)
        time.sleep(1.0)
        check("记分板两侧就位", p.eval(
            "!!document.querySelector('.scoreboard .score')"))
        p.shot(str(SHOTS / "08-arena.png"), full=True)

        # 再完整跑一局，验手数统计没把开局那一手摊给任何一方。
        # 先切到入门档：困难档每手要两秒多，一整局会拖到八十秒开外。
        p.eval("document.querySelector('.chip[data-level=\"1\"]').click()")
        time.sleep(0.3)
        p.eval("""
          (() => {
            const b = [...document.querySelectorAll('.arena-buttons button')]
              .find(x => /开始对局/.test(x.textContent));
            if (b) { b.click(); return true; }
            return false;
          })()
        """)
        for _ in range(400):
            time.sleep(0.3)
            if p.eval("!!document.querySelector('.lab-result') && document.querySelector('.board-overlay').hidden"):
                break
        score = p.eval("(() => [...document.querySelectorAll('.score em')].map(e => e.textContent.trim()))()")
        total = p.eval("""
          (() => {
            const t = document.querySelector('.lab-result span')?.textContent || '';
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
        print("\n[7] 战绩")
        st, me = api("/api/auth/me", token=token)
        user_obj = (me.get("user") or {}) if isinstance(me, dict) else {}
        check("me 返回战绩", st == 200 and "stats" in user_obj, json.dumps(me)[:140])
        check("战绩字段完整", {"games", "wins", "losses", "draws", "win_rate"} <= set(
            (user_obj.get("stats") or {}).keys()), str((user_obj.get("stats") or {}).keys()))
        history = user_obj.get("history") or []
        check("最近对局带得出结果", all(item.get("result") in {"win", "loss", "draw"} for item in history),
              str(history[:2]))
    finally:
        p.close()
        if browser is not None:
            browser.terminate()
        if server is not None:
            server.terminate()

    print(f"\n{'='*48}\n通过 {ok} 项，失败 {len(fail)} 项")
    for f in fail:
        print("  -", f)
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
