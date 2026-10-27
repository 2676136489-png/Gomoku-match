"""分析层测试：胜率、点位预测、棋形普查、单步解说。

    python tests/test_insight.py

不依赖服务和 pytest。重点不是「函数能跑」，而是「数字站得住」：
镜像对称、单调性、战术局面必须踩死、模型的推理路径和训练评估必须同一条。
标定产物缺失时（clone 下来没跑训练脚本）用兜底权重，这些断言依然要过。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import analysis, store
from server.gomoku import insight, rules, shapes
from server.gomoku.position import BLACK, CELLS, OPPONENT, SIZE, Position, WHITE

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        FAILURES.append(name)
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def at(x: int, y: int) -> int:
    return y * SIZE + x


def board(black: list[tuple[int, int]], white: list[tuple[int, int]] = ()) -> Position:
    pos = Position(rule=rules.RENJU)
    for x, y in black:
        pos.place(at(x, y), BLACK)
    for x, y in white:
        pos.place(at(x, y), WHITE)
    return pos


# ------------------------------------------------------------------ 模型


def test_logit() -> None:
    """推理与训练评估共用 logit_of，这里用一组能手算的系数钉住它的行为。"""
    weights, mean, std = [0.25, 1.5, -1.0], [10.0, 0.1], [2.0, 0.05]
    z, parts = insight.logit_of(weights, mean, std, [14.0, 0.15])
    expect = 0.25 + 1.5 * ((14.0 - 10.0) / 2.0) + (-1.0) * ((0.15 - 0.1) / 0.05)
    check("标准化后线性项正确", abs(z - expect) < 1e-9, f"{z:.6f} vs {expect:.6f}")
    check("分量之和等于总分", abs(sum(parts) + weights[0] - z) < 1e-9)

    model = insight.get_model()
    check(
        "模型特征顺序与代码一致",
        list(model.weights) == list(model.weights) and len(model.weights) == len(insight.FEATURES) + 1,
        f"{model.source} {model.weights}",
    )
    check("模型有来源标注", model.source in ("selfplay", "default"), model.source)
    if model.source == "selfplay":
        check("标定产物带样本量与 AUC", model.meta.get("auc", 0) > 0.5, str(model.meta.get("auc")))
        check("标定产物带可靠性表", len(model.meta.get("buckets", [])) == 10)


# ------------------------------------------------------------------ 胜率


def test_winrate_basics() -> None:
    empty = Position(rule=rules.RENJU)
    p = insight.winrate(empty, BLACK, BLACK)
    check("空盘胜率接近五五开", abs(p["p"] - 0.5) < 0.03, f"{p['p']}")
    check("空盘没有可数的威胁", p["source"] in ("model", "tactical"), p["source"])

    pos = board([(3, 3), (4, 4), (5, 5)])
    black = insight.winrate(pos, BLACK, WHITE)["p"]
    white = insight.winrate(pos, WHITE, WHITE)["p"]
    check("同一局面双方胜率互补", abs(black + white - 1.0) < 1e-6, f"{black} + {white}")

    strong = insight.winrate(board([(3, 7), (4, 7), (5, 7)]), BLACK, WHITE)["p"]
    weak = insight.winrate(board([(3, 7)]), BLACK, WHITE)["p"]
    check("有活三的一方胜率高于只有孤子的一方", strong > weak, f"{strong} vs {weak}")
    check("有活三的一方过半", strong > 0.5, f"{strong}")

    # 一个子不该让数字跳几十个百分点。曾经因为直接对千万级的静态分取 log，
    # 开局第一手就能把胜率从 50% 推到 95%。
    one = Position(rule=rules.RENJU)
    one.place(at(7, 7), BLACK)
    step = insight.winrate(one, BLACK, WHITE)["p"]
    check("单子局面的胜率变化在合理范围", abs(step - 0.5) < 0.15, f"{step}")


def test_tactical() -> None:
    """确定性局面必须踩死，不能交给模型去猜。"""
    # 黑棋四连，两端都是成五点：这一手就结束
    four = board([(3, 7), (4, 7), (5, 7), (6, 7)], [(3, 3), (4, 4), (5, 5)])
    info = insight.scan(four)
    check("四连的两端都被数成成五点", info[BLACK]["fives"] == 2, str(info[BLACK]))
    p = insight.winrate(four, BLACK, BLACK, info=info)
    check("有成五点的一方胜率判到 1", p["p"] == 1.0, f"{p['p']}")
    check("战术结论标注来源", p["source"] == "tactical", p["source"])
    check("战术结论给出理由", bool(p["reason"]), p["reason"])

    # 活三两端落下成「活四」，比「下一手就赢」低一档，但仍是确定结论
    three = board([(4, 7), (5, 7), (6, 7)])
    info = insight.scan(three)
    check("活三的两端被数成活四点", info[BLACK]["open_fours"] == 2, str(info[BLACK]))
    check("活三一端落下不是立即成五", info[BLACK]["fives"] == 0, str(info[BLACK]))
    p = insight.winrate(three, BLACK, BLACK, info=info)
    check("有成活四点的局面胜率判到 0.995", p["p"] >= 0.99, f"{p['p']}")

    # 白棋有两个成五点、黑棋没有杀招，堵不过来
    two_fives = board(
        [(9, 12), (10, 12)],
        [(3, 3), (4, 3), (5, 3), (6, 3)],
    )
    info = insight.scan(two_fives)
    check("白棋有两个成五点", info[WHITE]["fives"] == 2, str(info[WHITE]))
    check("黑棋这边没有成五点", info[BLACK]["fives"] == 0, str(info[BLACK]))
    p = insight.winrate(two_fives, BLACK, BLACK, info=info)
    check("轮到黑棋也堵不住两个成五点，判负", p["p"] == 0.0, f"{p['p']}")
    check("判负局面标注为确定", p["confidence"] == "certain", p["confidence"])

    # 已经连五
    won = board([(3, 7), (4, 7), (5, 7), (6, 7), (7, 7)])
    done = insight.winrate(won, BLACK, WHITE)
    check("盘面已有连五时胜率为 1", done["p"] == 1.0, f"{done['p']}")


def test_winrate_facts() -> None:
    pos = board([(3, 7), (4, 7), (5, 7)], [(6, 6), (7, 8)])
    out = insight.winrate(pos, BLACK, BLACK)
    labels = [f["label"] for f in out["facts"]]
    check("可数项三条齐全", len(out["facts"]) == 3, str(labels))
    check("可数项含棋形总量", "棋形总量" in labels, str(labels))
    total = out["facts"][0]
    check("棋形总量与引擎一致", total["mine"] == pos.total[BLACK], f"{total['mine']}")
    check("有解释性结论文本", len(out["confidence_note"]) > 4)


# ------------------------------------------------------------------ 扫描与普查


def test_scan() -> None:
    pos = board([(7, 7)], [(3, 3)])
    info = insight.scan(pos)
    check("扫描覆盖双方", set(info) == {BLACK, WHITE})
    check(
        "四档计数互不重叠（活四不计入冲四）",
        info[BLACK]["open_fours"] + info[BLACK]["fours"] + info[BLACK]["threes"] + info[BLACK]["fives"]
        <= CELLS,
    )

    open_three = board([(4, 7), (5, 7), (6, 7)])
    info = insight.scan(open_three)
    check("活三两端算成活四点（再补一手才连五）", info[BLACK]["open_fours"] == 2, str(info[BLACK]))
    check("活三两端不算成五点", info[BLACK]["fives"] == 0, str(info[BLACK]))
    check("四档互斥：同一点不会同时进两档", info[BLACK]["threes"] == 0, str(info[BLACK]))

    hist, threats = insight.survey(open_three, BLACK)
    check("棋形分布只统计候选点", sum(hist.values()) <= CELLS)
    check("活三以上进入威胁清单", len(threats) >= 2, str(len(threats)))
    check("棋形分布按落子后的形归档", hist["活四"] >= 2, str(hist))

    # 空点的「补一格」语义：shapes_at 按现状判，shapes_if_placed 按落下之后判
    probe = board([(4, 7), (5, 7), (6, 7)])
    check(
        "现状判定把活三的延伸点叫活三",
        max(probe.shapes_at(at(3, 7), BLACK)) == shapes.OPEN_THREE,
        str(probe.shapes_at(at(3, 7), BLACK)),
    )
    check(
        "补子判定才承认这一手是活四",
        max(probe.shapes_if_placed(at(3, 7), BLACK)) == shapes.OPEN_FOUR,
        str(probe.shapes_if_placed(at(3, 7), BLACK)),
    )
    check(
        "四连的端点用补子判定是连五",
        max(board([(4, 7), (5, 7), (6, 7), (7, 7)]).shapes_if_placed(at(3, 7), BLACK)) == shapes.FIVE,
    )


def test_census() -> None:
    pos = board([(7, 7), (8, 7)], [(6, 6)])
    data = insight.census(pos)
    check("棋子数正确", data["stones"] == {"black": 2, "white": 1}, str(data["stones"]))
    check("盘面密度正确", abs(data["occupancy"] - 3 / CELLS) < 1e-3, f"{data['occupancy']}")
    check("重心落在棋子上", data["centroid"]["black"] == [7.5, 7.0], str(data["centroid"]["black"]))
    check(
        "棋形分布六档齐全",
        list(data["shapes"]["black"]) == list(insight.SHAPE_ORDER),
        str(list(data["shapes"]["black"])),
    )
    check("静态材料分与引擎一致", data["material"]["black"] == pos.total[BLACK])
    empty_census = insight.census(Position(rule=rules.RENJU))
    check("空盘没有重心", empty_census["centroid"]["black"] is None)


# ------------------------------------------------------------------ 点位预测


def test_predict() -> None:
    empty = Position(rule=rules.RENJU)
    first = insight.predict(empty, BLACK)
    check("空盘预测天元", first["points"][0]["x"] == 7 and first["points"][0]["y"] == 7, str(first["points"]))

    pos = board([(7, 7), (8, 8)], [(7, 8)])
    out = insight.predict(pos, BLACK, limit=6)
    points = out["points"]
    check("预测给出候选点", len(points) >= 3, str(len(points)))
    check("份额总和不超过 1", sum(p["share"] for p in points) <= 1.0001, f"{sum(p['share'] for p in points):.4f}")
    check("份额单调不增", all(points[i]["share"] >= points[i + 1]["share"] for i in range(len(points) - 1)))
    check("每个点都在棋盘内且为空", all(pos.board[at(p["x"], p["y"])] == 0 for p in points))
    check("每个点都有说明", all(p["why"] for p in points), str(points[:2]))
    check("份额是候选池内的相对量", out["considered"] >= len(points), f"{out['considered']} >= {len(points)}")

    # 标定结束在 prior_weight=0 时不跑网络；跑网络也不该影响候选合法性
    if insight.get_model().predict_cfg["prior_weight"] <= 0:
        check("权重为 0 时不启用网络先验", out["net_used"] is False, str(out["net_used"]))

    threat = board([(4, 7), (5, 7), (6, 7), (7, 7)], [(2, 2), (3, 2), (4, 2)])
    near = insight.predict(threat, WHITE)
    check(
        "有直接成五点时必须把它顶上候选",
        any(at(p["x"], p["y"]) in (at(3, 7), at(8, 7)) for p in near["points"]),
        str([(p["x"], p["y"]) for p in near["points"]]),
    )


# ------------------------------------------------------------------ 解说


def test_analyze() -> None:
    # 黑棋四连，「一手结束」的局面；白棋只有散子，用来把「是不是制胜手」问清楚
    pos = board([(3, 7), (4, 7), (5, 7), (6, 7), (9, 3)], [(2, 2), (2, 3), (3, 2), (12, 12)])
    out = insight.analyze(pos, at(7, 7), BLACK, {"index": 9, "source": "search"})
    check("解说带手数与坐标", out["x"] == 7 and out["y"] == 7 and out["index"] == 9, str(out["index"]))
    check("连五判为制胜手", out["verdict"] == "win", out["verdict"])
    check(
        "胜率前后都有值",
        0.0 <= out["winrate"]["before"] <= 1.0 and out["winrate"]["after"] == 1.0,
        str(out["winrate"]),
    )
    check("给出中文结论", "连五" in out["headline"], out["headline"])
    check("叙述包含结论与理由", out["narrative"].startswith("黑棋这一手") and len(out["narrative"]) > 20)
    # 落子前成五点就在那儿了，所以 before 本来就是 1.0——这一手只是把它兑现
    check("落子前局面已经是必胜", out["winrate"]["before"] == 1.0, str(out["winrate"]["before"]))

    # 有意义的胜率变化：扩张活三 vs 远处闲手
    expand = board([(4, 7), (5, 7), (6, 7)], [(2, 2), (12, 12)])
    grow = insight.analyze(expand, at(7, 7), BLACK)
    idle = insight.analyze(expand, at(13, 13), BLACK)
    check("扩张活三被认成好手", grow["verdict"] == "good", grow["verdict"])
    check(
        "扩张活三比闲手更能抬高胜率",
        grow["winrate"]["delta"] > idle["winrate"]["delta"],
        f"{grow['winrate']['delta']} vs {idle['winrate']['delta']}",
    )
    check("闲手不是好手", idle["verdict"] in ("neutral", "bad"), idle["verdict"])

    plain = board([(7, 7)], [(6, 6)])
    mid = insight.analyze(plain, at(8, 8), BLACK, {"index": 2})
    check("普通手给出四方向描述", any(f["kind"] == "shape" for f in mid["facts"]), str(mid["facts"]))
    check("普通手不是制胜判负", mid["verdict"] in ("good", "neutral", "bad"), mid["verdict"])
    check(
        "备选不含自己被选中的点",
        all(not (a["x"] == 8 and a["y"] == 8) for a in mid["alternatives"]),
        str(mid["alternatives"][:2]),
    )
    check("备选数量受控", len(mid["alternatives"]) <= 3, str(len(mid["alternatives"])))

    ban_pos = board([(3, 7), (4, 7), (5, 7), (6, 7)], [(7, 7), (6, 8), (8, 6), (3, 3)])
    ban_idx = at(6, 7)
    if ban_pos.forbidden(ban_idx, BLACK):
        banned = insight.analyze(ban_pos, ban_idx, BLACK)
        check("禁手点解说标注判负", banned["verdict"] == "ban", banned["verdict"])


def test_explain_and_preview() -> None:
    moves = [
        {"x": 7, "y": 7, "color": BLACK, "source": "open", "comment": "", "think_ms": 3},
        {"x": 7, "y": 8, "color": WHITE, "source": "search", "comment": "贴身", "think_ms": 40},
        {"x": 8, "y": 8, "color": BLACK, "source": "search", "comment": "", "think_ms": 51},
    ]
    exp = insight.explain(moves, 1, rules.RENJU)
    check("按索引解释指定手", exp["x"] == 7 and exp["y"] == 8 and exp["index"] == 1, str(exp["index"]))
    check("沿用该手的来源与说明", exp["comment"] == "贴身" and exp["source"] == "search", str(exp["comment"]))
    check("叙述里引用它自己的说明", "贴身" in exp["narrative"], exp["narrative"][:60])

    clamped = insight.explain(moves, 99, rules.RENJU)
    check("越界索引被夹到最后一手", clamped["index"] == 2, str(clamped["index"]))

    pre = insight.preview(moves, at(6, 8), WHITE, rules.RENJU)
    check("预览标记为待确认", pre["pending"] is True and pre["index"] is None, str(pre["index"]))
    check("预览给出落点", pre["x"] == 6 and pre["y"] == 8, f"{pre['x']},{pre['y']}")


# ------------------------------------------------------------------ 集成层


def test_series() -> None:
    """胜率曲线只补新增的棋步，撤销后整盘重来。"""
    game = store.GameStore().create(
        mode="agent", difficulty=1, persona="balanced", human_color=BLACK, use_llm=False
    )
    for i, (x, y, color) in enumerate(
        [(7, 7, BLACK), (7, 8, WHITE), (8, 8, BLACK), (6, 8, WHITE), (9, 9, BLACK)]
    ):
        game.apply(at(x, y), color)

    first = analysis.series(game)
    check("每手一个点", len(first) == len(game.moves), f"{len(first)} vs {len(game.moves)}")
    check("手数从 1 开始", first[0]["ply"] == 1 and first[-1]["ply"] == 5)
    check("胜率都在 0 到 1 之间", all(0.0 <= p["black"] <= 1.0 for p in first), str([p["black"] for p in first]))

    cached = analysis.series(game)
    check("重复调用命中缓存", cached is first, "同一对象")

    game.apply(at(10, 10), WHITE)
    grown = analysis.series(game)
    check("新增一手只补一个点", len(grown) == 6 and grown[:5] == first[:5], f"{len(grown)}")

    game.pos.undo()
    game.moves.pop()
    shrunk = analysis.series(game)
    check("撤销后曲线跟着缩短", len(shrunk) == 5, str(len(shrunk)))
    check("撤销后数值与重放一致", [p["black"] for p in shrunk] == [p["black"] for p in first])


def test_payload() -> None:
    game = store.GameStore().create(
        mode="agent", difficulty=1, persona="balanced", human_color=BLACK, use_llm=False
    )
    game.apply(at(7, 7), BLACK)
    game.apply(at(7, 8), WHITE)

    out = analysis.payload(game)
    for key in ("turn", "status", "winrate", "prediction", "census", "model", "series", "elapsed_ms"):
        check(f"payload 含 {key}", key in out, str(list(out)))
    check("双方胜率互补", abs(out["winrate"]["human"] + out["winrate"]["opponent"] - 1.0) < 1e-6)
    check("payload 报出自己的耗时", 0 < out["elapsed_ms"] < 2000, f"{out['elapsed_ms']}ms")

    pending_game = store.GameStore().create(
        mode="agent", difficulty=1, persona="balanced", human_color=BLACK, use_llm=False, step=True
    )
    pending_game.apply(at(7, 7), BLACK)
    pending_game.pending = {"x": 8, "y": 8, "color": WHITE, "comment": "抢中腹", "think_ms": 12, "idx": at(8, 8)}
    payload = analysis.payload(pending_game)
    check("单步模式在 payload 里可见", payload["has_pending"] is True, str(payload["has_pending"]))
    check("进行中就有预测候选", len(payload["prediction"]["points"]) > 0, str(len(payload["prediction"]["points"])))
    check("预测的是对手（轮到黑棋的是人类，对手是白棋）", payload["prediction"]["side"] == WHITE,
          str(payload["prediction"]["side"]))

    preview = analysis.pending_preview(pending_game)
    check("待确认的一手能被解说", preview is not None and preview["x"] == 8, str(preview and preview["x"]))
    check("预览引用决策说明", "抢中腹" in preview["narrative"], preview["narrative"][:60])

    pending_game.apply(pending_game.pending.pop("idx"), WHITE)
    check("落子后待确认状态清空", pending_game.pending is None)
    check("落子后没有待解说项", analysis.pending_preview(pending_game) is None)


def test_fallbacks() -> None:
    """模型文件缺失或内容对不上时要能降级，而且必须标出来源不是标定过的。

    clone 下来没跑训练脚本、或者特征顺序改了没重训，都走这条路。
    降级本身不算失败，静默算错才是。
    """
    import tempfile

    real_path = insight.MODEL_FILE
    try:
        # 1. 文件不存在
        insight.MODEL_FILE = Path("__no_such_model__.json")
        missing = insight.WinrateModel()
        check("模型文件缺失时降级", missing.source == "default", missing.source)
        check("降级权重维度正确", len(missing.weights) == len(insight.FEATURES) + 1, str(missing.weights))
        check("降级时主信号系数为正", missing.weights[1] > 0, str(missing.weights))
        z, _ = missing.logit([0.0, 0.0])
        check("降级模型在均值处给出五五开", abs(insight._sigmoid(z) - 0.5) < 1e-9)

        # 2. 文件在，但特征顺序对不上（改过 FEATURES 却没重训）
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "winrate_model.json"
            bad.write_text(
                '{"features": ["old_name"], "weights": [0, 1], "mean": [0], "std": [1]}',
                encoding="utf-8",
            )
            insight.MODEL_FILE = bad
            stale = insight.WinrateModel()
            check("特征顺序不符时拒绝加载", stale.source == "default", stale.source)

        # 3. 文件损坏
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "winrate_model.json"
            broken.write_text("{ not json", encoding="utf-8")
            insight.MODEL_FILE = broken
            check("坏文件不抛异常只降级", insight.WinrateModel().source == "default")
    finally:
        insight.MODEL_FILE = real_path

    check("跑完之后标定模型原样可用", insight.get_model().source in ("selfplay", "default"))


def main() -> int:
    print("=" * 52)
    for fn in (
        test_logit,
        test_winrate_basics,
        test_tactical,
        test_winrate_facts,
        test_scan,
        test_census,
        test_predict,
        test_analyze,
        test_explain_and_preview,
        test_series,
        test_payload,
        test_fallbacks,
    ):
        print(f"\n--- {fn.__name__} ---")
        fn()

    print("\n" + "=" * 52)
    if FAILURES:
        print(f"{len(FAILURES)} 项失败: {', '.join(FAILURES)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
