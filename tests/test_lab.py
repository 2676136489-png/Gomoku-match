"""沙箱与实验室测试。

不依赖 pytest，直接 `python tests/test_lab.py` 跑。
覆盖静态校验的拦截面、子进程正常对弈、非法落点、超时熔断、C++ 通道探测。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import arena, lab  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def expect_block(name: str, source: str) -> None:
    try:
        lab.validate_python(source)
    except lab.LabError as exc:
        check(name, True)
        print(f"       拦下：{exc.message}")
    else:
        check(name, False, "没有被拦")


def test_validation() -> None:
    print("\n--- 静态校验 ---")
    expect_block("import os", "import os\n\ndef choose_move(b, c, n):\n    return (7, 7)")
    expect_block("from sys import path", "from sys import path\n\ndef choose_move(b, c, n):\n    return (7, 7)")
    expect_block("open()", "def choose_move(b, c, n):\n    return open('x.txt')")
    expect_block("eval()", "def choose_move(b, c, n):\n    return eval('1')")
    expect_block("exec()", "def choose_move(b, c, n):\n    exec('x=1')\n    return (7, 7)")
    expect_block("下划线属性", "def choose_move(b, c, n):\n    return (7, 7).__class__")
    expect_block("下划线名字", "def choose_move(b, c, n):\n    return __builtins__")
    expect_block("getattr", "def choose_move(b, c, n):\n    return getattr(b, 'x')")
    expect_block("缺 choose_move", "x = 1")
    expect_block("语法错误", "def choose_move(b, c, n)\n    return (7, 7)")
    expect_block("空代码", "   ")

    try:
        lab.validate_python(lab.PY_SAMPLE)
        check("自带模板通过校验", True)
    except lab.LabError as exc:
        check("自带模板通过校验", False, exc.message)

    try:
        lab.validate_python("import math\n\ndef choose_move(b, c, n):\n    return (7, 7)")
        check("白名单模块可导入", True)
    except lab.LabError as exc:
        check("白名单模块可导入", False, exc.message)


def test_sandbox_runtime() -> None:
    print("\n--- 子进程执行 ---")
    result = arena.verify({"kind": "code", "language": "python", "source": lab.PY_SAMPLE})
    check("模板能编译并出招", result.get("ok") is True, str(result))

    # 跑满一局，确认每手都合法、事件完整
    events = list(
        arena.run(
            {"kind": "code", "language": "python", "source": lab.PY_SAMPLE},
            {"kind": "algorithm", "algorithm": "greedy"},
            max_seconds=40,
        )
    )
    moves = [e for e in events if e["type"] == "move"]
    end = events[-1]
    check("事件以 end 收尾", end["type"] == "end", str(end))
    check("对局有落子", len(moves) >= 5, f"只有 {len(moves)} 手")
    check("每手都有坐标", all(0 <= m["x"] < 15 and 0 <= m["y"] < 15 for m in moves))
    print(f"       {len(moves)} 手，结束原因：{end.get('reason')}")

    # 返回已占用的点 -> 判负
    bad_move = "def choose_move(board, color, move_no):\n    return (7, 7)\n"
    events = list(
        arena.run(
            {"kind": "code", "language": "python", "source": bad_move},
            {"kind": "algorithm", "algorithm": "greedy"},
            max_seconds=20,
        )
    )
    end = events[-1]
    check("固定走同一点会被判负", end.get("forfeit") == 1, str(end))
    print(f"       原因：{end.get('reason')}")

    # 抛异常 -> 判负，不拖垮服务
    boom = "def choose_move(board, color, move_no):\n    return 1 / 0\n"
    events = list(
        arena.run(
            {"kind": "code", "language": "python", "source": boom},
            {"kind": "algorithm", "algorithm": "greedy"},
            max_seconds=20,
        )
    )
    end = events[-1]
    check("抛异常会被捕获并判负", end.get("forfeit") == 1, str(end))

    # 死循环 -> 超时熔断
    hang = "def choose_move(board, color, move_no):\n    while True:\n        pass\n"
    started = time.perf_counter()
    events = list(
        arena.run(
            {"kind": "code", "language": "python", "source": hang, "timeout": 0.8},
            {"kind": "algorithm", "algorithm": "greedy"},
            max_seconds=20,
        )
    )
    elapsed = time.perf_counter() - started
    end = events[-1]
    check("死循环被超时熔断", end.get("forfeit") == 1, str(end))
    check("熔断耗时受控", elapsed < 8, f"用了 {elapsed:.1f}s")
    print(f"       {elapsed:.1f}s 后结束：{end.get('reason')}")


def test_cpp_channel() -> None:
    print("\n--- C++ 通道 ---")
    caps = lab.capabilities()
    print(f"       能力：{caps}")
    if caps["cpp"]["available"]:
        result = arena.verify({"kind": "code", "language": "cpp", "source": lab.CPP_SAMPLE})
        check("C++ 模板能编译并出招", result.get("ok") is True, str(result))
        check("C++ 编译日志可读", isinstance(result.get("log"), str))
    else:
        # 没有编译器时，必须明确报错而不是静默失败
        try:
            lab.ProcessPlayer("pair<int,int> choose_move(const vector<vector<int>>& b, int c, int n){return {7,7};}", "cpp")
        except lab.LabError as exc:
            check("无编译器时报错明确", "编译器" in exc.message, exc.message)
        else:
            check("无编译器时报错明确", False, "没有报错")

    try:
        lab.validate_cpp('int main(){ system("dir"); }')
    except lab.LabError as exc:
        check("C++ 的 system() 被拦", True)
        print(f"       拦下：{exc.message}")
    else:
        check("C++ 的 system() 被拦", False)


def test_algorithms() -> None:
    print("\n--- 内置算法 ---")
    from server.gomoku import algorithms
    from server.gomoku.position import BLACK, Position, WHITE

    pos = Position()
    pos.place(112, BLACK)
    pos.place(113, WHITE)

    for key in ("random", "greedy", "minimax", "neural"):
        meta = algorithms.ALGORITHMS[key]
        try:
            res = algorithms.choose(key, pos, BLACK, difficulty=1)
            legal = pos.board[res["idx"]] == 0
            check(f"{meta['label']} 出招合法", legal, str(res))
        except Exception as exc:  # noqa: BLE001
            check(f"{meta['label']} 出招合法", False, f"{type(exc).__name__}: {exc}")


def main() -> None:
    test_validation()
    test_sandbox_runtime()
    test_cpp_channel()
    test_algorithms()
    print(f"\n{'=' * 46}\n通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
