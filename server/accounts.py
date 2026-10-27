"""账户与会话。

密码用 PBKDF2-HMAC-SHA256 派生，12 万轮，每账户独立 salt。服务端只存 token 的
SHA-256 摘要，所以数据文件被人拷走也换不出能用的凭据。

上一版的实现是「一个 JSON 文件 + 一把互斥锁」，`resolve()` 要遍历所有用户才能
找到 token 属于谁——账户上百之后每次带鉴权的请求都是 O(n)。现在 token 摘要
是会话表的主键，解析是一次索引命中。

登录失败按用户名计数而不是按 IP：部署在反向代理后面时 X-Forwarded-For 可以伪造，
按 IP 限流反而给了绕过路径。用户不存在时照样跑一遍哈希，避免用响应时间探测
某个名字是否注册过。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import db, elo, repo

ROOT = Path(__file__).resolve().parent.parent
LEGACY_FILE = ROOT / "data" / "accounts.json"

TOKEN_TTL = 7 * 24 * 3600
ITERATIONS = 120_000
NAME_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$")

MAX_FAILS = 8
FAIL_WINDOW = 300.0


class AuthError(Exception):
    """带 HTTP 语义的鉴权失败。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _hash_password(password: str, salt: bytes, iterations: int = ITERATIONS) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations).hex()


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> float:
    return time.time()


