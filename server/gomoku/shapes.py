"""棋形识别。

这里的做法和常见的"模板表"不一样：不枚举棋形，而是问一个问题——
「这条线上，把某个空点补成自己的子，能不能变成更强的棋形？」

具体地把一条以落点为中心、长度 11 的窗口写成字符串：
    0 = 空点   1 = 自己的子   2 = 阻挡（对手的子或棋盘外）

然后递归地用"补一格"来判定：

    连五   : 窗口里已经存在含中心的连续五个 1
    活四   : 存在两个以上的空点，补上就能连五（两头都能成五，堵不住）
    冲四   : 存在一个空点，补上就能连五
    活三   : 存在一个空点，补上之后能变成活四
    眠三   : 存在一个空点，补上之后能变成冲四
    活二   : 存在一个空点，补上之后能变成活三
    眠二   : 存在一个空点，补上之后能变成眠三

好处是不会漏棋形、不会把"活二 + 活三"这种累加误判成一个活三——
每一档都是独立判定，不存在分值叠加导致的降级/升级错误。
所有判定函数都带 lru_cache，同一条线只算一次，后续命中缓存。
"""

from functools import lru_cache

EMPTY_CH = "0"
OWN_CH = "1"
BLOCK_CH = "2"

CENTER = 5
WIN_LEN = 5

# 棋形等级，数值越大越强
NONE = 0
TWO = 1
OPEN_TWO = 2
THREE = 3
OPEN_THREE = 4
FOUR = 5
OPEN_FOUR = 6
FIVE = 7

SHAPE_NAMES = {
    NONE: "无",
    TWO: "眠二",
    OPEN_TWO: "活二",
    THREE: "眠三",
    OPEN_THREE: "活三",
    FOUR: "冲四",
    OPEN_FOUR: "活四",
    FIVE: "连五",
}

# 用于"补一格"判定的目标等级
_GOALS = (OPEN_FOUR, FOUR, OPEN_THREE, THREE)


@lru_cache(maxsize=1 << 17)
def _has_five(s: str) -> bool:
    """窗口里是否存在包含中心的连续五个己方子。"""
    for start in range(CENTER - WIN_LEN + 1, CENTER + 1):
        if s[start : start + WIN_LEN] == OWN_CH * WIN_LEN:
            return True
    return False


@lru_cache(maxsize=1 << 17)
def win_points(s: str) -> int:
    """返回一个位掩码：哪些空点补上己方子就能连五（且成五的一串包含中心）。"""
    mask = 0
    for i, ch in enumerate(s):
        if ch != EMPTY_CH:
            continue
        if _has_five(s[:i] + OWN_CH + s[i + 1 :]):
            mask |= 1 << i
    return mask


@lru_cache(maxsize=1 << 17)
def _reachable(s: str, goal: int) -> bool:
    """补一个空点之后，能否得到等级 goal 的棋形。"""
    for i, ch in enumerate(s):
        if ch != EMPTY_CH:
            continue
        if _rank(s[:i] + OWN_CH + s[i + 1 :]) >= goal:
            return True
    return False


@lru_cache(maxsize=1 << 17)
def _rank(s: str) -> int:
    """棋形等级。这是一个纯函数，也是整个评估层唯一的真相来源。"""
    if _has_five(s):
        return FIVE

    mask = win_points(s)
    if mask:
        # 有两个以上的成五点就是活四，一个就是冲四
        return OPEN_FOUR if mask.bit_count() >= 2 else FOUR

    if _reachable(s, OPEN_FOUR):
        return OPEN_THREE
    if _reachable(s, FOUR):
        return THREE
    if _reachable(s, OPEN_THREE):
        return OPEN_TWO
    if _reachable(s, THREE):
        return TWO
    return NONE


def rank(s: str) -> int:
    return _rank(s)


def cache_size() -> int:
    return _rank.cache_info().currsize


def clear_cache() -> None:
    for fn in (_rank, _reachable, win_points, _has_five):
        fn.cache_clear()
