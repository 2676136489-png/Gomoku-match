"""一把跑完全部测试。

    python tools/run_tests.py            # 全跑
    python tools/run_tests.py --fast     # 跳过端到端（只跑引擎、沙箱、分析、存储）

端到端那两套需要一个真在跑的服务。这里自己起一个：临时数据库、独立端口，
跑完杀掉。这样 CI 和本地是同一条命令，不会出现「在我机器上过了」。

退出码是失败套数——CI 只认非零。
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SUITES = (
    ("引擎", "tests/test_engine.py", False),
    ("沙箱与实验室", "tests/test_lab.py", False),
    ("分析层", "tests/test_insight.py", False),
    ("存储与等级分", "tests/test_storage.py", False),
    ("接口", "tests/test_api.py", True),
    ("在线对战", "tests/test_online.py", True),
)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_ready(base: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/api/health", timeout=2) as res:
                if res.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.4)
    return False


def run(cmd: list[str], env: dict[str, str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="跳过需要服务的端到端套件")
    parser.add_argument("--keep", action="store_true", help="跑完保留临时数据库，便于排查")
    args = parser.parse_args()

    suites = [item for item in SUITES if not (args.fast and item[2])]

    tmpdir = Path(tempfile.mkdtemp(prefix="gomoku-test-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "PORT": str(port),
        "GOMOKU_DB": str(tmpdir / "test.db"),
        "GOMOKU_LOG": "WARNING",
        "GOMOKU_TEST_BASE": base,
        "GOMOKU_TEST_WS": base.replace("http://", "ws://"),
        # 本机可能挂着 HTTP_PROXY，别让测试流量走到代理上
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }

    server = None
    failures = 0
    try:
        print(f"启动测试服务 :{port}  (db={tmpdir / 'test.db'})")
        server = subprocess.Popen(
            [sys.executable, "serve.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        if not wait_ready(base):
            print("服务没起来，端到端套件跳过")
            suites = [s for s in suites if not s[2]]

        total_pass = total_fail = 0
        for label, path, _needs in suites:
            started = time.time()
            code, output = run([sys.executable, path], env)
            elapsed = time.time() - started
            passed = output.count("[PASS]")
            failed = output.count("[FAIL]")
            total_pass += passed
            total_fail += failed
            mark = "OK " if code == 0 else "FAIL"
            print(f"  [{mark}] {label:<12} {passed:>3} 通过 {failed:>2} 失败   {elapsed:5.1f}s")
            if code != 0:
                failures += 1
                tail = "\n".join(line for line in output.splitlines() if "[FAIL]" in line or "Error" in line)
                if tail:
                    print("        " + tail.replace("\n", "\n        "))

        print(f"\n合计 {total_pass} 项通过，{total_fail} 项失败，{failures} 个套件未过")
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if not args.keep:
            shutil.rmtree(tmpdir, ignore_errors=True)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
