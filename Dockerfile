# 两阶段构建：前端在 node 镜像里编译成静态文件，运行镜像里只留 Python 和 dist。
# 不这么做的话镜像里会多出 node_modules（几百 MB）和整个工具链。
FROM node:22-alpine AS web

WORKDIR /build
COPY web/package.json web/package-lock.json* ./
# 有 lock 文件就走 ci，保证可复现；仓库里还没提交 lock 时退回 install
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY web/ ./
RUN npm run build


FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
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
COPY --from=web /build/dist ./web/dist/

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
