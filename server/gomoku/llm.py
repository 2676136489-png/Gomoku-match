"""OpenAI 兼容协议的模型客户端。

智谱、DeepSeek、OpenAI、本地 Ollama 都走同一套 /chat/completions，
所以这里只认三个环境变量，换厂商不用改代码：

    GOMOKU_LLM_BASE_URL   默认 https://open.bigmodel.cn/api/paas/v4
    GOMOKU_LLM_API_KEY
    GOMOKU_LLM_MODEL      默认 glm-4-flash

没有配 key 时 enabled 为 False，Agent 自动退回内置策略人格。
这个降级不是摆设——它保证项目 clone 下来就能跑完整流程。
"""

from __future__ import annotations

import json
import os
import re

import httpx

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"


class LLMConfig:
    def __init__(self) -> None:
        self.base_url = (
            os.environ.get("GOMOKU_LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self.api_key = (
            os.environ.get("GOMOKU_LLM_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ZHIPUAI_API_KEY")
            or ""
        )
        self.model = (
            os.environ.get("GOMOKU_LLM_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or DEFAULT_MODEL
        )
        self.timeout = float(os.environ.get("GOMOKU_LLM_TIMEOUT", "20"))

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def describe(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model if self.enabled else None,
            "base_url": self.base_url if self.enabled else None,
        }


class LLMError(RuntimeError):
    pass


def chat(messages: list[dict], *, temperature: float = 0.4, max_tokens: int = 512) -> str:
    cfg = LLMConfig()
    if not cfg.enabled:
        raise LLMError("未配置模型 API Key")

    payload = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(timeout=cfg.timeout) as client:
            resp = client.post(f"{cfg.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMError(f"模型调用失败: {exc}") from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"模型返回格式异常: {data}") from exc


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def extract_json(text: str) -> dict | None:
    """模型经常在 JSON 外面包一层 ```json 或者解释文字，这里做容错。"""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    match = _JSON_BLOCK.search(candidate)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
