"""棋谱库。

棋谱是「给贪心学习用的数据」，不是对局记录。两者刻意分开：对局记录在
games / moves 表里，是「谁在哪天下了一盘什么棋」；棋谱库只有落子序列和一句
出处说明，可以被删除、被替换、被重复喂给训练，不影响任何人的战绩。

内置的那一份（gomoku/corpus_seed.json）随仓库走，用来保证第一次打开页面就有
东西可看、可训练；用户上传的写在 data/corpus.json 里，跟运行时数据一起不进仓库。
上传只收文本，解析器认三种写法：SGF、JSON、人类坐标（H8 / 7,7 / 7 7）。
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from .gomoku import rules
from .gomoku.position import Position
from .paths import DATA_DIR

SEED_FILE = Path(__file__).resolve().parent / "gomoku" / "corpus_seed.json"
USER_FILE = DATA_DIR / "corpus.json"

MAX_MOVES = 225
MAX_TEXT = 64 * 1024

_LOCK = threading.Lock()
_CACHE: dict | None = None

_SGF_MOVE = re.compile(r";\s*([BW])\s*\[([a-oA-O]{0,2})\]")


class CorpusError(ValueError):
    """棋谱读不出来时给一句人话。"""


# --------------------------------------------------------------------- 解析


def _finish(moves: list[list[int]], *, rule: str = rules.RENJU) -> dict:
    """重放一遍，验合法性并算出结果。顺带砍掉连五之后的废手。"""
    pos = Position(rule)
    clean: list[list[int]] = []
    winner = None
    reason = "棋谱未分胜负"
    for i, (x, y) in enumerate(moves):
        if not (0 <= x < 15 and 0 <= y < 15):
            raise CorpusError(f"第 {i + 1} 手坐标越界：({x},{y})")
        idx = y * 15 + x
        if pos.board[idx] != 0:
            raise CorpusError(f"第 {i + 1} 手落在已有棋子的位置：({x},{y})")
        color = 1 if i % 2 == 0 else 2
        pos.place(idx, color)
        clean.append([x, y])
        verdict = rules.judge(pos.board, idx, color, rule)
        if verdict.finished:
            winner = verdict.winner
            reason = verdict.label
            break
    if len(clean) < 5:
        raise CorpusError("棋谱太短：至少要有 5 手才能用来学习")
    return {"moves": clean, "winner": winner, "reason": reason, "plies": len(clean)}


def _parse_sgf(text: str) -> list[list[int]]:
    out: list[list[int]] = []
    for _, coord_text in _SGF_MOVE.findall(text):
        if len(coord_text) < 2:
            continue
        # SGF 坐标：字母 a..o，两段都是 0 基
        out.append([ord(coord_text[0].lower()) - 97, ord(coord_text[1].lower()) - 97])
    return out


def _parse_json(text: str) -> list[list[int]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusError(f"JSON 解析失败：{exc.msg}") from exc
    if isinstance(data, dict):
        data = data.get("moves") or data.get("棋谱") or []
    if not isinstance(data, list):
        raise CorpusError("JSON 里要是一个落子数组")
    out: list[list[int]] = []
    for i, item in enumerate(data):
        if isinstance(item, dict):
            try:
                out.append([int(item["x"]), int(item["y"])])
            except (KeyError, TypeError, ValueError) as exc:
                raise CorpusError(f"第 {i + 1} 项缺少 x / y") from exc
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            out.append([int(item[0]), int(item[1])])
        elif isinstance(item, str):
            parsed = _parse_text(item)
            if len(parsed) != 1:
                raise CorpusError(f"第 {i + 1} 项不是单个坐标")
            out.append(parsed[0])
        else:
            raise CorpusError(f"第 {i + 1} 项格式不认识")
    return out


def _parse_text(text: str) -> list[list[int]]:
    """人类写法。先试字母列号（H8），再退回纯数字（7,7）。"""
    body = re.sub(r"^\s*\d+[.、)]\s*", "", text, flags=re.MULTILINE)
    letters = re.findall(r"(?<![A-Za-z0-9])([a-oA-O])\s*(\d{1,2})(?![\d])", body)
    if letters:
        out = []
        for col, row in letters:
            x = ord(col.lower()) - 97
            y = int(row) - 1
            out.append([x, y])
        return out

    nums = re.findall(r"(\d{1,2})\s*[,;:\s]\s*(\d{1,2})", body)
    if nums:
        return [[int(a), int(b)] for a, b in nums]

    raise CorpusError("没找到坐标。支持 SGF、JSON、以及 H8 / 7,7 这样的写法")


def parse(text: str) -> dict:
    """把一段文本解析成一条棋谱。"""
    if not text or not text.strip():
        raise CorpusError("内容为空")
    if len(text) > MAX_TEXT:
        raise CorpusError(f"内容太大，最多 {MAX_TEXT // 1024} KB")
    raw = text.strip()
    if raw.startswith("(") or raw.startswith(";"):
        moves = _parse_sgf(raw)
    elif raw.startswith("[") or raw.startswith("{"):
        moves = _parse_json(raw)
    else:
        moves = _parse_text(raw)
    if not moves:
        raise CorpusError("没解析出任何落子")
    if len(moves) > MAX_MOVES:
        raise CorpusError("超过 225 手，不可能是 15 路棋谱")
    return _finish(moves)


# --------------------------------------------------------------------- 存取


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load() -> dict:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    seed = _read_json(SEED_FILE)
    user = _read_json(USER_FILE)
    items = []
    for item in seed.get("items", []) + user.get("items", []):
        if not isinstance(item, dict) or not item.get("moves"):
            continue
        items.append(_normalise(item))
    _CACHE = {"items": items}
    return _CACHE


def _normalise(item: dict) -> dict:
    moves = [[int(m[0]), int(m[1])] for m in item.get("moves", []) if len(m) >= 2]
    return {
        "id": str(item.get("id") or f"u-{uuid.uuid4().hex[:8]}"),
        "name": str(item.get("name") or "未命名棋谱")[:60],
        "source": "builtin" if item.get("source") == "builtin" else "upload",
        "author": str(item.get("author") or "")[:40],
        "note": str(item.get("note") or "")[:160],
        "moves": moves,
        "winner": item.get("winner"),
        "reason": str(item.get("reason") or ""),
        "created_at": float(item.get("created_at") or time.time()),
    }


def _user_items() -> list[dict]:
    return [i for i in _load()["items"] if i["source"] == "upload"]


def _write_user(items: list[dict]) -> None:
    USER_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "items": items}
    tmp = USER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(USER_FILE)


def _view(item: dict, *, preview: int = 0) -> dict:
    moves = item["moves"]
    out = {
        "id": item["id"],
        "name": item["name"],
        "source": item["source"],
        "author": item["author"],
        "note": item["note"],
        "plies": len(moves),
        "winner": item["winner"],
        "reason": item["reason"],
        "created_at": item["created_at"],
    }
    out["moves"] = moves[:preview] if preview else moves
    return out


def items(*, preview: int = 0) -> list[dict]:
    with _LOCK:
        data = _load()
        return [_view(i, preview=preview) for i in sorted(data["items"], key=lambda x: (x["source"] != "builtin", -x["created_at"]))]


def get(item_id: str) -> dict | None:
    with _LOCK:
        for item in _load()["items"]:
            if item["id"] == item_id:
                return _view(item)
    return None


def _result_note(winner: int | None, reason: str) -> str:
    """一句话说明这盘棋怎么结束的。

    刻意不带手数：列表里已经有一列「34 手」，备注再写一遍就是同一个数字出现两次。
    和棋也不加「未分胜负 ·」前缀——reason 本身就是「满盘未分胜负」，加完会读成
    「未分胜负 · 满盘未分胜负」。
    """
    side = "黑胜" if winner == 1 else "白胜" if winner == 2 else ""
    if not side:
        return reason or "未分胜负"
    return f"{side} · {reason}" if reason else side


def add(name: str, text: str, *, author: str = "") -> dict:
    parsed = parse(text)
    with _LOCK:
        data = _load()
        item = _normalise(
            {
                "id": f"u-{uuid.uuid4().hex[:8]}",
                "name": name or "未命名棋谱",
                "source": "upload",
                "author": author,
                "note": _result_note(parsed["winner"], parsed["reason"]),
                "moves": parsed["moves"],
                "winner": parsed["winner"],
                "reason": parsed["reason"],
                "created_at": time.time(),
            }
        )
        users = _user_items() + [item]
        _write_user(users)
        data["items"].append(item)
        return _view(item)


def delete(item_id: str) -> bool:
    with _LOCK:
        data = _load()
        target = next((i for i in data["items"] if i["id"] == item_id), None)
        if target is None:
            return False
        if target["source"] == "builtin":
            raise CorpusError("内置棋谱不能删除")
        data["items"] = [i for i in data["items"] if i["id"] != item_id]
        _write_user(_user_items())
        return True


def summary() -> dict:
    with _LOCK:
        data = _load()
        builtin = [i for i in data["items"] if i["source"] == "builtin"]
        upload = [i for i in data["items"] if i["source"] == "upload"]
        return {
            "total": len(data["items"]),
            "builtin": len(builtin),
            "upload": len(upload),
            "plies": sum(len(i["moves"]) for i in data["items"]),
        }


def training_set() -> list[dict]:
    with _LOCK:
        return [dict(i) for i in _load()["items"] if len(i["moves"]) >= 6]


def refresh() -> None:
    """丢掉缓存，下次访问重新读盘。手改了 data/corpus.json 或换数据目录之后用。"""
    global _CACHE
    with _LOCK:
        _CACHE = None
