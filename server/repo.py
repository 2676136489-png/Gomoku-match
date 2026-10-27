"""仓储层。

所有 SQL 只出现在这个文件里。上层（accounts / store / realtime）拿到的都是普通
字典，不接触 sqlite3.Row——Row 是「还连着游标的元组」，一旦被传出去并缓存，
连接关闭后这些引用即失效。

返回值统一是 dict，时间统一是 Unix 秒（REAL），布尔统一是 int（0/1）。
这三条一致性定了之后，序列化边界上不用再逐字段转换。
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Iterable

from . import db

# 一局棋的落子记录对前端是只读回放，不需要在数据库里做增量更新
MOVE_COLUMNS = "ply, x, y, color, source, comment, think_ms, eval"


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


# --------------------------------------------------------------------- 用户


def create_user(name: str, salt: str, password_hash: str, iterations: int) -> dict:
    now = time.time()
    cur = db.execute(
        "INSERT INTO users (name, name_key, salt, hash, iterations, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (name, name.casefold(), salt, password_hash, iterations, now),
    )
    return {"id": cur.lastrowid, "name": name, "created_at": now}


def user_by_name(name: str) -> dict | None:
    return _row(db.one("SELECT * FROM users WHERE name_key = ?", (name.strip().casefold(),)))


def user_by_id(user_id: int) -> dict | None:
    return _row(db.one("SELECT * FROM users WHERE id = ?", (user_id,)))


def user_exists(name: str) -> bool:
    return db.one("SELECT 1 FROM users WHERE name_key = ?", (name.strip().casefold(),)) is not None


def touch_login(user_id: int) -> None:
    """登录时间不单独建表：会话表里最新一条 created_at 就是它。"""
    db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))


def bump_stats(user_id: int, result: str) -> dict:
    """记一局。result 取 win / loss / draw。

    streak 的逻辑放在 SQL 里而不是 Python 里——它是「读当前值再写回」的典型
    read-modify-write，放在应用层就得自己保证串行。交给 UPSERT 表达式，
    一条语句原子完成。
    """
    column = {"win": "wins", "loss": "losses", "draw": "draws"}.get(result)
    if column is None:
        raise ValueError(f"未知结果 {result}")

    with db.transaction() as conn:
        if result == "win":
            conn.execute(
                "UPDATE users SET games = games + 1, wins = wins + 1,"
                " streak = streak + 1, best_streak = MAX(best_streak, streak + 1)"
                " WHERE id = ?",
                (user_id,),
            )
        else:
            conn.execute(
                f"UPDATE users SET games = games + 1, {column} = {column} + 1, streak = 0 WHERE id = ?",
                (user_id,),
            )
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else {}


def set_rating(user_id: int, rating: float, *, rated: bool = True) -> None:
    if rated:
        db.execute(
            "UPDATE users SET rating = ?, rated_games = rated_games + 1 WHERE id = ?",
            (rating, user_id),
        )
    else:
        db.execute("UPDATE users SET rating = ? WHERE id = ?", (rating, user_id))


def leaderboard(limit: int = 20, *, min_games: int = 1) -> list[dict]:
    rows = db.query(
        "SELECT name, rating, rated_games, games, wins, losses, draws,"
        " best_streak, created_at FROM users"
        " WHERE rated_games >= ? ORDER BY rating DESC, rated_games DESC LIMIT ?",
        (min_games, limit),
    )
    out = []
    for i, row in enumerate(rows, 1):
        item = dict(row)
        played = item["rated_games"] or 0
        item["rank"] = i
        item["win_rate"] = round(item["wins"] / item["games"], 3) if item["games"] else 0.0
        item["rating"] = round(item["rating"], 1)
        out.append(item)
    return out


def count_users() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


def rank_of(user_id: int) -> int | None:
    """全量排名。排序键必须和 leaderboard() 完全一致，否则两块界面会互相打脸。"""
    me = db.one("SELECT rating, rated_games FROM users WHERE id = ?", (user_id,))
    if me is None:
        return None
    if not me["rated_games"]:
        return None
    row = db.one(
        "SELECT COUNT(*) AS n FROM users WHERE rated_games >= 1"
        " AND (rating > ? OR (rating = ? AND rated_games > ?))",
        (me["rating"], me["rating"], me["rated_games"]),
    )
    return int(row["n"]) + 1 if row else None


def rating_board_stats() -> dict:
    row = db.one(
        "SELECT COUNT(*) AS players, AVG(rating) AS avg, MAX(rating) AS top"
        " FROM users WHERE rated_games >= 1"
    )
    if not row:
        return {"players": 0, "average": 0, "top": 0}
    return {
        "players": int(row["players"] or 0),
        "average": round(row["avg"] or 0, 1),
        "top": round(row["top"] or 0, 1),
    }


# --------------------------------------------------------------------- 会话


def create_session(user_id: int, digest: str, expires_at: float, user_agent: str = "") -> None:
    db.execute(
        "INSERT OR REPLACE INTO sessions (digest, user_id, created_at, expires_at, user_agent)"
        " VALUES (?, ?, ?, ?, ?)",
        (digest, user_id, time.time(), expires_at, user_agent[:200]),
    )


def session_owner(digest: str) -> int | None:
    row = db.one("SELECT user_id, expires_at FROM sessions WHERE digest = ?", (digest,))
    if row is None:
        return None
    if row["expires_at"] <= time.time():
        db.execute("DELETE FROM sessions WHERE digest = ?", (digest,))
        return None
    return int(row["user_id"])


def delete_session(digest: str) -> None:
    db.execute("DELETE FROM sessions WHERE digest = ?", (digest,))


def delete_user_sessions(user_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_sessions() -> int:
    cur = db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
    return cur.rowcount or 0


# --------------------------------------------------------------------- 对局


def create_game(game: dict) -> None:
    base = int(game.get("clock_base", 0) or 0)
    db.execute(
        "INSERT INTO games (id, mode, owner_id, black_id, white_id, difficulty, persona,"
        " human_color, use_llm, rule, strict_ban, step, status, rated,"
        " clock_base, clock_inc, clock_black, clock_white, turn_started, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            game["id"],
            game["mode"],
            game.get("owner_id"),
            game.get("black_id"),
            game.get("white_id"),
            game.get("difficulty", 3),
            game.get("persona", "balanced"),
            game.get("human_color", 1),
            int(game.get("use_llm", True)),
            game.get("rule", "renju"),
            int(game.get("strict_ban", False)),
            int(game.get("step", False)),
            game.get("status", "playing"),
            int(game.get("rated", False)),
            base,
            int(game.get("clock_inc", 0) or 0),
            float(base) if base else None,
            float(base) if base else None,
            time.time() if base else None,
            game.get("created_at", time.time()),
        ),
    )


def update_clocks(game_id: str, black: float, white: float, turn_started: float | None) -> None:
    """每手结算后的剩余秒数。不这么做的话进程重启会把计时对局变回无计时。"""
    db.execute(
        "UPDATE games SET clock_black = ?, clock_white = ?, turn_started = ? WHERE id = ?",
        (black, white, turn_started, game_id),
    )


def game_by_id(game_id: str) -> dict | None:
    return _row(db.one("SELECT * FROM games WHERE id = ?", (game_id,)))


def add_move(game_id: str, ply: int, x: int, y: int, color: int, **extra: Any) -> None:
    db.execute(
        "INSERT OR REPLACE INTO moves (game_id, ply, x, y, color, source, comment, think_ms, eval)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            game_id,
            ply,
            x,
            y,
            color,
            extra.get("source", ""),
            extra.get("comment", ""),
            int(extra.get("think_ms", 0)),
            float(extra.get("eval", 0.0)),
        ),
    )
    db.execute("UPDATE games SET moves_count = ? WHERE id = ?", (ply + 1, game_id))


def trim_moves(game_id: str, keep: int) -> None:
    db.execute("DELETE FROM moves WHERE game_id = ? AND ply >= ?", (game_id, keep))
    db.execute("UPDATE games SET moves_count = ? WHERE id = ?", (keep, game_id))


def finish_game(
    game_id: str,
    *,
    status: str,
    winner: int | None,
    reason: str,
    win_line: list[list[int]],
    rated: bool = False,
    rating_before: float | None = None,
    rating_after: float | None = None,
) -> None:
    db.execute(
        "UPDATE games SET status = ?, winner = ?, result_reason = ?, win_line = ?,"
        " rated = ?, rating_before = ?, rating_after = ?, finished_at = ? WHERE id = ?",
        (
            status,
            winner,
            reason,
            json.dumps(win_line),
            int(rated),
            rating_before,
            rating_after,
            time.time(),
            game_id,
        ),
    )


def moves_of(game_id: str) -> list[dict]:
    return _rows(db.query(f"SELECT {MOVE_COLUMNS} FROM moves WHERE game_id = ? ORDER BY ply", (game_id,)))


def games_of_user(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """一个人参与过的所有对局（无论执黑执白还是人机）。对局大厅的战绩列表用它。"""
    return _rows(
        db.query(
            "SELECT g.*,"
            " (SELECT COUNT(*) FROM moves m WHERE m.game_id = g.id) AS ply_count"
            " FROM games g WHERE g.owner_id = ? OR g.black_id = ? OR g.white_id = ?"
            " ORDER BY g.created_at DESC LIMIT ? OFFSET ?",
            (user_id, user_id, user_id, limit, offset),
        )
    )


def count_games() -> dict:
    total = db.one("SELECT COUNT(*) AS n FROM games")
    live = db.one("SELECT COUNT(*) AS n FROM games WHERE status IN ('playing','waiting')")
    return {
        "games": int(total["n"]) if total else 0,
        "live": int(live["n"]) if live else 0,
    }


# --------------------------------------------------------------------- 房间


def create_room(room: dict) -> None:
    db.execute(
        "INSERT INTO rooms (id, code, host_id, guest_id, game_id, rule, clock_base, clock_inc,"
        " rated, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            room["id"],
            room["code"],
            room["host_id"],
            room.get("guest_id"),
            room.get("game_id"),
            room.get("rule", "renju"),
            room.get("clock_base", 300),
            room.get("clock_inc", 5),
            int(room.get("rated", True)),
            room.get("status", "open"),
            room.get("created_at", time.time()),
        ),
    )


def room_by_code(code: str) -> dict | None:
    return _row(db.one("SELECT * FROM rooms WHERE code = ? AND status != 'closed'", (code.upper(),)))


def room_by_id(room_id: str) -> dict | None:
    return _row(db.one("SELECT * FROM rooms WHERE id = ?", (room_id,)))


def update_room(room_id: str, **fields: Any) -> None:
    if not fields:
        return
    columns = ", ".join(f"{k} = ?" for k in fields)
    db.execute(f"UPDATE rooms SET {columns} WHERE id = ?", (*fields.values(), room_id))


def list_rooms(status: str = "open", limit: int = 30) -> list[dict]:
    return _rows(
        db.query(
            "SELECT r.*, u.name AS host_name, u.rating AS host_rating"
            " FROM rooms r JOIN users u ON u.id = r.host_id"
            " WHERE r.status = ? ORDER BY r.created_at DESC LIMIT ?",
            (status, limit),
        )
    )


def reap_rooms(max_idle_seconds: float = 1800.0) -> int:
    """回收长时间无人进入的房间。若不清理，大厅列表会累积大量已失效房间。"""
    cutoff = time.time() - max_idle_seconds
    cur = db.execute(
        "UPDATE rooms SET status = 'closed' WHERE status = 'open' AND created_at < ?", (cutoff,)
    )
    return cur.rowcount or 0
