"""极简 CDP 驱动：导航 / 求值 / 截图。

用 websocket-client 直连 DevTools 协议，够用不引重量级依赖。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

from websocket import create_connection

BASE = os.environ.get("CDP_BASE", "http://127.0.0.1:9222")

# 环境里挂着 HTTP_PROXY 时，urllib 会把 127.0.0.1 也交给代理，拿到 502。
# DevTools 端点永远在本机，显式绕开代理。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _targets(base: str = BASE):
    with _OPENER.open(base + "/json", timeout=10) as r:
        return json.load(r)


_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/microsoft-edge",
)


def alive(base: str = BASE) -> bool:
    try:
        with _OPENER.open(base + "/json/version", timeout=2) as r:
            r.read(1)
        return True
    except Exception:
        return False


def ensure_browser(base: str = BASE, width: int = 1440, height: int = 1080):
    """CDP 端点没起来就自己拉一个无头浏览器。

    走查脚本原先要求人手开两个浏览器，忘了开就是一堆 WinError 10061。
    这里按端口推导一个独立的临时 profile——两个账号必须落在两个浏览器里，
    否则同一个 localStorage 会互相顶掉 token。
    """
    if alive(base):
        return None
    exe = next((p for p in _CANDIDATES if os.path.exists(p)), None)
    if exe is None:
        raise RuntimeError("没找到 Chrome / Edge，装一个或手动起带 --remote-debugging-port 的实例")
    port = urllib.parse.urlparse(base).port or 9222
    profile = os.path.join(tempfile.gettempdir(), f"gomoku-cdp-{port}")
    proc = subprocess.Popen(
        [
            exe,
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            f"--window-size={width},{height}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        if alive(base):
            return proc
        time.sleep(0.25)
    proc.terminate()
    raise RuntimeError(f"浏览器起来了但 {base} 的 DevTools 端口一直没应答")


def new_page(url: str = "about:blank", base: str = BASE):
    req = urllib.request.Request(
        base + "/json/new?" + urllib.parse.quote(url), method="PUT"
    )
    with _OPENER.open(req, timeout=10) as r:
        return json.load(r)


class Page:
    def __init__(self, ws_url: str, target_id: str, base: str = BASE):
        self.ws = create_connection(ws_url, timeout=40)
        self.id = target_id
        self.base = base
        self.n = 0

    @classmethod
    def open(cls, url: str = "about:blank", base: str = BASE):
        """开一个新标签。

        base 可以指到另一个浏览器实例——验证多用户场景时必须这么做：
        同一个浏览器里所有标签共享 localStorage，两个账号的 token 会互相覆盖，
        第二个标签一登录，第一个就掉线了。
        """
        t = new_page(url, base)
        return cls(t["webSocketDebuggerUrl"], t["id"], base)

    def send(self, method: str, **params):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def goto(self, url: str, wait: float = 1.2):
        self.send("Page.navigate", url=url)
        time.sleep(wait)

    def viewport(self, width: int = 1440, height: int = 900):
        """把视口钉死。

        不钉的话尺寸取决于浏览器窗口——复用了一个残留实例就可能是 756×488，
        截图和响应式断点全跟着变，布局断言会莫名其妙地失败。
        """
        self.send(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=False,
        )

    def eval(self, expr: str):
        r = self.send(
            "Runtime.evaluate",
            expression=expr,
            returnByValue=True,
            awaitPromise=True,
        )
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text"))
        return r.get("result", {}).get("value")

    def shot(self, path: str, full: bool = False):
        params = {"format": "png"}
        if full:
            params["captureBeyondViewport"] = True
        data = self.send("Page.captureScreenshot", **params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    p = Page.open(sys.argv[1] if len(sys.argv) > 1 else "about:blank")
    print(p.eval("document.title"))
    p.close()
