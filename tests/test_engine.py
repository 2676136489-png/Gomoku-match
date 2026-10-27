"""引擎测试。

不依赖 pytest，直接 `python tests/test_engine.py` 就能跑。
覆盖棋形分类、增量分一致性、战术正确性、搜索状态安全、连珠禁手、Botzone 协议。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import botzone
from server.gomoku import BLACK, WHITE, Position, Searcher, coord, rules, shapes
from server.gomoku.position import CELLS

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    if not condition:
        FAILURES.append(name)
    print(f"[{status}] {name}" + (f"  {detail}" if detail else ""))


def idx(x: int, y: int) -> int:
    return y * 15 + x


def window(core: str) -> str:
    """把一段棋形字符串塞进长度 11、中心为己方子的窗口。"""
    pad = 5 - len(core) // 2
    return "2" * pad + core + "2" * (11 - pad - len(core))


# ---------------------------------------------------------------- 棋形分类


def test_shapes() -> None:
    cases = {
        "11111": shapes.FIVE,
        "011110": shapes.OPEN_FOUR,
        "011112": shapes.FOUR,
        "211110": shapes.FOUR,
        "011100": shapes.OPEN_THREE,
        "011010": shapes.OPEN_THREE,
        "001100": shapes.OPEN_TWO,
        "000100": shapes.NONE,
    }
    for core, expect in cases.items():
        got = shapes.rank(window(core))
        check(
            f"棋形 {core} -> {shapes.SHAPE_NAMES[expect]}",
            got == expect,
            f"实际 {shapes.SHAPE_NAMES[got]}",
        )

    # 原 C++ 版的核心 bug：活二(100) + 活三(1000) 在同方向累加成 1100，
    # 会被误判成活三。这里棋形是独立判定的，不该出现这种串味。
    mixed = window("0101100")
    check("同一方向活二+活三不被合并升级", shapes.rank(mixed) <= shapes.OPEN_THREE)


# ---------------------------------------------------------------- 增量状态


def test_incremental() -> None:
    rng = random.Random(20240614)
    pos = Position()
    ok_scores = True
    ok_cands = True
    for step in range(120):
        empties = [i for i in range(CELLS) if pos.board[i] == 0]
        pos.place(rng.choice(empties), BLACK if step % 2 == 0 else WHITE)
        for color in (BLACK, WHITE):
            expected = sum(pos._stone_value(i, color) for i in range(CELLS) if pos.board[i] == color)
            if pos.total[color] != expected:
                ok_scores = False
        expected_cands = {j for j in range(CELLS) if pos.board[j] == 0 and pos.neighbor[j] > 0}
        if pos.cands != expected_cands:
            ok_cands = False
    check("增量总分与全量重算一致", ok_scores)
    check("候选集与邻域一致", ok_cands)

    pos2 = Position()
    for i in (112, 113, 127, 98, 97):
        pos2.place(i, BLACK)
    for _ in range(5):
        pos2.undo()
    check("撤销后棋盘完全还原", pos2.total == [0, 0, 0] and pos2.hash == 0 and not pos2.cands)


# ---------------------------------------------------------------- 战术


def solve(setup: list[tuple[int, int, int]], color: int, limit: float = 2.0) -> int:
    pos = Position()
    for x, y, c in setup:
        pos.place(idx(x, y), c)
    return Searcher(time_limit=limit, max_depth=4).search(pos, color).move


def test_tactics() -> None:
    move = solve([(7, 5, BLACK), (7, 6, BLACK), (7, 7, BLACK), (7, 8, BLACK)], BLACK)
    check("己方四连直接成五", move == idx(7, 9) or move == idx(7, 4), f"选 {coord(move)}")

    move = solve(
        [(7, 4, WHITE), (7, 5, BLACK), (7, 6, BLACK), (7, 7, BLACK), (7, 8, BLACK), (3, 3, WHITE)],
        WHITE,
    )
    check("封堵对手冲四的唯一成五点", move == idx(7, 9), f"选 {coord(move)}")

    # 活四的两个成五点都要被识别出来，这是"堵不住"的判定依据
    wp = shapes.win_points(window("011110"))
    check("活四有两个成五点", wp.bit_count() == 2, f"实际 {wp.bit_count()}")

    # 冲四只有一个成五点，所以能堵
    wp = shapes.win_points(window("211110"))
    check("冲四只有一个成五点", wp.bit_count() == 1, f"实际 {wp.bit_count()}")


# ---------------------------------------------------------------- 连珠禁手


def place(stones: list[tuple[int, int, int]], rule: str = rules.RENJU) -> Position:
    pos = Position(rule)
    for x, y, color in stones:
        pos.place(idx(x, y), color)
    return pos


def test_rules() -> None:
    # 长连：六子
    pos = place([(5, 7, BLACK), (6, 7, BLACK), (8, 7, BLACK), (9, 7, BLACK), (10, 7, BLACK)])
    check("长连判禁手", pos.forbidden(idx(7, 7)) == rules.LONG, str(pos.forbidden(idx(7, 7))))

    # 连五不是禁手
    pos = place([(4, 7, BLACK), (5, 7, BLACK), (6, 7, BLACK), (8, 7, BLACK)])
    check("连五点不是禁手", pos.forbidden(idx(7, 7)) is None)

    # 四四：横竖各一个活四
    pos = place([(5, 7, BLACK), (6, 7, BLACK), (8, 7, BLACK), (7, 4, BLACK), (7, 5, BLACK), (7, 6, BLACK)])
    check("四四判禁手", pos.forbidden(idx(7, 7)) == rules.DOUBLE_FOUR, str(pos.forbidden(idx(7, 7))))

    # 单个活四只能算一个四 —— 数成五点会把它误判成四四
    pos = place([(5, 7, BLACK), (6, 7, BLACK), (9, 7, BLACK)])
    check("单个活四不算四四", pos.forbidden(idx(7, 7)) is None)

    # 三三：横竖各一个活三
    pos = place([(6, 7, BLACK), (8, 7, BLACK), (7, 6, BLACK), (7, 8, BLACK)])
    check("三三判禁手", pos.forbidden(idx(7, 7)) == rules.DOUBLE_THREE, str(pos.forbidden(idx(7, 7))))

    # 四三是合法手（连珠的基本杀法）
    pos = place([(5, 7, BLACK), (6, 7, BLACK), (8, 7, BLACK), (7, 5, BLACK), (7, 6, BLACK)])
    check("四三不是禁手", pos.forbidden(idx(7, 7)) is None)

    # 白棋没有禁手
    pos = place([(6, 7, BLACK), (8, 7, BLACK), (7, 6, BLACK), (7, 8, BLACK)])
    check("白棋不判禁手", pos.forbidden(idx(7, 7), WHITE) is None)

    # 假活三：横向活三的两个补点都是四四禁手，这个三不算，
    # 加上竖向的真活三只有一三，所以本手合法。
    fake = [
        (5, 7, BLACK), (6, 7, BLACK),
        (4, 4, BLACK), (4, 5, BLACK), (4, 6, BLACK),
        (8, 4, BLACK), (8, 5, BLACK), (8, 6, BLACK),
        (7, 5, BLACK), (7, 6, BLACK),
    ]
    pos = place(fake)
    check("假活三不计入三三", pos.forbidden(idx(7, 7)) is None, str(pos.forbidden(idx(7, 7))))

    # 五连优先级高于四四：同时成五判胜，不判禁手
    pos = place(
        [
            (3, 7, BLACK), (4, 7, BLACK), (5, 7, BLACK), (6, 7, BLACK),
            (7, 4, BLACK), (7, 5, BLACK), (7, 6, BLACK),
            (6, 6, BLACK), (5, 5, BLACK),
        ]
    )
    verdict = rules.judge(_with(pos, 7, 7, BLACK), idx(7, 7), BLACK)
    check("五连优先于四四", verdict.winner == BLACK and verdict.kind == rules.FIVE, str(verdict))

    # 长连优先级高于五连：同时成五判负
    pos = place(
        [
            (3, 7, BLACK), (4, 7, BLACK), (5, 7, BLACK), (6, 7, BLACK),
            (7, 2, BLACK), (7, 3, BLACK), (7, 4, BLACK), (7, 5, BLACK), (7, 6, BLACK),
        ]
    )
    board = _with(pos, 7, 7, BLACK)
    verdict = rules.judge(board, idx(7, 7), BLACK)
    check("长连优先于五连", verdict.kind == rules.LONG and verdict.winner == WHITE, str(verdict))

    # 白棋长连算胜
    board = bytearray(225)
    for x in (3, 4, 5, 6, 8, 9):
        board[idx(x, 7)] = WHITE
    board[idx(7, 7)] = WHITE
    verdict = rules.judge(board, idx(7, 7), WHITE)
    check("白棋长连算胜", verdict.winner == WHITE, str(verdict))

    # 自由规则下没有禁手，六子也算胜
    pos = place(
        [(5, 7, BLACK), (6, 7, BLACK), (8, 7, BLACK), (9, 7, BLACK), (10, 7, BLACK)],
        rules.FREESTYLE,
    )
    check("自由规则无禁手", pos.forbidden(idx(7, 7)) is None)
    verdict = rules.judge(_with(pos, 7, 7, BLACK), idx(7, 7), BLACK, rules.FREESTYLE)
    check("自由规则长连算胜", verdict.winner == BLACK, str(verdict))

    # 禁手点集合：三三局面里 (7,7) 必须被标出来
    pos = place([(6, 7, BLACK), (8, 7, BLACK), (7, 6, BLACK), (7, 8, BLACK)])
    banned = {(p["x"], p["y"]) for p in pos.banned_points()}
    check("禁手点集合包含三三点", (7, 7) in banned, str(sorted(banned))[:80])

    # 引擎执黑时不能走禁手点
    pos = place([(6, 7, BLACK), (8, 7, BLACK), (7, 6, BLACK), (7, 8, BLACK), (5, 5, WHITE)])
    move = Searcher(time_limit=1.5, max_depth=4).search(pos, BLACK).move
    check("引擎不会走禁手点", pos.forbidden(move) is None, f"选了 {coord(move)}")


def _with(pos: Position, x: int, y: int, color: int) -> bytearray:
    board = bytearray(pos.board)
    board[idx(x, y)] = color
    return board


# ---------------------------------------------------------------- 搜索安全


def test_search_safety() -> None:
    pos = Position()
    pos.place(idx(7, 7), BLACK)
    searcher = Searcher(time_limit=0.3, max_depth=5)
    leaked = False
    illegal = False
    color = WHITE
    for _ in range(12):
        result = searcher.search(pos, color)
        if len(pos.history) != pos.occupied:
            leaked = True
        if pos.board[result.move] != 0:
            illegal = True
        pos.place(result.move, color)
        color = WHITE if color == BLACK else BLACK
    check("搜索不残留幽灵棋子", not leaked)
    check("搜索不返回非法落点", not illegal)


# ---------------------------------------------------------------- 协议


def test_botzone() -> None:
    opening = botzone.play({"requests": [{"x": -1, "y": -1}], "responses": []})
    check("Botzone 先手返回坐标", "response" in opening and opening["response"]["x"] >= 0, str(opening))

    swap = botzone.play({"requests": [{"x": 7, "y": 7}], "responses": []})
    check("星位首手触发换手", swap["response"] == {"x": -1, "y": -1}, str(swap))

    corner = botzone.play({"requests": [{"x": 1, "y": 1}], "responses": []})
    check("角落首手正常应招", corner["response"]["x"] >= 0, str(corner))

    mid = botzone.play(
        {
            "requests": [{"x": 1, "y": 1}, {"x": 7, "y": 7}],
            "responses": [{"x": 2, "y": 2}],
        }
    )
    check("中盘补招返回合法点", mid["response"]["x"] >= 0, str(mid))


def main() -> int:
    random.seed(20240614)
    test_shapes()
    test_incremental()
    test_tactics()
    test_rules()
    test_search_safety()
    test_botzone()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项失败: {', '.join(FAILURES)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
