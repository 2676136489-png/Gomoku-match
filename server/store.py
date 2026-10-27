"""对局存储。

对局是「热态在内存、冷态在库」的两层结构：

- 内存里放 `Game` 对象，因为搜索、胜率曲线、教练点评都要频繁读 `Position`，
  每次都从数据库重放一遍不现实。
- 每落一手写一行 `moves`，写完就完事，不做事务性回写其他字段。
- 内存里没有的局（进程重启、或者查历史）走 `load()`：从 `moves` 表重放出一个
  `Position`。200 手的重放是毫秒级，比任何快照方案都简单。

这里有个明确的取舍：数据库是「记录」，内存是「状态」。写库失败时对局继续，
只在日志里留一条——一盘正在下的棋不该因为磁盘抖动而中断，棋谱丢了比棋下不下去
的损失小。真正需要强一致的是天梯分数，那个在对局结束时单独走事务。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from . import repo
from .gomoku import rules
from .gomoku.agent import DIFFICULTY, PERSONAS
from .gomoku.llm import LLMConfig
from .gomoku.position import BLACK, CELLS, OPPONENT, SIZE, WHITE, Position, coord

log = logging.getLogger("gomoku.store")

MAX_HOT_GAMES = 400


@dataclass
class Game:
    id: str
    mode: str
    difficulty: int
    persona: str
    human_color: int
    use_llm: bool
    rule: str = rules.RENJU
    strict_ban: bool = False
    step: bool = False
    owner: str | None = None
    owner_id: int | None = None
    black_id: int | None = None
    white_id: int | None = None
    rated: bool = False
    room_id: str | None = None
    recorded: bool = False
    settled: bool = False
    pos: Position = field(default_factory=Position)
    moves: list[dict] = field(default_factory=list)
    status: str = "playing"
    winner: int | None = None
    win_line: list[list[int]] = field(default_factory=list)
    result_reason: str = ""
    created_at: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    _ban_cache: tuple[tuple[int, int], list[dict]] | None = field(default=None, repr=False)
    pending: dict | None = None
    series_cache: list[dict] = field(default_factory=list)
    series_pos: Position | None = field(default=None, repr=False)

    # 计时：只在联网对局里启用。clocks 是双方各自剩余的秒数，
    # turn_started 是本回合开始的墙钟——剩余时间不落库，落库的是每手结算后的值。
    clock_base: int = 0
    clock_inc: int = 0
    clocks: dict[int, float] = field(default_factory=dict)
    turn_started: float | None = None

    def __post_init__(self) -> None:
        self.pos.rule = self.rule
        if self.clock_base:
            self.clocks = {BLACK: float(self.clock_base), WHITE: float(self.clock_base)}
            self.turn_started = time.time()

    # ---------------------------------------------------------------- 基础

    @property
    def turn(self) -> int:
        return BLACK if len(self.moves) % 2 == 0 else WHITE

    @property
    def rule_label(self) -> str:
        return "国际连珠" if self.rule == rules.RENJU else "自由规则"

    @property
    def timed(self) -> bool:
        return self.clock_base > 0

    def player_of(self, user_id: int | None) -> int | None:
        if user_id is None:
            return None
        if self.black_id == user_id:
            return BLACK
        if self.white_id == user_id:
            return WHITE
        return None

    # ---------------------------------------------------------------- 计时

    def clock_snapshot(self) -> dict | None:
        """双方剩余秒数 + 本回合已经用掉多少。前端画倒计时用这个。"""
        if not self.timed:
            return None
        spent = (time.time() - self.turn_started) if self.turn_started else 0.0
        live = dict(self.clocks)
        live[self.turn] = max(0.0, live.get(self.turn, 0.0) - spent)
        return {
            "base": self.clock_base,
            "inc": self.clock_inc,
            "black": round(live.get(BLACK, 0.0), 1),
            "white": round(live.get(WHITE, 0.0), 1),
            "turn": self.turn,
            "spent": round(spent, 1),
        }

    def _settle_clock(self, color: int) -> int:
        """一手落完，扣掉本回合耗时、加上读秒。返回这一手用了多少毫秒。"""
        if not self.timed or self.turn_started is None:
            return 0
        now = time.time()
        spent = max(0.0, now - self.turn_started)
        self.clocks[color] = max(0.0, self.clocks.get(color, 0.0) - spent) + self.clock_inc
        self.turn_started = now
        try:
            repo.update_clocks(
                self.id,
                self.clocks.get(BLACK, 0.0),
                self.clocks.get(WHITE, 0.0),
                self.turn_started,
            )
        except Exception:
            log.warning("计时写库失败 game=%s", self.id, exc_info=True)
        return int(spent * 1000)

    def flagged(self) -> int | None:
        """谁超时了。轮次方读秒归零即判负。"""
        if not self.timed or self.status != "playing" or self.turn_started is None:
            return None
        left = self.clocks.get(self.turn, 0.0) - (time.time() - self.turn_started)
        return self.turn if left <= 0 else None

    # ---------------------------------------------------------------- 落子

    def apply(
        self,
        idx: int,
        color: int,
        *,
        comment: str = "",
        source: str = "",
        think_ms: int = 0,
        ban: str | None = None,
        persist: bool = True,
    ) -> None:
        if self.timed and color == self.turn and think_ms <= 0:
            think_ms = self._settle_clock(color)

        self.pos.place(idx, color)
        x, y = coord(idx)
        entry = {
            "x": x,
            "y": y,
            "color": color,
            "source": source,
            "comment": comment,
            "think_ms": think_ms,
            "ban": bool(ban),
            "eval": self.pos.evaluate(BLACK),
        }
        if ban:
            # 禁手判负是终局，静态分不能还显示「黑棋形势大好」，曲线得跟着掉下去
            entry["eval"] = -10_000_000 if color == BLACK else 10_000_000
        ply = len(self.moves)
        self.moves.append(entry)
        self._ban_cache = None
        self.pending = None

        if persist:
            try:
                repo.add_move(
                    self.id,
                    ply,
                    x,
                    y,
                    color,
                    source=source,
                    comment=comment,
                    think_ms=think_ms,
                    eval=entry["eval"],
                )
            except Exception:  # 写库失败不该打断正在下的棋
                log.warning("落子写库失败 game=%s ply=%s", self.id, ply, exc_info=True)

        if ban:
            self.status = "finished"
            self.winner = OPPONENT[color]
            self.result_reason = f"黑棋{rules.BAN_LABELS[ban]}，判负"
            self.turn_started = None
            return

        verdict = rules.judge(self.pos.board, idx, color, self.rule)
        if verdict.finished:
            self.status = "finished"
            self.winner = verdict.winner
            self.result_reason = verdict.label
            self.turn_started = None
            if verdict.line:
                self.win_line = [[i % SIZE, i // SIZE] for i in verdict.line]
        elif self.pos.occupied == CELLS:
            self.status = "finished"
            self.result_reason = "棋盘已满，和棋"
            self.turn_started = None

    def resign(self, color: int) -> None:
        self.status = "finished"
        self.winner = OPPONENT[color]
        self.result_reason = "认输"
        self.turn_started = None

    def abort(self) -> None:
        self.status = "abandoned"
        self.result_reason = self.result_reason or "对手离开"
        self.turn_started = None

    def banned_points(self) -> list[dict]:
        """当前局面下黑棋的禁手点。只在轮到黑棋时算，其余时候返回空。"""
        if self.rule != rules.RENJU or self.status != "playing" or self.turn != BLACK:
            return []
        key = (self.pos.occupied, self.pos.hash)
        cached = self._ban_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        points = self.pos.banned_points()
        self._ban_cache = (key, points)
        return points

    # ---------------------------------------------------------------- 输出

    def snapshot(self) -> dict:
        last = self.moves[-1] if self.moves else None
        seat = None
        return {
            "id": self.id,
            "mode": self.mode,
            "difficulty": self.difficulty,
            "difficulty_label": DIFFICULTY.get(self.difficulty, DIFFICULTY[3])["label"],
            "persona": self.persona,
            "persona_label": PERSONAS.get(self.persona, PERSONAS["balanced"])["label"],
            "human_color": self.human_color,
            "use_llm": self.use_llm,
            "rule": self.rule,
            "rule_label": self.rule_label,
            "strict_ban": self.strict_ban,
            "step": self.step,
            "rated": self.rated,
            "room_id": self.room_id,
            "seat": seat,
            "pending": (
                {
                    "x": self.pending["x"],
                    "y": self.pending["y"],
                    "color": self.pending["color"],
                    "comment": self.pending.get("comment", ""),
                    "source": self.pending.get("source", ""),
                    "think_ms": self.pending.get("think_ms", 0),
                }
                if self.pending
                else None
            ),
            "board": list(self.pos.board),
            "turn": self.turn,
            "status": self.status,
            "winner": self.winner,
            "win_line": self.win_line,
            "result_reason": self.result_reason,
            "ban_loss": "禁手" in self.result_reason,
            "banned": self.banned_points(),
            "moves": self.moves,
            "last_move": {"x": last["x"], "y": last["y"]} if last else None,
            "occupied": self.pos.occupied,
            "clock": self.clock_snapshot(),
            "created_at": self.created_at,
        }


class GameStore:
    def __init__(self) -> None:
        self._games: dict[str, Game] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- 生命周期

    def create(
        self,
        *,
        mode: str,
        difficulty: int,
        persona: str,
        human_color: int,
        use_llm: bool,
        rule: str = rules.RENJU,
        strict_ban: bool = False,
        step: bool = False,
        owner: str | None = None,
        owner_id: int | None = None,
        black_id: int | None = None,
        white_id: int | None = None,
        rated: bool = False,
        room_id: str | None = None,
        clock_base: int = 0,
        clock_inc: int = 0,
        status: str = "playing",
    ) -> Game:
        game = Game(
            id=uuid.uuid4().hex[:12],
            mode=mode,
            difficulty=difficulty,
            persona=persona,
            human_color=human_color,
            use_llm=use_llm,
            rule=rule,
            strict_ban=strict_ban,
            step=step,
            owner=owner,
            owner_id=owner_id,
            black_id=black_id,
            white_id=white_id,
            rated=rated,
            room_id=room_id,
            status=status,
            clock_base=clock_base,
            clock_inc=clock_inc,
        )
        try:
            repo.create_game(
                {
                    "id": game.id,
                    "mode": mode,
                    "owner_id": owner_id,
                    "black_id": black_id,
                    "white_id": white_id,
                    "difficulty": difficulty,
                    "persona": persona,
                    "human_color": human_color,
                    "use_llm": use_llm,
                    "rule": rule,
                    "strict_ban": strict_ban,
                    "step": step,
                    "status": status,
                    "rated": rated,
                    "clock_base": clock_base,
                    "clock_inc": clock_inc,
                    "created_at": game.created_at,
                }
            )
        except Exception:
            log.warning("建局写库失败 game=%s", game.id, exc_info=True)

        self._remember(game)
        return game

    def _remember(self, game: Game) -> None:
        with self._lock:
            self._games[game.id] = game
            self._order.append(game.id)
            # 只淘汰内存里的热态，数据库里的记录一条不删
            while len(self._order) > MAX_HOT_GAMES:
                stale = self._order.pop(0)
                if stale != game.id:
                    self._games.pop(stale, None)

    def get(self, game_id: str) -> Game | None:
        game = self._games.get(game_id)
        if game is not None:
            return game
        return self.load(game_id)

    def load(self, game_id: str) -> Game | None:
        """从数据库重放出一局。进程重启之后、或者查历史棋谱时走这条路。"""
        row = repo.game_by_id(game_id)
        if row is None:
            return None
        game = Game(
            id=row["id"],
            mode=row["mode"],
            difficulty=row["difficulty"],
            persona=row["persona"],
            human_color=row["human_color"],
            use_llm=bool(row["use_llm"]),
            rule=row["rule"],
            strict_ban=bool(row["strict_ban"]),
            step=bool(row["step"]),
            owner_id=row["owner_id"],
            black_id=row["black_id"],
            white_id=row["white_id"],
            rated=bool(row["rated"]),
            status=row["status"],
            winner=row["winner"],
            result_reason=row["result_reason"],
            created_at=row["created_at"],
            clock_base=row["clock_base"] or 0,
            clock_inc=row["clock_inc"] or 0,
        )
        if row["clock_base"]:
            game.clocks = {
                BLACK: float(row["clock_black"] if row["clock_black"] is not None else row["clock_base"]),
                WHITE: float(row["clock_white"] if row["clock_white"] is not None else row["clock_base"]),
            }
            game.turn_started = row["turn_started"] if row["status"] == "playing" else None
        for ply, move in enumerate(repo.moves_of(game_id)):
            game.pos.place(move["y"] * SIZE + move["x"], move["color"])
            game.moves.append(
                {
                    "x": move["x"],
                    "y": move["y"],
                    "color": move["color"],
                    "source": move["source"],
                    "comment": move["comment"],
                    "think_ms": move["think_ms"],
                    "eval": move["eval"],
                    "ban": False,
                }
            )
        try:
            game.win_line = [
                [int(v) for v in pair] for pair in __import__("json").loads(row["win_line"] or "[]")
            ]
        except (ValueError, TypeError):
            game.win_line = []
        # 进程在落子之后、写终局状态之前挂掉时，库里是 playing 但棋盘已经连五。
        # 重放时补一次判定，避免这种局永远不算完。
        if game.status == "playing" and game.moves:
            last = game.moves[-1]
            verdict = rules.judge(
                game.pos.board, last["y"] * SIZE + last["x"], last["color"], game.rule
            )
            if verdict.finished:
                game.status = "finished"
                game.winner = verdict.winner
                game.result_reason = verdict.label
                if verdict.line:
                    game.win_line = [[i % SIZE, i // SIZE] for i in verdict.line]
        self._remember(game)
        return game

    def finish(self, game: Game, *, rated: bool = False, rating_before: float | None = None,
               rating_after: float | None = None) -> None:
        try:
            repo.finish_game(
                game.id,
                status=game.status,
                winner=game.winner,
                reason=game.result_reason,
                win_line=game.win_line,
                rated=rated,
                rating_before=rating_before,
                rating_after=rating_after,
            )
        except Exception:
            log.warning("终局写库失败 game=%s", game.id, exc_info=True)

    def stats(self) -> dict:
        counts = repo.count_games()
        return {
            "active_games": len(self._games),
            "finished_games": counts["games"],
            "llm": LLMConfig().describe(),
        }


def opponent_of(color: int) -> int:
    return OPPONENT[color]
