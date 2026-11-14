.PHONY: help install serve test test-fast docker docker-run clean

PY := python
PORT ?= 8000

help:
	@echo "install    安装 Python 依赖"
	@echo "serve      单端口启动（:$(PORT)）"
	@echo "test       跑全部测试（自带测试服务）"
	@echo "test-fast  跳过端到端套件"
	@echo "docker     构建镜像"
	@echo "docker-run docker compose up -d"
	@echo "clean      清理缓存与旧前端依赖（不动 web/site 与 data/）"

install:
	$(PY) -m pip install -r requirements.txt

serve:
	PORT=$(PORT) $(PY) serve.py

test:
	$(PY) tools/run_tests.py

test-fast:
	$(PY) tools/run_tests.py --fast

docker:
	docker build -t gomoku-match:4.0.0 .

docker-run:
	docker compose up -d

clean:
	rm -rf web/node_modules __pycache__ server/__pycache__ server/api/__pycache__ server/gomoku/__pycache__ .tmp
