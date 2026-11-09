"""启动入口。

    python serve.py

只监听一个端口，前端构建产物由同一个进程托管。

.env 会在这里读进 os.environ：云端发布只注入 PORT，模型密钥这类东西没法从
平台侧传进来，只能随项目目录一起上传。已存在的环境变量优先，不会被 .env 覆盖。
"""

import os
from pathlib import Path


def load_dotenv(path: Path = Path(".env")) -> int:
    """极简 .env 解析：# 开头是注释，KEY=VALUE，值两侧的引号会被去掉。

    只做 setdefault，不动已有的环境变量——容器里显式给定的值优先级更高。
    返回实际写入的条数，方便启动日志里核对。
    """
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


# 必须在导入 server.app 之前完成，模块级读环境变量的地方才不会拿到空值。
_DOTENV_COUNT = load_dotenv()

import uvicorn  # noqa: E402

from server.app import app  # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    if _DOTENV_COUNT:
        print(f"[serve] .env 已加载 {_DOTENV_COUNT} 项", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")
