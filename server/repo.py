"""仓储层。

所有 SQL 只出现在这个文件里。上层（accounts / store / greedy）拿到的都是普通
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


def count_users() -> int:
    row = db.one("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


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
    db.execute(
        "INSERT INTO games (id, mode, owner_id, difficulty, persona,"
        " human_color, use_llm, rule, strict_ban, step, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            game["id"],
            game["mode"],
            game.get("owner_id"),
            game.get("difficulty", 3),
            game.get("persona", "balanced"),
            game.get("human_color", 1),
            int(game.get("use_llm", True)),
            game.get("rule", "renju"),
            int(game.get("strict_ban", False)),
            int(game.get("step", False)),
            game.get("status", "playing"),
            game.get("created_at", time.time()),
        ),
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
) -> None:
    db.execute(
        "UPDATE games SET status = ?, winner = ?, result_reason = ?, win_line = ?,"
        " finished_at = ? WHERE id = ?",
        (status, winner, reason, json.dumps(win_line), time.time(), game_id),
    )


def moves_of(game_id: str) -> list[dict]:
    return _rows(db.query(f"SELECT {MOVE_COLUMNS} FROM moves WHERE game_id = ? ORDER BY ply", (game_id,)))


def games_of_user(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """一个人下过的棋。大厅的「最近对局」和复盘列表都用它。"""
    return _rows(
        db.query(
            "SELECT g.*,"
            " (SELECT COUNT(*) FROM moves m WHERE m.game_id = g.id) AS ply_count"
            " FROM games g WHERE g.owner_id = ?"
            " ORDER BY g.created_at DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        )
    )


def count_games() -> dict:
    total = db.one("SELECT COUNT(*) AS n FROM games")
    live = db.one("SELECT COUNT(*) AS n FROM games WHERE status IN ('playing','waiting')")
    return {
        "games": int(total["n"]) if total else 0,
        "live": int(live["n"]) if live else 0,
    }
