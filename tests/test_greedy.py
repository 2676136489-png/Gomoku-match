"""可学习贪心与棋谱库测试。不需要起服务，直接跑：

    python tests/test_greedy.py

覆盖四件事：棋谱解析三种写法、棋谱库增删与持久化、参数投影的领域约束、
以及「训练之后模型确实变准了」——最后这条是整页的存在理由，其余都是它的前提。

数据目录顶到临时目录再导入 server，否则跑一次测试就会把开发时的模型覆盖掉。
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import server.* 之前：paths.DATA_DIR 是导入时求值的
_TMP = Path(tempfile.mkdtemp(prefix="gomoku-greedy-"))
os.environ["GOMOKU_DATA"] = str(_TMP)
os.environ["GOMOKU_DB"] = str(_TMP / "greedy.db")

from server import corpus  # noqa: E402
from server.gomoku import algorithms, rules, tuning  # noqa: E402
from server.gomoku.position import BLACK, WHITE, Position  # noqa: E402

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


# 一盘最短的合法棋：黑在 H8..L8 连五，白在 A1..D1 陪着下
BLACK_FIVE = [[7, 7], [0, 0], [8, 7], [1, 0], [9, 7], [2, 0], [10, 7], [3, 0], [11, 7]]


def sgf_of(moves: list[list[int]]) -> str:
    """把落子序列写成 SGF，坐标 a..o。"""
    parts = ["(;GM[1]FF[4]SZ[15]"]
    for i, (x, y) in enumerate(moves):
        tag = "B" if i % 2 == 0 else "W"
        parts.append(f";{tag}[{chr(97 + x)}{chr(97 + y)}]")
    parts.append(")")
    return "".join(parts)


def text_of(moves: list[list[int]]) -> str:
    """人类坐标写法：1. H8 2. A1 …"""
    rows = []
    for i, (x, y) in enumerate(moves):
        rows.append(f"{i + 1}. {chr(65 + x)}{y + 1}")
    return "\n".join(rows)


# --------------------------------------------------------------------- 解析


def test_parse() -> None:
    print("\n--- 棋谱解析 ---")

    parsed = corpus.parse(sgf_of(BLACK_FIVE))
    check("SGF 解析出手数", parsed["plies"] == 9, str(parsed["plies"]))
    check("SGF 坐标对得上", parsed["moves"][0] == [7, 7], str(parsed["moves"][:2]))
    check("连五之后判出胜负", parsed["winner"] == 1, str(parsed))

    parsed = corpus.parse(text_of(BLACK_FIVE))
    check("人类坐标解析出来", parsed["plies"] == 9, str(parsed["plies"]))
    check("字母列号与数字行号对应", parsed["moves"][1] == [0, 0], str(parsed["moves"][:3]))

    parsed = corpus.parse(json.dumps(BLACK_FIVE))
    check("JSON 数组解析出来", parsed["plies"] == 9)

    parsed = corpus.parse(json.dumps({"moves": [{"x": 7, "y": 7}] * 1 + BLACK_FIVE[1:]}))
    check("JSON 对象里的 moves 也认", parsed["plies"] == 9 and parsed["moves"][0] == [7, 7])

    # 逗号分隔纯数字：最容易从别处粘过来的格式
    nums = "\n".join(f"{x},{y}" for x, y in BLACK_FIVE)
    check("纯数字坐标也认", corpus.parse(nums)["plies"] == 9)

    # 连五之后的手要被砍掉：棋谱里常有「认输前多点的几手」
    long_sgf = sgf_of(BLACK_FIVE + [[5, 5], [6, 6]])
    parsed = corpus.parse(long_sgf)
    check("连五之后的手被忽略", parsed["plies"] == 9, str(parsed["plies"]))

    for text, label in (
        ("", "空内容"),
        ("(;GM[1]SZ[15];B[hh];W[hh])", "同一格落两次"),
        ("(;GM[1];B[hh];W[hh];B[ii])", "只有三手"),
        ("这里没有坐标", "没有坐标"),
        ("[7, 7]", "只有一手"),
        ("(;B[zz])", "坐标越界"),
    ):
        try:
            corpus.parse(text)
            check(f"坏棋谱被拒：{label}", False)
        except corpus.CorpusError:
            check(f"坏棋谱被拒：{label}", True)

    check("超大文本被拒", _too_big())


def _too_big() -> bool:
    try:
        corpus.parse("x" * (corpus.MAX_TEXT + 1))
        return False
    except corpus.CorpusError:
        return True


# --------------------------------------------------------------------- 棋谱库


def test_store() -> None:
    print("\n--- 棋谱库 ---")
    corpus.refresh()
    base = corpus.summary()
    check("内置棋谱已就位", base["builtin"] >= 5, str(base))
    check("内置棋谱合计手数可观", base["plies"] >= 200, str(base))
    check("初始没有上传", base["upload"] == 0, str(base))

    item = corpus.add("测试棋谱", sgf_of(BLACK_FIVE), author="走查")
    check("上传后进库", corpus.summary()["total"] == base["total"] + 1)
    check("上传标记为 upload", item["source"] == "upload", item["source"])
    check("作者记下来了", item["author"] == "走查")
    # 备注只说胜负和手段，不重复手数——列表里本来就有一列写着
    check("备标注了胜负", "胜" in item["note"], item["note"])
    check("备注不重复手数", "手" not in item["note"], item["note"])

    again = corpus.get(item["id"])
    check("按 id 取得回来", again is not None and again["moves"] == item["moves"])
    check("取不存在的返回 None", corpus.get("nope") is None)

    # 落盘：清掉缓存重新读盘，模拟进程重启
    corpus.refresh()
    check("重启后还在", any(i["id"] == item["id"] for i in corpus.items()))
    check("持久化文件落在数据目录", corpus.USER_FILE.parent == _TMP, str(corpus.USER_FILE))
    check("持久化文件是合法 JSON", json.loads(corpus.USER_FILE.read_text(encoding="utf-8"))["version"] == 1)

    builtin = next(i for i in corpus.items() if i["source"] == "builtin")
    try:
        corpus.delete(builtin["id"])
        check("内置棋谱不可删", False)
    except corpus.CorpusError:
        check("内置棋谱不可删", True)

    check("删除上传的棋谱", corpus.delete(item["id"]) is True)
    check("删完就查不到", corpus.get(item["id"]) is None)
    check("删完回到原数量", corpus.summary()["total"] == base["total"])
    check("删不存在的返回 False", corpus.delete("nope") is False)

    preview = next(i for i in corpus.items(preview=5) if i["plies"] > 5)
    check("预览只给前几手", len(preview["moves"]) == 5, str(len(preview["moves"])))
    full = corpus.get(preview["id"])
    check("完整取回是全量", len(full["moves"]) == full["plies"])

    trainable = corpus.training_set()
    check("训练集只收长棋谱", all(len(r["moves"]) >= 6 for r in trainable), str(len(trainable)))
    check("训练集是深拷贝", trainable[0] is not corpus.training_set()[0])


# --------------------------------------------------------------------- 参数


def test_params() -> None:
    print("\n--- 分值表参数 ---")
    p = tuning.baseline_params()
    check("参数个数对得上", len(p) == tuning.PARAMS, str(len(p)))
    check("初始防守系数是 0.9", abs(tuning._alpha(p) - 0.9) < 1e-6, str(tuning._alpha(p)))
    check("空棋形权重为 0", p[tuning.W_OWN] == 0.0)

    # 投影：把权重排成倒序，投影之后必须变成非递减
    broken = list(p)
    for r in range(1, tuning.RANKS):
        broken[tuning.W_OWN + r] = 1.0 - r * 0.1
    broken[tuning.W_EXTRA] = -3.0
    broken[tuning.W_ALPHA] = 99.0
    tuning._project(broken)
    own = [broken[tuning.W_OWN + r] for r in range(1, tuning.RANKS)]
    check("投影后分值随棋形单调递增", all(a <= b for a, b in zip(own, own[1:])), str(own))
    check("投影后辅助项非负", broken[tuning.W_EXTRA] >= 0 and broken[tuning.W_EXTRA + 1] >= 0)
    check("投影后 α 收回区间", tuning.ALPHA_MIN <= tuning._alpha(broken) <= tuning.ALPHA_MAX,
          str(tuning._alpha(broken)))

    model = tuning.Model(path=_TMP / "scratch-model.json")
    table = model.table()
    check("表里八档棋形", len(table) == tuning.RANKS, str(len(table)))
    check("手写值来自 WEIGHT", table[1]["hand"] == tuning.WEIGHT[1], str(table[1]))
    # 未训练时「学到」这一列必须原样还原成手写表，否则对照表从第一眼就是假的
    check("未训练时学到的等于手写", all(
        abs(row["learned"] - round(row["hand"], 1)) < 0.11 for row in table
    ), str([(r["label"], r["hand"], r["learned"]) for r in table]))

    # 落盘 / 读取 / 还原
    model.w = [v + 0.01 for v in model.w]
    model.meta = {"summary": "测试"}
    model.trained = True
    model.save()
    reloaded = tuning.Model(path=_TMP / "scratch-model.json")
    check("权重能存能读", reloaded.trained and abs(reloaded.w[5] - model.w[5]) < 1e-5, str(reloaded.w[5]))
    check("meta 一起存", reloaded.meta.get("summary") == "测试")
    reloaded.reset()
    check("还原回到手写", abs(reloaded.alpha - 0.9) < 1e-6 and reloaded.trained is False)
    check("还原会删掉文件", not (_TMP / "scratch-model.json").exists())

    # 特征与打分
    pos = Position(rules.RENJU)
    pos.place(7 * 15 + 7, BLACK)
    feats = tuning.features(pos, 8 * 15 + 7, WHITE)
    check("特征维度正确", len(feats) == tuning.FEATURES, str(len(feats)))
    check("特征都在 [0,1] 内", all(0.0 <= v <= 1.0 for v in feats), str([round(v, 2) for v in feats]))

    ranked = model.ranked(pos, WHITE)
    check("排序给出候选", len(ranked) >= 8, str(len(ranked)))
    check("排序按分值降序", all(a[0] >= b[0] for a, b in zip(ranked, ranked[1:])))

    idx, note = model.choose(pos, WHITE)
    check("选点落在空位", pos.board[idx] == 0, str(idx))
    check("选点带一句说明", bool(note), note)


def test_choose_legal() -> None:
    print("\n--- 学习型贪心的合法性 ---")
    pos = Position(rules.RENJU)
    # 摆一个黑棋几乎连五的局面，逼它去算禁手
    for x in range(3, 7):
        pos.place(7 * 15 + x, BLACK)
    for x in range(3, 7):
        pos.place(8 * 15 + x, WHITE)

    picked = algorithms.choose("tuned", pos, BLACK, difficulty=3)
    check("算法入口认得 tuned", picked["algorithm"] == "tuned", str(picked))
    check("落点是空的", pos.board[picked["y"] * 15 + picked["x"]] == 0, str(picked))
    check("黑棋不会选禁手", not pos.forbidden(picked["y"] * 15 + picked["x"], BLACK), str(picked))

    desc = {a["key"]: a for a in algorithms.describe()}
    check("五路算法都在", set(desc) >= {"random", "greedy", "tuned", "minimax", "neural"}, str(list(desc)))
    check("tuned 自报参数量", desc["tuned"]["params"] == tuning.PARAMS, str(desc["tuned"].get("params")))


# --------------------------------------------------------------------- 训练


def test_train() -> None:
    print("\n--- 从棋谱训练 ---")
    records = corpus.training_set()
    check("有可训练的棋谱", len(records) >= 5, str(len(records)))

    samples = tuning.build_samples(records)
    check("构建出样本", len(samples) >= 40, str(len(samples)))
    check("每条样本都有合法目标", all(
        0 <= s["target"] < len(s["feats"]) for s in samples
    ))
    check("样本宽度不超过设定", all(len(s["feats"]) <= tuning.WIDTH for s in samples))

    model = tuning.get_model()
    model.reset()
    before = tuning.evaluate(tuning.baseline_params(), samples)

    events: list[dict] = []
    result = tuning.train(records, epochs=8, on_event=events.append)

    types = [e["type"] for e in events]
    check("训练过程有阶段事件", types.count("stage") >= 2, str(types))
    check("每轮都上报", types.count("epoch") == 8, str(types.count("epoch")))
    check("曲线长度等于轮数", len(result["curve"]) == 8, str(len(result["curve"])))
    check("损失在下降", result["curve"][-1]["loss"] < result["curve"][0]["loss"],
          str([p["loss"] for p in result["curve"][:3]] + [result["curve"][-1]["loss"]]))

    after = result["eval"]
    check("训练后模型标记为已训练", model.trained is True)
    check("验证集比训练前更准", after["top1"] >= before["top1"],
          f"{before['top1']} → {after['top1']}")
    check("命中率真的上去了", after["top1"] > 0.35, f"top1={after['top1']}")
    check("平均排名下降", after["rank"] <= before["rank"], f"{before['rank']} → {after['rank']}")
    check("指标带样本量", after["n"] > 0 and after["n"] < len(samples), str(after))
    check("结果是可序列化的", json.dumps(result)[:1] == "{")

    saved = json.loads(tuning.MODEL_FILE.read_text(encoding="utf-8"))
    check("模型写到了数据目录", tuning.MODEL_FILE.parent == _TMP, str(tuning.MODEL_FILE))
    check("落盘权重个数正确", len(saved["w"]) == tuning.PARAMS, str(len(saved["w"])))

    # 训练之后的分值表仍然满足领域约束
    table = model.table()
    own = [row["weight"] for row in table]
    check("学到的权重依然单调", all(a <= b + 1e-9 for a, b in zip(own, own[1:])), str(own))
    check("空棋形仍然是 0", table[0]["weight"] == 0.0, str(table[0]))
    check("表里带变化量", any(abs(row["delta"]) > 1e-4 for row in table[1:]),
          str([round(r["delta"], 3) for r in table]))

    desc = model.describe()
    check("描述里有训练时间", isinstance(desc.get("seconds"), (int, float)), str(desc.get("seconds")))
    check("描述里记了盘数", desc.get("games") == len([r for r in records if len(r["moves"]) >= 6]),
          str(desc.get("games")))
    check("描述带评估结果", desc.get("eval", {}).get("top1") == after["top1"], str(desc.get("eval")))

    # 棋谱太少时要给出人话，而不是抛一个除零
    try:
        tuning.train([], epochs=1)
        check("样本不足时报错", False)
    except ValueError as exc:
        check("样本不足时报错", "样本太少" in str(exc), str(exc))


def main() -> None:
    print(f"数据目录：{_TMP}")
    test_parse()
    test_store()
    test_params()
    test_choose_legal()
    test_train()

    print("\n" + "=" * 46)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
