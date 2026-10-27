"""启动入口。

    python serve.py

只监听一个端口，前端构建产物由同一个进程托管。
"""

import os

import uvicorn

from server.app import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")
