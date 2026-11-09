"""运行时数据目录。

所有「会写、但不进仓库」的文件都挂在这里：数据库、上传的棋谱、训练出来的
权重表。集中成一个常量，是因为它们必须一起搬家——测试用临时库却把权重表写进
开发目录，下一次真人打开页面看到的就是测试训出来的模型。

`GOMOKU_DATA` 换目录，`GOMOKU_DB` 单独换库文件（它优先级更高）。两个都有是
为了兼容旧脚本：以前只有 GOMOKU_DB，现在只设它也不会失效。
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("GOMOKU_DATA") or ROOT / "data")


def data_file(name: str) -> Path:
    return DATA_DIR / name
