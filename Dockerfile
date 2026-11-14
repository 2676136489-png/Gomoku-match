# 前端是原生 JS 静态多页站（web/site），不需要 Node 构建阶段。
# 运行镜像只装 Python 与运行时依赖，静态文件直接复制进去。
FROM python:3.13-slim AS runtime

# 库、上传的棋谱、训练出来的权重表都落在 GOMOKU_DATA 下，
# 挂一个卷就能把「这个实例学到的东西」整体搬走。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    GOMOKU_DATA=/data \
    GOMOKU_DB=/data/gomoku.db \
    GOMOKU_LOG=INFO

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./server/
COPY serve.py ./
COPY engine/ ./engine/
COPY web/site/ ./web/site/

# 非 root 跑起来。数据目录单独建，挂卷的时候权限才对得上。
RUN useradd --create-home --uid 10001 gomoku \
    && mkdir -p /data \
    && chown -R gomoku:gomoku /data /app
USER gomoku

EXPOSE 8000

# 用 readiness 而不是 health：容器还在跑但数据库不可写时，这个探针会先把流量摘掉。
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/ready" || exit 1

CMD ["python", "serve.py"]
