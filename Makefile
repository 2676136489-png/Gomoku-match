.PHONY: help install build serve dev test test-fast docker docker-run clean

PY := python
PORT ?= 8000

help:
	@echo "install    安装依赖（Python + 前端）"
	@echo "build      构建前端到 web/dist"
	@echo "serve      单端口启动（:$(PORT)）"
	@echo "dev        起后端 + Vite 双端口开发"
	@echo "test       跑全部测试（自带测试服务）"
	@echo "test-fast  跳过端到端套件"
	@echo "docker     构建镜像"
	@echo "clean      清掉构建产物与缓存（不动 data/）"

install:
	$(PY) -m pip install -r requirements.txt
	cd web && npm install

build:
	cd web && npm run build

serve: build
	PORT=$(PORT) $(PY) serve.py

dev:
	@echo "后端 :$(PORT)，前端 :5173，Ctrl-C 一起停"
	@set -e; \
	PORT=$(PORT) $(PY) serve.py & \
	BACK=$$!; \
	trap 'kill $$BACK 2>/dev/null || true' EXIT INT TERM; \
	cd web && npm run dev

test:
	$(PY) tools/run_tests.py

test-fast:
	$(PY) tools/run_tests.py --fast

docker:
	docker build -t gomoku-match:4.0.0 .

docker-run:
	docker compose up -d

clean:
	rm -rf web/dist web/node_modules __pycache__ server/__pycache__ server/api/__pycache__ server/gomoku/__pycache__ .tmp
