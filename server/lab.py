"""代码实验室：把用户写的 AI 放进隔离进程里跟内置引擎对弈。

三层防护，缺一不可：

1. **静态校验**：AST 层面禁掉 import（白名单除外）、下划线开头的名字与属性、
   eval/exec/open/getattr 这类能绕过限制的入口。这一层挡的是「有意为之」。
2. **独立子进程**：死循环、爆内存、段错误都只损失一个子进程。这一层挡的是
   「无意写错」，也是唯一能真正兜住 C++ 的一层。
3. **每手超时 + 总时长上限**：超时直接 kill。静态校验拦不住 `while True`。

用户代码和父进程之间走一套极简的行协议，Python 和 C++ 共用同一份：
父进程发 `颜色 手数` 加一行 225 个格子值，子进程回 `x y`，出错回 `ERR 说明`。
不用 JSON 是为了让 C++ 侧不需要任何第三方库——一个 `cin >>` 就够了。
"""

from __future__ import annotations

import ast
import math
import os
import queue
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .gomoku import algorithms, rules
from .gomoku.position import BLACK, CELLS, OPPONENT, SIZE, WHITE, Position, coord

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CPP_ARCHIVE = ROOT / "engine" / "bot.cpp"

MAX_SOURCE = 200_000
MAX_NODES = 6000
# zig 第一次编 C++ 会顺手把 libc++ 源码编出来，冷缓存能吃掉半分钟以上；
# 之后就只剩一两秒。上限给宽一点，别让「首次编译」被误判成卡死。
MAX_CPP_BUILD_SECONDS = 90.0

DENY_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__", "globals", "locals",
    "vars", "breakpoint", "exit", "quit", "help", "memoryview", "getattr", "setattr",
    "delattr", "hasattr", "object", "type", "super", "classmethod", "staticmethod",
    "property", "builtins", "importlib", "ctypes", "os", "sys", "subprocess", "socket",
}

ALLOWED_IMPORTS = {
    "math", "random", "collections", "itertools", "heapq", "bisect",
    "functools", "operator", "statistics", "typing", "array",
}

CPP_DENY = [
    (r"\bsystem\s*\(", "system() 调用"),
    (r"\b(?:execl|execlp|execle|execv|execvp|execve)\s*\(", "进程执行"),
    (r"\b(?:fork|popen|waitpid)\s*\(", "进程控制"),
    (r"\b(?:fopen|freopen|remove|rename|unlink|mkdir|rmdir)\s*\(", "文件操作"),
    (r"\b(?:ifstream|ofstream|fstream)\b", "文件流"),
    (r"\b(?:socket|connect|listen|accept|send|recv)\s*\(", "网络调用"),
    (r"#\s*include\s*<(?:unistd|sys/|winsock|windows|direct)\.?h?>", "系统头文件"),
    (r"\b__asm__\b|\basm\s*\(", "内联汇编"),
]


class LabError(Exception):
    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


# ------------------------------------------------------------------ 环境能力


@dataclass
class Capability:
    available: bool
    detail: str
    command: list[str] = field(default_factory=list)


def _zig_cxx() -> list[str]:
    """zig 自带 clang 前端，`zig c++` 就是一个完整的 C++ 编译器。

    只在装了 ziglang 这个可选包时才用，不进依赖清单——有 g++ 就轮不到它。
    """
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return []
    exe = Path(ziglang.__file__).resolve().parent / ("zig.exe" if os.name == "nt" else "zig")
    return [str(exe), "c++"] if exe.exists() else []


def _compiler() -> list[str]:
    """按 CXX → g++ → clang++ → zig c++ 的顺序找编译器。

    CXX 允许带参数（例如 `ccache g++`），所以走 shlex 拆词而不是当成裸路径。
    """
    env = os.environ.get("CXX", "").strip()
    if env:
        try:
            parts = shlex.split(env)
        except ValueError:
            parts = [env]
        if parts:
            return parts
    for name in ("g++", "clang++"):
        found = shutil.which(name)
        if found:
            return [found]
    return _zig_cxx()


