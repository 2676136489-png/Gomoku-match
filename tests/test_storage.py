"""存储层测试。不需要起服务，直接跑：

    python tests/test_storage.py

覆盖迁移、会话过期、棋谱重放、悔棋回滚、战绩计数、旧数据导入的幂等性，
以及「换个连接还能读到」这件事本身。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import db, repo  # noqa: E402
from server.accounts import ITERATIONS, AccountStore, _hash_password, migrate_legacy_json  # noqa: E402
from server.gomoku.position import BLACK, WHITE  # noqa: E402
from server.store import GameStore  # noqa: E402

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


def fresh(path: Path) -> None:
    db.configure(path)
    db.connect()


# --------------------------------------------------------------------- 模式


def test_schema(tmp: Path) -> None:
    print("\n--- 模式与迁移 ---")
    fresh(tmp / "schema.db")
    check("迁移跑到最新版本", db.version() == len(db.MIGRATIONS), f"{db.version()}/{len(db.MIGRATIONS)}")

    tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in ("users", "sessions", "games", "moves"):
        check(f"表 {name} 存在", name in tables)
    # v2 把房间表删了。这条不是洁癖：留着表就会有下一个人照着它写新逻辑。
    check("房间表已随 v2 移除", "rooms" not in tables, str(sorted(tables)))

    game_cols = {r["name"] for r in db.query("PRAGMA table_info(games)")}
    for dead in ("black_id", "white_id", "rated", "clock_base", "clock_black", "turn_started"):
        check(f"games.{dead} 已移除", dead not in game_cols)
    user_cols = {r["name"] for r in db.query("PRAGMA table_info(users)")}
    check("users.rating 已移除", "rating" not in user_cols)

    indexes = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='index'")}
    check("会话按用户建了索引", any("sessions_user" in i for i in indexes))
    check("对局按建局人建了索引", any("games_owner" in i for i in indexes))
    check("黑白方索引随列一起移除", not any("games_black" in i or "games_white" in i for i in indexes))

    # 二次连接不会重放迁移
    db.close_all()
    conn = db.connect()
    check("重复连接不重复迁移", db.version() == len(db.MIGRATIONS))
    check("WAL 已开启", conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal")
    check("外键已开启", conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1)
    check("外键没有悬空引用", db.query("PRAGMA foreign_key_check") == [])


def test_tables(tmp: Path) -> None:
    print("\n--- 账户与会话 ---")
    fresh(tmp / "users.db")
    salt = "00" * 16
    user = repo.create_user("张三abc", salt, _hash_password("secret-1", bytes(16)), ITERATIONS)
    check("建账户成功", user["id"] > 0)
    check("按名字能查到", repo.user_by_name("张三abc")["id"] == user["id"])
    check("名字大小写不敏感", repo.user_by_name("ABC") is None)
    repo.create_user("Alice", salt, "x", ITERATIONS)
    check("大小写视为同一个名字", repo.user_by_name("alice")["name"] == "Alice")

    stats = repo.user_by_id(user["id"])
    check("新账户战绩全零", (stats["games"], stats["wins"], stats["losses"]) == (0, 0, 0), str(dict(stats)))

    repo.bump_stats(user["id"], "win")
    repo.bump_stats(user["id"], "win")
    stats = repo.user_by_id(user["id"])
    check("两连胜计数正确", stats["wins"] == 2 and stats["streak"] == 2)
    check("总手数跟着涨", stats["games"] == 2)
    repo.bump_stats(user["id"], "loss")
    stats = repo.user_by_id(user["id"])
    check("输了清空连胜", stats["streak"] == 0)
    check("最长连胜被记住", stats["best_streak"] == 2)
    repo.bump_stats(user["id"], "draw")
    check("和棋单独计数", repo.user_by_id(user["id"])["draws"] == 1)

    digest = "d" * 64
    repo.create_session(user["id"], digest, time.time() + 60)
    check("会话能解析到人", repo.session_owner(digest) == user["id"])
    repo.delete_session(digest)
    check("注销后解析不到", repo.session_owner(digest) is None)

    repo.create_session(user["id"], "expired", time.time() - 1)
    check("过期会话自动失效", repo.session_owner("expired") is None)
    check("过期会话被清理", db.one("SELECT 1 FROM sessions WHERE digest='expired'") is None)

    repo.create_session(user["id"], "a" * 64, time.time() + 60)
    repo.delete_user_sessions(user["id"])
    check("改密码清空全部会话", db.one("SELECT COUNT(*) n FROM sessions WHERE user_id=?", (user["id"],))["n"] == 0)


def test_games(tmp: Path) -> None:
    print("\n--- 对局与棋谱 ---")
    fresh(tmp / "games.db")
    a = repo.create_user("player", "00" * 16, "x", ITERATIONS)

    store = GameStore()
    game = store.create(
        mode="agent",
        difficulty=3,
        persona="balanced",
        human_color=BLACK,
        use_llm=False,
        owner_id=a["id"],
    )
    check("建局落库", repo.game_by_id(game.id) is not None)
    check("初始状态是进行中", game.status == "playing")
    check("建局人记在库里", repo.game_by_id(game.id)["owner_id"] == a["id"])

    game.apply(7 * 15 + 7, BLACK)
    game.apply(8 * 15 + 8, WHITE)
    check("两手都在内存里", len(game.moves) == 2)
    rows = repo.moves_of(game.id)
    check("两手都落了库", len(rows) == 2, str(len(rows)))
    check("落库坐标一致", (rows[0]["x"], rows[0]["y"]) == (7, 7))

    # 换一个 store 实例 = 模拟进程重启
    reborn = GameStore()
    check("缓存里没有这个局", game.id not in reborn._games)
    loaded = reborn.get(game.id)
    check("能从库里重放出来", loaded is not None)
    check("重放后手数一致", loaded is not None and len(loaded.moves) == 2)
    check("重放后棋盘一致", loaded is not None and loaded.pos.board == game.pos.board)
    check("重放后轮到黑棋", loaded is not None and loaded.turn == BLACK)

    loaded.apply(6 * 15 + 6, BLACK)
    check("重放出来的局还能继续下", repo.moves_of(game.id)[-1]["x"] == 6)

    # 悔棋：数据库要跟着回退，否则重启会多出幽灵棋子
    repo.trim_moves(game.id, 2)
    check("悔棋回退到两手", len(repo.moves_of(game.id)) == 2)
    check("手数计数同步", repo.game_by_id(game.id)["moves_count"] == 2)

    repo.finish_game(game.id, status="finished", winner=BLACK, reason="黑棋连成 5 子", win_line=[[7, 7], [11, 11]])
    row = repo.game_by_id(game.id)
    check("终局状态写库", row["status"] == "finished" and row["winner"] == BLACK)
    check("胜利连线是 JSON", json.loads(row["win_line"])[1] == [11, 11])

    again = GameStore().get(game.id)
    check("重启后终局状态保留", again is not None and again.status == "finished" and again.winner == BLACK)

    check("按人查得到自己的局", len(repo.games_of_user(a["id"])) == 1)
    stranger = repo.create_user("stranger", "00" * 16, "x", ITERATIONS)
    check("无关的人查不到", len(repo.games_of_user(stranger["id"])) == 0)


def test_settlement(tmp: Path) -> None:
    print("\n--- 单人结算 ---")
    from server import settlement
    from server.runtime import store

    fresh(tmp / "settle.db")
    # fresh() 换了库位置，但单例还是同一个对象；库里没有数据，正好当新库用
    accounts = AccountStore()
    _, token = accounts.register("棋手甲", "gomoku-2026")
    uid = accounts.resolve_id(token)

    # 人执黑并赢：胜场 +1
    game = store.create(
        mode="agent", difficulty=3, persona="balanced", human_color=BLACK, use_llm=False,
        owner_id=uid,
    )
    game.apply(7 * 15 + 7, BLACK)
    game.apply(8 * 15 + 8, WHITE)
    game.apply(8 * 15 + 7, BLACK)
    game.winner = BLACK
    game.status = "finished"
    settlement.settle(game)
    stats = accounts.public("棋手甲")["stats"]
    check("赢一局记一胜", stats["wins"] == 1 and stats["games"] == 1, str(stats))
    check("结算后标记已记账", game.recorded is True)

    settlement.settle(game)
    check("重复结算不重复记账", accounts.public("棋手甲")["stats"]["games"] == 1)

    # 人执白、黑胜：这一局是负
    loss = store.create(
        mode="agent", difficulty=3, persona="balanced", human_color=WHITE, use_llm=False,
        owner_id=uid,
    )
    loss.winner = BLACK
    loss.status = "finished"
    settlement.settle(loss)
    stats = accounts.public("棋手甲")["stats"]
    check("执白被判负", stats["losses"] == 1, str(stats))
    check("最近对局记成负", accounts.public("棋手甲")["history"][0]["result"] == "loss")

    # 无主局（引擎对局）只写终局状态，不记账
    anon = store.create(
        mode="engine", difficulty=3, persona="balanced", human_color=BLACK, use_llm=False,
    )
    anon.status = "finished"
    settlement.settle(anon)
    check("无主局不记账", accounts.public("棋手甲")["stats"]["games"] == 2)
    check("无主局也落终局状态", repo.game_by_id(anon.id)["status"] == "finished")


def test_accounts(tmp: Path) -> None:
    print("\n--- 账户服务 ---")
    fresh(tmp / "store.db")
    store = AccountStore()
    user, token = store.register("棋手甲", "gomoku-2026")
    check("注册返回账户", user["name"] == "棋手甲")
    check("注册即带战绩", user["stats"]["games"] == 0)
    check("返回 token", len(token) > 20)

    check("token 能解析出人", store.resolve(token) == "棋手甲")
    check("假 token 解析不出", store.resolve("nope") is None)
    check("能解析出 id", store.resolve_id(token) > 0)

    try:
        store.register("棋手甲", "another-pass")
        check("重名被拒", False)
    except Exception as exc:
        check("重名被拒", getattr(exc, "status", 0) == 409, str(exc))

    try:
        store.register("x", "123")
        check("弱用户名被拒", False)
    except Exception:
        check("弱用户名被拒", True)

    dump = json.dumps([dict(row) for row in db.query("SELECT * FROM users")])
    check("密码不落明文", "gomoku-2026" not in dump)

    new_token = store.change_password("棋手甲", "gomoku-2026", "new-pass-2026")
    check("改密码后旧 token 失效", store.resolve(token) is None)
    check("新 token 可用", store.resolve(new_token) == "棋手甲")

    store.record("棋手甲", result="win")
    pub = store.public("棋手甲")
    check("战绩计入", pub["stats"]["wins"] == 1)
    check("胜率算对", pub["stats"]["win_rate"] == 1.0)

    store.logout(new_token)
    check("注销后 token 失效", store.resolve(new_token) is None)

    # 顺序：注册 → 重连（模拟重启）
    store2 = AccountStore()
    check("重启后账户还在", store2.resolve(store.login("棋手甲", "new-pass-2026")[1]) == "棋手甲")


def test_legacy(tmp: Path) -> None:
    print("\n--- 旧数据导入 ---")
    fresh(tmp / "legacy.db")
    source = tmp / "accounts.json"
    salt = bytes(16).hex()
    payload = {
        "version": 1,
        "users": {
            "老用户": {
                "name": "老用户",
                "salt": salt,
                "hash": _hash_password("old-pass-2026", bytes(16)),
                "iterations": ITERATIONS,
                "created_at": time.time() - 86400,
                "stats": {"games": 5, "wins": 3, "losses": 2, "draws": 0, "streak": 1, "best_streak": 2},
                "history": [],
                "tokens": {"f" * 64: {"expires": time.time() + 3600, "at": time.time()}},
            }
        },
    }
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    first = migrate_legacy_json(source)
    check("导入成功", first["imported"] == 1, str(first))
    check("旧 token 一起搬过来", first["tokens"] == 1)
    migrated = repo.user_by_name("老用户")
    check("战绩被保留", migrated["games"] == 5 and migrated["wins"] == 3)
    check("老 token 仍然可用", repo.session_owner("f" * 64) == migrated["id"])

    second = migrate_legacy_json(source)
    check("重复导入不报错", second["ok"] is True)
    check("重复导入全部跳过", second["imported"] == 0 and second["skipped"] == 1, str(second))
    check("用户没有重复", repo.count_users() == 1)

    dry = migrate_legacy_json(source, dry_run=True)
    check("试跑不写库", dry["dry_run"] is True and repo.count_users() == 1)

    missing = migrate_legacy_json(tmp / "nope.json")
    check("文件不存在时报错不抛异常", missing["ok"] is False)


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp = Path(tmpdir)
        try:
            test_schema(tmp)
            test_tables(tmp)
            test_games(tmp)
            test_settlement(tmp)
            test_accounts(tmp)
            test_legacy(tmp)
        finally:
            # Windows 上不关连接就删不掉临时目录，报的还是一个跟测试无关的
            # PermissionError。测试自己收尾，别让清理失败看起来像用例失败。
            db.close_all()

    print("\n" + "=" * 46)
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