class AccountStore:
    def __init__(self, path: str | Path | None = None) -> None:
        # path 保留是为了让老的调用点（测试、脚本）不用改签名；
        # 传进来的是 .db 才当数据库路径用，否则沿用 db 模块的配置。
        if path and str(path).endswith((".db", ".sqlite", ".sqlite3")):
            db.configure(path)
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        db.connect()

    # ---------------------------------------------------------------- 状态

    @property
    def persistent(self) -> bool:
        return bool(db.info()["persistent"])

    def count(self) -> int:
        return repo.count_users()

    # ---------------------------------------------------------------- 注册 / 登录

    @staticmethod
    def _check_credentials(name: str, password: str) -> None:
        if not NAME_RE.match(name):
            raise AuthError("用户名 2–20 位，只允许中英文、数字和下划线")
        if not 6 <= len(password) <= 128:
            raise AuthError("密码长度需在 6–128 位之间")

    def register(self, name: str, password: str) -> tuple[dict, str]:
        name = name.strip()
        self._check_credentials(name, password)
        if repo.user_exists(name):
            raise AuthError("该用户名已被占用", 409)
        salt = secrets.token_bytes(16)
        try:
            user = repo.create_user(name, salt.hex(), _hash_password(password, salt), ITERATIONS)
        except Exception as exc:  # 并发注册撞上唯一约束
            raise AuthError("该用户名已被占用", 409) from exc
        token = self._issue(user["id"])
        return self.public(name), token

    def login(self, name: str, password: str) -> tuple[dict, str]:
        name = name.strip()
        self._throttle(name)
        user = repo.user_by_name(name)
        # 用户不存在也走一遍哈希，避免用响应时间分辨哪些名字注册过
        salt = bytes.fromhex(user["salt"]) if user else b"\x00" * 16
        iterations = user["iterations"] if user else ITERATIONS
        digest = _hash_password(password, salt, iterations)
        if user is None or not hmac.compare_digest(digest, user["hash"]):
            if user is not None:
                self._note_fail(name)
            raise AuthError("用户名或密码不正确", 401)

        with self._lock:
            self._fails.pop(name, None)
        token = self._issue(user["id"])
        return self.public(user["name"]), token

    def _issue(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        repo.create_session(user_id, _token_digest(token), _now() + TOKEN_TTL)
        return token

    def resolve(self, token: str | None) -> str | None:
        if not token:
            return None
        user_id = repo.session_owner(_token_digest(token))
        if user_id is None:
            return None
        user = repo.user_by_id(user_id)
        return user["name"] if user else None

    def resolve_id(self, token: str | None) -> int | None:
        if not token:
            return None
        return repo.session_owner(_token_digest(token))

    def logout(self, token: str | None) -> None:
        if not token:
            return
        repo.delete_session(_token_digest(token))

    def change_password(self, name: str, old: str, new: str) -> str:
        self._check_credentials(name, new)
        user = repo.user_by_name(name)
        if user is None:
            raise AuthError("账户不存在", 404)
        salt = bytes.fromhex(user["salt"])
        if not hmac.compare_digest(_hash_password(old, salt, user["iterations"]), user["hash"]):
            raise AuthError("原密码不正确", 401)
        salt = secrets.token_bytes(16)
        with db.transaction() as conn:
            conn.execute(
                "UPDATE users SET salt = ?, hash = ?, iterations = ? WHERE id = ?",
                (salt.hex(), _hash_password(new, salt), ITERATIONS, user["id"]),
            )
        # 改密码等于踢掉所有旧会话，只留新签发的这一个
        repo.delete_user_sessions(user["id"])
        return self._issue(user["id"])

    # ---------------------------------------------------------------- 限流

    def _throttle(self, name: str) -> None:
        now = _now()
        with self._lock:
            hits = [t for t in self._fails.get(name, []) if now - t < FAIL_WINDOW]
            self._fails[name] = hits
            blocked = len(hits) >= MAX_FAILS
        if blocked:
            raise AuthError("尝试次数过多，请五分钟后再试", 429)

    def _note_fail(self, name: str) -> None:
        with self._lock:
            self._fails.setdefault(name, []).append(_now())

    # ---------------------------------------------------------------- 战绩

    def record(
        self,
        name: str,
        *,
        mode: str,
        result: str,
        moves: int,
        opponent: str,
        meta: dict | None = None,
    ) -> dict:
        """记一局人机或双人战绩。

        棋谱本身在 games / moves 两张表里，这里只更新聚合计数。
        上一版把最近 40 条战绩塞进用户的 JSON 字段，那是「没有表」时的权宜；
        现在战绩列表从对局表直接查，天然分页、天然跨重启。
        """
        user = repo.user_by_name(name)
        if user is None:
            raise AuthError("账户不存在", 404)
        stats = repo.bump_stats(user["id"], result)
        return self._stats_view(stats)

    def settle_rated(self, winner_id: int, loser_id: int | None, draw: bool = False) -> dict:
        """天梯结算。返回两边的 (旧分, 新分, 变化量)，前端拿它做动效。

        loser_id 为 None 表示对手已注销，只给赢家算分但不改分——对局记录保留。
        """
        a = repo.user_by_id(winner_id)
        if a is None:
            raise AuthError("账户不存在", 404)
        b = repo.user_by_id(loser_id) if loser_id else None
        if b is None:
            return {"rated": False, "reason": "对手账户缺失"}

        score_a = 0.5 if draw else 1.0
        new_a, new_b, delta = elo.settle(
            a["rating"], b["rating"], score_a,
            games_a=a["rated_games"], games_b=b["rated_games"],
        )
        repo.set_rating(a["id"], new_a, rated=True)
        repo.set_rating(b["id"], new_b, rated=True)
        return {
            "rated": True,
            "black": {"name": a["name"], "before": a["rating"], "after": new_a, "delta": delta},
            "white": {"name": b["name"], "before": b["rating"], "after": new_b, "delta": -delta},
        }

    # ---------------------------------------------------------------- 输出

    def _stats_view(self, user: dict[str, Any]) -> dict[str, Any]:
        games = user.get("games", 0) or 0
        return {
            "games": games,
            "wins": user.get("wins", 0),
            "losses": user.get("losses", 0),
            "draws": user.get("draws", 0),
            "streak": user.get("streak", 0),
            "best_streak": user.get("best_streak", 0),
            "win_rate": round(user.get("wins", 0) / games, 3) if games else 0.0,
            "rating": round(user.get("rating", elo.START), 1),
            "tier": elo.tier(user.get("rating", elo.START)),
        }

    def _history(self, user_id: int, limit: int = 20) -> list[dict]:
        rows = repo.games_of_user(user_id, limit=limit)
        out = []
        for row in rows:
            if row["status"] not in ("finished", "abandoned"):
                continue
            human = 1 if row["black_id"] == user_id else 2
            if row["winner"] is None:
                result = "draw"
            else:
                result = "win" if row["winner"] == human else "loss"
            if row["mode"] in ("pvp", "online"):
                other_id = row["white_id"] if human == 1 else row["black_id"]
                other = repo.user_by_id(other_id) if other_id else None
                opponent = other["name"] if other else "对手"
            else:
                opponent = f"{row['mode']}·难度{row['difficulty']}"
            out.append(
                {
                    "id": row["id"],
                    "mode": row["mode"],
                    "result": result,
                    "moves": row["ply_count"] or row["moves_count"],
                    "opponent": opponent,
                    "at": row["finished_at"] or row["created_at"],
                    "rated": bool(row["rated"]),
                    "rating_delta": (
                        round((row["rating_after"] or 0) - (row["rating_before"] or 0), 1)
                        if row["rated"] and row["rating_before"] is not None
                        else None
                    ),
                    "reason": row["result_reason"],
                }
            )
        return out

    def public(self, name: str) -> dict:
        user = repo.user_by_name(name)
        if user is None:
            raise AuthError("账户不存在", 404)
        return {
            "name": user["name"],
            "created_at": user["created_at"],
            "stats": self._stats_view(user),
            "history": self._history(user["id"]),
        }

    def leaderboard(self, limit: int = 20) -> list[dict]:
        rows = repo.leaderboard(limit)
        for row in rows:
            row["tier"] = elo.tier(row["rating"])["label"]
        return rows

    def gamified(self) -> dict:
        return {
            "elo": elo.describe(),
            "board": repo.rating_board_stats(),
            "tiers": [
                {"min": t, "label": n, "count": 0} for t, n in elo.TIERS
            ],
        }


# --------------------------------------------------------------------- 旧数据迁移


def migrate_legacy_json(path: Path = LEGACY_FILE, *, dry_run: bool = False) -> dict:
    """把 v2 的 accounts.json 导进 SQLite。

    幂等：按用户名跳过已存在的账户，重复执行不会撞唯一约束。
    旧的 token 摘要一起搬过来——升级完不该把所有人踢下线。

    数据迁移代码最容易出的事是「跑第二遍就炸」，所以这里用 user_exists 做门，
    而不是靠用户名唯一约束抛异常。
    """
    if not path.exists():
        return {"ok": False, "reason": "没有找到 accounts.json", "imported": 0, "skipped": 0}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "reason": f"读取失败：{exc}", "imported": 0, "skipped": 0}

    users = data.get("users")
    if not isinstance(users, dict):
        return {"ok": False, "reason": "文件里没有 users 字段", "imported": 0, "skipped": 0}

    imported = skipped = tokens = 0
    for name, raw in users.items():
        if not isinstance(raw, dict) or "salt" not in raw or "hash" not in raw:
            skipped += 1
            continue
        if repo.user_exists(name):
            skipped += 1
            continue
        if dry_run:
            imported += 1
            continue

        stats = raw.get("stats") or {}
        with db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users (name, name_key, salt, hash, iterations, created_at,"
                " games, wins, losses, draws, streak, best_streak)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    name.casefold(),
                    raw["salt"],
                    raw["hash"],
                    raw.get("iterations", ITERATIONS),
                    raw.get("created_at") or _now(),
                    stats.get("games", 0),
                    stats.get("wins", 0),
                    stats.get("losses", 0),
                    stats.get("draws", 0),
                    stats.get("streak", 0),
                    stats.get("best_streak", 0),
                ),
            )
            user_id = cur.lastrowid
            now = _now()
            for digest, entry in (raw.get("tokens") or {}).items():
                expires = float(entry.get("expires", 0))
                if expires <= now:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (digest, user_id, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?)",
                    (digest, user_id, float(entry.get("at", now)), expires),
                )
                tokens += 1
        imported += 1

    return {
        "ok": True,
        "imported": imported,
        "skipped": skipped,
        "tokens": tokens,
        "dry_run": dry_run,
        "total": len(users),
    }