def _detect() -> dict[str, Capability]:
    py = Capability(True, f"CPython {sys.version.split()[0]}", [sys.executable])

    compiler = _compiler()
    if compiler:
        try:
            out = subprocess.run(
                [*compiler, "--version"], capture_output=True, text=True, timeout=8
            ).stdout.strip().splitlines()
            cpp = Capability(True, out[0] if out else "C++ 编译器就绪", compiler)
        except (OSError, subprocess.SubprocessError):
            cpp = Capability(False, "编译器存在但无法执行")
    else:
        cpp = Capability(False, "本机未安装 C++ 编译器（g++ / clang++ / zig），C++ 通道不可用")

    return {"python": py, "cpp": cpp}


_CAPS: dict[str, Capability] | None = None


def capabilities() -> dict[str, dict]:
    global _CAPS
    if _CAPS is None:
        _CAPS = _detect()
    return {
        "python": {"available": _CAPS["python"].available, "detail": _CAPS["python"].detail},
        "cpp": {"available": _CAPS["cpp"].available, "detail": _CAPS["cpp"].detail},
        "languages": [k for k, v in _CAPS.items() if v.available],
    }


# ------------------------------------------------------------------ 静态校验


def validate_python(source: str) -> None:
    if not source.strip():
        raise LabError("代码是空的")
    if len(source) > MAX_SOURCE:
        raise LabError(f"代码超过 {MAX_SOURCE} 字符上限")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise LabError(f"语法错误：第 {exc.lineno} 行 {exc.msg}") from exc

    nodes = 0
    for node in ast.walk(tree):
        nodes += 1
        if nodes > MAX_NODES:
            raise LabError(f"代码结构过于复杂（超过 {MAX_NODES} 个语法节点）")

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    raise LabError(f"不允许导入模块 {alias.name}", "只允许 " + "、".join(sorted(ALLOWED_IMPORTS)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise LabError("不允许相对导入")
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                raise LabError(f"不允许导入模块 {node.module}", "只允许 " + "、".join(sorted(ALLOWED_IMPORTS)))
        elif isinstance(node, ast.Name):
            if node.id.startswith("_"):
                raise LabError(f"不允许使用以下划线开头的名字 {node.id}")
            if node.id in DENY_NAMES:
                raise LabError(f"不允许调用 {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise LabError(f"不允许访问以下划线开头的属性 .{node.attr}")
            if node.attr in DENY_NAMES:
                raise LabError(f"不允许调用 .{node.attr}")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in DENY_NAMES:
                raise LabError(f"不允许调用 {fn.id}")

    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if not any(f.name == "choose_move" for f in funcs):
        # 允许把函数定义在 if/class 里，但顶层必须有，否则运行时找不到
        found = any(
            isinstance(n, ast.FunctionDef) and n.name == "choose_move" for n in ast.walk(tree)
        )
        if not found:
            raise LabError("没有找到 choose_move 函数", "入口签名见下方模板")


def validate_cpp(source: str) -> None:
    if not source.strip():
        raise LabError("代码是空的")
    if len(source) > MAX_SOURCE:
        raise LabError(f"代码超过 {MAX_SOURCE} 字符上限")
    # 安全检查放最前面：代码里同时有危险调用和笔误时，先报安全问题
    for pattern, label in CPP_DENY:
        if re.search(pattern, source):
            raise LabError(f"C++ 代码里出现了不允许的{label}")
    if "choose_move" not in source:
        raise LabError("没有找到 choose_move 函数", "入口签名见下方模板")
    if source.count("{") != source.count("}"):
        raise LabError("花括号不配对")


# ------------------------------------------------------------------ 子进程

RUNNER = '''\
"""沙箱入口：由服务端生成，不要改。"""
import sys
import traceback

import user_code

def main():
    choose = getattr(user_code, "choose_move", None)
    if choose is None:
        print("ERR 没有定义 choose_move", flush=True)
        return
    read = sys.stdin.readline
    while True:
        head = read()
        if not head:
            return
        head = head.strip()
        if not head:
            continue
        parts = head.split()
        if len(parts) < 2:
            continue
        color, move_no = int(parts[0]), int(parts[1])
        line = read()
        while line is not None and not line.strip():
            line = read()
        if not line:
            return
        values = [int(v) for v in line.split()]
        if len(values) < 225:
            print("ERR 棋盘数据不完整", flush=True)
            continue
        board = [values[y * 15:(y + 1) * 15] for y in range(15)]
        try:
            x, y = choose(board, color, move_no)
        except Exception:
            print("ERR " + traceback.format_exc(limit=3).replace("\\n", " | "), flush=True)
            continue
        print(f"{int(x)} {int(y)}", flush=True)

main()
'''

# 头部不用 <bits/stdc++.h>——那是 GCC 专有的聚合头，clang + libc++（zig c++、
# macOS 上的 clang++）找不到它。列全反而更稳，也顺便告诉用户能用哪些标准库。
CPP_WRAPPER_HEAD = """#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <random>
#include <set>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std;

// ======================== 用户代码 ========================
"""

CPP_WRAPPER_TAIL = """
// =========================================================

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    int color, moveNo;
    while (cin >> color >> moveNo) {
        vector<int> flat(225);
        for (int i = 0; i < 225; i++) {
            if (!(cin >> flat[i])) return 0;
        }
        vector<vector<int>> board(15, vector<int>(15));
        for (int i = 0; i < 225; i++) board[i / 15][i % 15] = flat[i];
        pair<int, int> p = choose_move(board, color, moveNo);
        cout << p.first << " " << p.second << endl;
    }
    return 0;
}
"""


class _Channel:
    """读子进程 stdout 的后台线程 + 带超时的取行。"""

    def __init__(self, stream) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        self.thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self.thread.start()

    def _pump(self, stream) -> None:
        try:
            for line in stream:
                self.q.put(line)
        except (OSError, ValueError):
            pass
        finally:
            self.q.put(None)

    def readline(self, timeout: float) -> str | None:
        try:
            return self.q.get(timeout=max(timeout, 0.01))
        except queue.Empty:
            raise TimeoutError from None


@dataclass
class MoveOutcome:
    idx: int = -1
    x: int = -1
    y: int = -1
    ms: int = 0
    note: str = ""
    error: str = ""
    ban_fixed: bool = False


class BasePlayer:
    side = "player"

    def choose(self, pos: Position, color: int, move_no: int) -> MoveOutcome:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:
        pass

    def info(self) -> dict:
        return {"side": self.side}


class EnginePlayer(BasePlayer):
    side = "engine"

    def __init__(self, algorithm: str = "minimax", difficulty: int = 3, label: str = "") -> None:
        self.algorithm = algorithm
        self.difficulty = difficulty
        self.label = label or algorithms.ALGORITHMS[algorithm]["label"]

    def choose(self, pos: Position, color: int, move_no: int) -> MoveOutcome:
        started = time.perf_counter()
        try:
            res = algorithms.choose(self.algorithm, pos, color, difficulty=self.difficulty)
        except Exception as exc:  # 内置算法不该出错，但别让它带崩整局
            return MoveOutcome(error=f"引擎异常：{exc}", ms=int((time.perf_counter() - started) * 1000))
        return MoveOutcome(
            idx=res["idx"],
            x=res["x"],
            y=res["y"],
            ms=int((time.perf_counter() - started) * 1000),
            note=res["note"],
        )

    def info(self) -> dict:
        return {
            "side": self.side,
            "label": self.label,
            "algorithm": self.algorithm,
            "difficulty": self.difficulty,
        }


class ProcessPlayer(BasePlayer):
    side = "code"

    def __init__(
        self,
        source: str,
        language: str = "python",
        *,
        label: str = "你的代码",
        timeout: float = 2.0,
        avoid_bans: bool = True,
        workdir: Path | None = None,
    ) -> None:
        self.label = label
        self.language = language
        self.timeout = timeout
        # 用户代码通常没实现禁手判定。默认把这种手改判成贪心推荐的合法点，
        # 并在事件里说明——直接判负太劝退，静默改掉又是在骗人。
        self.avoid_bans = avoid_bans
        self.ban_fixes = 0
        self.workdir = Path(workdir or tempfile.mkdtemp(prefix="gomoku-lab-"))
        self.owns_dir = workdir is None
        self.proc: subprocess.Popen | None = None
        self.channel: _Channel | None = None
        self.stderr_buf: queue.Queue[str] = queue.Queue()
        self.compile_log = ""
        self._stderr_thread: threading.Thread | None = None
        self._prepare(source)

    # -------------------------------------------------------------- 构建

    def _prepare(self, source: str) -> None:
        if self.language == "python":
            validate_python(source)
            (self.workdir / "user_code.py").write_text(source, encoding="utf-8")
            (self.workdir / "runner.py").write_text(RUNNER, encoding="utf-8")
            cmd = [sys.executable, "-u", str(self.workdir / "runner.py")]
        elif self.language == "cpp":
            validate_cpp(source)
            full = CPP_WRAPPER_HEAD + source + CPP_WRAPPER_TAIL
            src = self.workdir / "main.cpp"
            src.write_text(full, encoding="utf-8")
            compiler = _CAPS["cpp"].command if _CAPS else []
            if not compiler:
                raise LabError("本机没有 C++ 编译器，C++ 通道不可用")
            exe = self.workdir / ("main.exe" if os.name == "nt" else "main")
            try:
                build = subprocess.run(
                    [*compiler, "-O2", "-std=c++17", "-pipe", "-o", str(exe), str(src)],
                    capture_output=True,
                    text=True,
                    timeout=MAX_CPP_BUILD_SECONDS,
                    cwd=str(self.workdir),
                )
            except subprocess.TimeoutExpired:
                raise LabError(f"编译超时（超过 {MAX_CPP_BUILD_SECONDS:.0f} 秒）") from None
            self.compile_log = (build.stderr or "")[:6000]
            if build.returncode != 0:
                raise LabError("编译失败", self.compile_log)
            cmd = [str(exe)]
        else:
            raise LabError(f"不支持的语言 {self.language}")

        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(self.workdir),
            creationflags=flags,
        )
        self.channel = _Channel(self.proc.stdout)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        try:
            for line in self.proc.stderr:
                self.stderr_buf.put(line.rstrip())
        except (OSError, ValueError):
            pass

    def tail_stderr(self, limit: int = 4) -> str:
        lines = []
        while True:
            try:
                lines.append(self.stderr_buf.get_nowait())
            except queue.Empty:
                break
        return " / ".join(lines[-limit:])

    # -------------------------------------------------------------- 对弈接口

    def choose(self, pos: Position, color: int, move_no: int) -> MoveOutcome:
        started = time.perf_counter()
        if self.proc is None or self.channel is None or self.proc.poll() is not None:
            return MoveOutcome(error="子进程已经退出")

        payload = " ".join(str(v) for v in pos.board)
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(f"{color} {move_no}\n{payload}\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return MoveOutcome(error="写入子进程失败，进程可能已崩溃")

        try:
            line = self.channel.readline(self.timeout)
        except TimeoutError:
            self.close(force=True)
            return MoveOutcome(
                error=f"第 {move_no} 手超时（超过 {self.timeout:.1f} 秒没有返回）",
                ms=int((time.perf_counter() - started) * 1000),
            )

        elapsed = int((time.perf_counter() - started) * 1000)
        if line is None:
            tail = self.tail_stderr()
            return MoveOutcome(error="子进程意外退出" + (f"：{tail}" if tail else ""), ms=elapsed)

        text = line.strip()
        if text.startswith("ERR"):
            return MoveOutcome(error=text[3:].strip()[:400] or "子进程报错", ms=elapsed)

        parts = text.split()
        if len(parts) < 2:
            return MoveOutcome(error=f"返回值无法解析：{text[:80]!r}", ms=elapsed)
        try:
            x, y = int(parts[0]), int(parts[1])
        except ValueError:
            return MoveOutcome(error=f"返回值不是整数：{text[:80]!r}", ms=elapsed)

        if not (0 <= x < SIZE and 0 <= y < SIZE):
            return MoveOutcome(error=f"落点 ({x},{y}) 越界", ms=elapsed)
        idx = y * SIZE + x
        if pos.board[idx] != 0:
            return MoveOutcome(error=f"落点 ({x},{y}) 已经有棋子了", ms=elapsed)
        if pos.rule == rules.RENJU and color == BLACK:
            reason = pos.forbidden(idx, color)
            if reason:
                label = rules.BAN_LABELS.get(reason, "禁手")
                if not self.avoid_bans:
                    return MoveOutcome(error=f"落点 ({x},{y}) 是{label}", ms=elapsed)
                self.ban_fixes += 1
                fallback = algorithms.choose("greedy", pos, color)
                return MoveOutcome(
                    idx=fallback["idx"],
                    x=fallback["x"],
                    y=fallback["y"],
                    ms=elapsed,
                    note=f"({x},{y}) 是{label}，已自动改走 ({fallback['x']},{fallback['y']})",
                    ban_fixed=True,
                )

        # 耗时走 ms 字段，note 只留给「改判禁手」这类真需要解释的情况
        return MoveOutcome(idx=idx, x=x, y=y, ms=elapsed)

    def close(self, force: bool = False) -> None:
        proc = self.proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                if force:
                    proc.kill()
                else:
                    try:
                        if proc.stdin:
                            proc.stdin.close()
                        proc.wait(timeout=1.0)
                    except (OSError, subprocess.TimeoutExpired):
                        proc.kill()
        except OSError:
            pass
        finally:
            self.proc = None
            if self.owns_dir:
                shutil.rmtree(self.workdir, ignore_errors=True)

    def info(self) -> dict:
        return {
            "side": self.side,
            "label": self.label,
            "language": self.language,
            "timeout": self.timeout,
            "ban_fixes": self.ban_fixes,
        }


# ------------------------------------------------------------------ 对局驱动


# 开局落点的高斯尺度（单位：格）。取 2.6 时天元自身 2.4%，是均匀分布的 5 倍多；
# 三圈内 68%、五圈内 84.8%，剩下的一成多才会甩到外面去。
# 再收紧就不是随机了，再放开就成了「随便下」——五子棋开局下到边角等于白让一先。
OPENING_SIGMA = 2.6


def opening_point(rng: random.Random | None = None) -> int:
    """中心加权的随机开局点。

    双方都是确定性算法时，同一个开局意味着同一盘棋——点一次「开始对局」和点十次
    看到的是一模一样的回放。让第一手随机，后面的每一步才真的分岔。
    """
    picker = rng or random
    center = SIZE // 2
    weights = [
        math.exp(
            -(((i % SIZE) - center) ** 2 + ((i // SIZE) - center) ** 2)
            / (2 * OPENING_SIGMA * OPENING_SIGMA)
        )
        for i in range(CELLS)
    ]
    return picker.choices(range(CELLS), weights=weights, k=1)[0]


def play(
    left: BasePlayer,
    right: BasePlayer,
    *,
    rule: str = rules.RENJU,
    max_moves: int = 225,
    max_seconds: float = 90.0,
    opening: str | None = "center",
) -> Iterator[dict]:
    pos = Position(rule)
    players = {BLACK: left, WHITE: right}
    started = time.perf_counter()

    yield {
        "type": "start",
        "rule": rule,
        "left": left.info(),
        "right": right.info(),
        "max_moves": max_moves,
        "opening": opening,
    }

    try:
        first = 1
        # 只随机第一手：黑棋的开局点由中心加权抽样决定，不交给算法。
        # 从第二手开始双方照常算，于是每局从这里分岔出不同的棋。
        if opening == "center":
            idx = opening_point()
            x, y = coord(idx)
            pos.place(idx, BLACK)
            first = 2
            yield {
                "type": "move",
                "n": 1,
                "color": BLACK,
                "x": x,
                "y": y,
                "ms": 0,
                "note": "随机开局（中心加权，每局不同）",
                "ban_fixed": False,
                "eval": pos.evaluate(BLACK),
                "player": left.info().get("label", "?"),
                "win_kind": None,
                "opening": True,
            }

        for move_no in range(first, max_moves + 1):
            if time.perf_counter() - started > max_seconds:
                yield {"type": "end", "winner": None, "reason": f"达到 {max_seconds:.0f} 秒时限，判和", "moves": move_no - 1}
                return

            color = BLACK if move_no % 2 == 1 else WHITE
            player = players[color]
            outcome = player.choose(pos, color, move_no)

            if outcome.error:
                yield {
                    "type": "end",
                    "winner": OPPONENT[color],
                    "forfeit": color,
                    "reason": f"{player.info().get('label', '该方')} 落子失败：{outcome.error}",
                    "moves": move_no - 1,
                }
                return

            pos.place(outcome.idx, color)
            kind = pos.win_kind(outcome.idx, color)
            yield {
                "type": "move",
                "n": move_no,
                "color": color,
                "x": outcome.x,
                "y": outcome.y,
                "ms": outcome.ms,
                "note": outcome.note,
                "ban_fixed": outcome.ban_fixed,
                "eval": pos.evaluate(BLACK),
                "player": player.info().get("label", "?"),
                "win_kind": kind,
            }

            if kind == rules.FIVE:
                yield {"type": "end", "winner": color, "reason": "连成五子", "moves": move_no}
                return
            if kind == rules.LONG:
                if color == WHITE or rule != rules.RENJU:
                    yield {"type": "end", "winner": color, "reason": "长连（白棋或自由规则下算胜）", "moves": move_no}
                else:
                    yield {"type": "end", "winner": WHITE, "reason": "黑棋长连禁手，判负", "moves": move_no}
                return
            if pos.occupied == CELLS:
                yield {"type": "end", "winner": None, "reason": "棋盘已满，和棋", "moves": move_no}
                return
    finally:
        left.close()
        right.close()

    yield {"type": "end", "winner": None, "reason": f"达到 {max_moves} 手上限，判和", "moves": max_moves}


# ------------------------------------------------------------------ 示例代码

PY_SAMPLE = '''\
"""最简单的贪心：只算一步棋形分，不看对手应手。
改它，或者整段删掉重写——只要保留 choose_move 就行。"""

# board[y][x]：0 空、1 黑、2 白
# color：你执的颜色；move_no：第几手，从 1 开始
# 返回 (x, y)，必须是空点，连珠规则下黑棋不能走禁手点

DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))


def line_score(board, x, y, color):
    """假设 (x, y) 落 color 子，四个方向的连子数与活端带来的粗估分。"""
    total = 0
    for dx, dy in DIRS:
        run = 1
        ends = 0
        for sign in (1, -1):
            k = 1
            while True:
                nx, ny = x + dx * k * sign, y + dy * k * sign
                if not (0 <= nx < 15 and 0 <= ny < 15):
                    break
                if board[ny][nx] != color:
                    break
                run += 1
                k += 1
            if 0 <= nx < 15 and 0 <= ny < 15 and board[ny][nx] == 0:
                ends += 1
        if run >= 5:
            total += 10_000_000
        elif run == 4 and ends == 2:
            total += 300_000
        elif run == 4:
            total += 15_000
        elif run == 3 and ends == 2:
            total += 1_500
        elif run == 3:
            total += 350
        elif run == 2 and ends == 2:
            total += 100
    return total


def choose_move(board, color, move_no):
    opp = 3 - color
    best, best_score = None, -1
    for y in range(15):
        for x in range(15):
            if board[y][x] != 0:
                continue
            if abs(x - 7) > 6 or abs(y - 7) > 6:
                continue
            attack = line_score(board, x, y, color)
            defence = line_score(board, x, y, opp)
            score = attack + int(defence * 0.9)
            score += 6 - abs(x - 7) - abs(y - 7)
            if score > best_score:
                best, best_score = (x, y), score
    return best if best else (7, 7)
'''

CPP_SAMPLE = '''\
// 用 C++ 写的话，只写 choose_move 这个函数，前后框架由服务端生成。
// board[y][x]：0 空、1 黑、2 白。返回落点 (x, y)。

const int DX[4] = {1, 0, 1, 1};
const int DY[4] = {0, 1, 1, -1};

int lineScore(const vector<vector<int>>& board, int x, int y, int color) {
    int total = 0;
    for (int d = 0; d < 4; d++) {
        int run = 1, ends = 0;
        for (int sign = -1; sign <= 1; sign += 2) {
            int k = 1;
            while (true) {
                int nx = x + DX[d] * k * sign, ny = y + DY[d] * k * sign;
                if (nx < 0 || nx >= 15 || ny < 0 || ny >= 15) break;
                if (board[ny][nx] != color) break;
                run++;
                k++;
            }
            int nx = x + DX[d] * k * sign, ny = y + DY[d] * k * sign;
            if (nx >= 0 && nx < 15 && ny >= 0 && ny < 15 && board[ny][nx] == 0) ends++;
        }
        if (run >= 5) total += 10000000;
        else if (run == 4 && ends == 2) total += 300000;
        else if (run == 4) total += 15000;
        else if (run == 3 && ends == 2) total += 1500;
        else if (run == 3) total += 350;
        else if (run == 2 && ends == 2) total += 100;
    }
    return total;
}

// 防守按九折算——抢自己的点略优于堵对方同样的点，避免整局被动。
inline int defendSafely(int v) { return (int)(v * 0.9); }

pair<int, int> choose_move(const vector<vector<int>>& board, int color, int moveNo) {
    int opp = 3 - color;
    int bestScore = -1;
    pair<int, int> best = {7, 7};
    for (int y = 0; y < 15; y++) {
        for (int x = 0; x < 15; x++) {
            if (board[y][x] != 0) continue;
            if (abs(x - 7) > 6 || abs(y - 7) > 6) continue;
            int attack = lineScore(board, x, y, color);
            int defence = lineScore(board, x, y, opp);
            int score = attack + defendSafely(defence) + (6 - abs(x - 7) - abs(y - 7));
            if (score > bestScore) {
                bestScore = score;
                best = {x, y};
            }
        }
    }
    return best;
}
'''

LINE_PROTOCOL = """\
父进程每手发两行，子进程回一行：

    2 7
    0 0 0 ... （225 个格子值，空格分隔）
    → 7 7

第一行是「你的颜色 手数」，第二行按 y*15+x 顺序列出全盘。返回值是 x y。
出错回 `ERR 说明`，这一方直接判负。
"""


def samples() -> dict:
    archive = ""
    if SAMPLE_CPP_ARCHIVE.exists():
        try:
            archive = SAMPLE_CPP_ARCHIVE.read_text(encoding="utf-8")
        except OSError:
            archive = ""
    return {
        "python": {"title": "贪心模板（Python）", "code": PY_SAMPLE, "runnable": True},
        "cpp": {"title": "贪心模板（C++）", "code": CPP_SAMPLE, "runnable": capabilities()["cpp"]["available"]},
        "archive": {
            "title": "早期 Botzone 版本（存档，不可直接运行）",
            "code": archive,
            "runnable": False,
            "note": "这是最早提交到 Botzone 的单文件版本，走的是 Botzone 的 requests/responses 协议，"
                    "入口是 main 不是 choose_move。留在这里是为了对照：同一套棋形评估，从单文件比赛 bot 走到了现在的服务。",
        },
        "protocol": LINE_PROTOCOL,
    }
