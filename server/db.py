"""SQLite 存储层。

为什么从「一个 JSON 文件」换成 SQLite：账户和战绩原来是整文件读改写，登录一次
就要把 77 KB 的 JSON 重新序列化并原子替换，写放大与并发上限都锁在单进程内。
现在有对局、房间、棋谱三张会持续增长的表，整文件方案直接失效——一盘 200 手的
棋不可能为了记一手而重写整个库。

选型理由不是「SQLite 比 Postgres 好」，而是这个服务的部署形态只有单进程单端口：
SQLite 是标准库，不起额外进程、不需要账号密码、不需要连接池，崩溃恢复靠 WAL
重放。换 Postgres 只会给部署链路多加一个失败点和一份凭据管理。

并发模型：
- 每线程一条连接。FastAPI 的同步路由跑在线程池里，连接不能跨线程共享；
  用 threading.local 而不是传参，是因为仓储层不该把连接生命周期泄漏给路由。
- WAL + busy_timeout，读不阻塞写。写事务用 BEGIN IMMEDIATE 提前拿写锁，
  避免「读到一半升级写锁失败」的 SQLITE_BUSY。
- synchronous=NORMAL：WAL 下最坏丢最近一次提交，但不会损坏库。对棋谱来说
  这个取舍成立；对账务类数据就不成立。

迁移用 PRAGMA user_version 记版本，只前进不回退。新增迁移只能往后追加，
已发布的迁移不允许修改——线上库已经跑过了，改了也不会重放。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "data" / "gomoku.db"

_local = threading.local()
_state = {
    "path": os.environ.get("GOMOKU_DB") or str(DEFAULT_PATH),
    "ready": False,
    "writable": True,
}


# --------------------------------------------------------------------- 迁移

# 每个元素是一次迁移，下标 + 1 就是目标版本号。
# 只允许追加，不允许修改已发布的条目。
MIGRATIONS: list[str] = [
    # --- v1: 账户、会话、对局、棋谱、房间 ---
    """
    CREATE TABLE users (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT    NOT NULL UNIQUE,
        name_key     TEXT    NOT NULL UNIQUE,
        salt         TEXT    NOT NULL,
        hash         TEXT    NOT NULL,
        iterations   INTEGER NOT NULL,
        created_at   REAL    NOT NULL,
        rating       REAL    NOT NULL DEFAULT 1200,
        rated_games  INTEGER NOT NULL DEFAULT 0,
        games        INTEGER NOT NULL DEFAULT 0,
        wins         INTEGER NOT NULL DEFAULT 0,
        losses       INTEGER NOT NULL DEFAULT 0,
        draws        INTEGER NOT NULL DEFAULT 0,
        streak       INTEGER NOT NULL DEFAULT 0,
        best_streak  INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE sessions (
        digest     TEXT    PRIMARY KEY,
        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at REAL    NOT NULL,
        expires_at REAL    NOT NULL,
        user_agent TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id, expires_at);

    CREATE TABLE games (
        id            TEXT    PRIMARY KEY,
        mode          TEXT    NOT NULL,
        owner_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        black_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        white_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
        difficulty    INTEGER NOT NULL DEFAULT 3,
        persona       TEXT    NOT NULL DEFAULT 'balanced',
        human_color   INTEGER NOT NULL DEFAULT 1,
        use_llm       INTEGER NOT NULL DEFAULT 1,
        rule          TEXT    NOT NULL DEFAULT 'renju',
        strict_ban    INTEGER NOT NULL DEFAULT 0,
        step          INTEGER NOT NULL DEFAULT 0,
        status        TEXT    NOT NULL DEFAULT 'playing',
        winner        INTEGER,
        result_reason TEXT    NOT NULL DEFAULT '',
        win_line      TEXT    NOT NULL DEFAULT '[]',
        rated         INTEGER NOT NULL DEFAULT 0,
        rating_before REAL,
        rating_after  REAL,
        clock_base    INTEGER NOT NULL DEFAULT 0,
        clock_inc     INTEGER NOT NULL DEFAULT 0,
        clock_black   REAL,
        clock_white   REAL,
        turn_started  REAL,
        created_at    REAL    NOT NULL,
        finished_at   REAL,
        moves_count   INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_games_owner ON games(owner_id, created_at DESC);
    CREATE INDEX idx_games_black ON games(black_id, created_at DESC);
    CREATE INDEX idx_games_white ON games(white_id, created_at DESC);

    CREATE TABLE moves (
        game_id  TEXT    NOT NULL REFERENCES games(id) ON DELETE CASCADE,
        ply      INTEGER NOT NULL,
        x        INTEGER NOT NULL,
        y        INTEGER NOT NULL,
        color    INTEGER NOT NULL,
        source   TEXT    NOT NULL DEFAULT '',
        comment  TEXT    NOT NULL DEFAULT '',
        think_ms INTEGER NOT NULL DEFAULT 0,
        eval     REAL    NOT NULL DEFAULT 0,
        PRIMARY KEY (game_id, ply)
    );

    CREATE TABLE rooms (
        id         TEXT    PRIMARY KEY,
        code       TEXT    NOT NULL UNIQUE,
        host_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        guest_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
        game_id    TEXT,
        rule       TEXT    NOT NULL DEFAULT 'renju',
        clock_base INTEGER NOT NULL DEFAULT 300,
        clock_inc  INTEGER NOT NULL DEFAULT 5,
        rated      INTEGER NOT NULL DEFAULT 1,
        status     TEXT    NOT NULL DEFAULT 'open',
        created_at REAL    NOT NULL
    );
    CREATE INDEX idx_rooms_status ON rooms(status, created_at DESC);
    """,
]


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= len(MIGRATIONS):
        return
    for target in range(version + 1, len(MIGRATIONS) + 1):
        script = MIGRATIONS[target - 1]
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _split(script):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={target}")
            conn.execute("COMMIT")
        except sqlite3.Error:
            conn.execute("ROLLBACK")
            raise


def _split(script: str) -> list[str]:
    """按分号切语句。迁移脚本里没有触发器，不需要真正的 SQL 解析器。"""
    return [part.strip() for part in script.split(";") if part.strip()]


def configure(path: str | Path | None = None) -> None:
    """切换数据库位置。测试和一次性迁移脚本用，线上不会调用。"""
    global _local
    close_all()
    _state["path"] = str(path or DEFAULT_PATH)
    _state["ready"] = False
    _state["writable"] = True
    _local = threading.local()


def connect() -> sqlite3.Connection:
    """取当前线程的连接，必要时建立并完成迁移。"""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == _state["path"]:
        return conn
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass

    target = Path(_state["path"])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(str(target))
        _migrate(conn)
    except (OSError, sqlite3.Error):
        # 只读文件系统等环境下退成内存库：功能全部可用，进程一退就没了。
        # 比让服务起不来好——健康检查会把 persistent 报成 false。
        conn = _connect(":memory:")
        _migrate(conn)
        _state["writable"] = False

    _local.conn = conn
    _local.path = _state["path"]
    return conn


def close_all() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """写事务。BEGIN IMMEDIATE 提前拿锁，避免读事务升级写锁时撞 SQLITE_BUSY。

    不支持嵌套：仓储层的方法都是叶子操作，路由层负责组合。
    """
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def one(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def execute(sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
    """单条自动提交写。多条语句要原子性时用 transaction()。"""
    return connect().execute(sql, params)


def version() -> int:
    return connect().execute("PRAGMA user_version").fetchone()[0]


def info() -> dict:
    """健康检查用的现场信息。故意不查表行数——那会给探针引入写放大。"""
    path = Path(_state["path"])
    return {
        "engine": "sqlite",
        "path": str(path),
        "version": version(),
        "writable": _state["writable"],
        "persistent": _state["writable"] and path.exists(),
        "size_kb": round(path.stat().st_size / 1024, 1) if path.exists() else 0,
    }


def reset_for_tests() -> None:
    """测试专用：清库但不换路径。"""
    conn = connect()
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("BEGIN IMMEDIATE")
    for table in tables:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys=ON")


def db_path() -> str:
    return _state["path"]


def env_path() -> str:
    return os.environ.get("GOMOKU_DB", str(DEFAULT_PATH))
