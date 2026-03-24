"""
模块描述：调用外部大模型接口（x666.me / gemini-2.5-pro-1m）对长转录文本进行总结。

特点与约束：
1. 采用同步 HTTP 请求，Token Bucket 速率限制（允许适度突发）。
2. 支持自定义 System Prompt、温度、模型名与超时。
3. 支持从环境变量 `X666_API_KEY` 读取密钥，若缺省则使用文档提供的默认 key。
4. 自动重试机制：区分可重试错误（429/5xx/网络）和不可重试错误（4xx），使用指数退避策略。

@author 开发
@date 2026-02-23
@version v2.0 (Token Bucket 限速，支持适度突发)
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from config import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL, X666_API_KEY, YUNWU_API_KEY
from utils.retry_helper import api_retry_decorator

logger = logging.getLogger(__name__)

DEFAULT_API_URL = DEFAULT_LLM_API_URL
DEFAULT_MODEL = DEFAULT_LLM_MODEL
DEFAULT_API_KEY = None  # 不再内置默认密钥，必须由环境变量或显式传入
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 150
DEFAULT_MAX_TOKENS = 8192  # 默认最大输出 token 数
RATE_LIMIT_SECONDS = 10  # Token Bucket 补充间隔（秒）
BURST_SIZE = 3  # 允许的突发请求数
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent.parent / "docs" / "default_prompt.md"
_FALLBACK_SYSTEM_PROMPT = """你是一个专业的长视频笔记助手，请将输入的完整转录文本，提炼为结构化笔记，需包含：
1) 内容摘要：3-5 条
2) 核心亮点/金句：2-4 条
3) 结论与行动建议：2-3 条
要求：用中文输出；保持事实准确，不臆测；必要时保留数字、公式或关键引用。"""


class _TokenBucket:
    """线程安全的 Token Bucket 实现，支持适度突发。"""

    def __init__(self, rate: float, capacity: int):
        self._rate = rate  # 每秒补充的 token 数
        self._capacity = capacity  # 桶容量
        self._tokens = float(capacity)
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60) -> bool:
        """获取一个 token，超时返回 False。"""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # 等待到下一个 token 可用或超时
            wait_time = min((1 - self._tokens) / self._rate, remaining)
            if wait_time > 0:
                time.sleep(min(wait_time, 0.1))  # 最多等 0.1 秒再检查

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_update = now


# 全局 Token Bucket：允许适度突发而非严格串行
_bucket = _TokenBucket(rate=1.0 / RATE_LIMIT_SECONDS, capacity=BURST_SIZE)


def _load_default_system_prompt(prompt_path: Path = DEFAULT_PROMPT_PATH) -> str:
    """
    从 docs/default_prompt.md 读取默认 System Prompt，读取失败时回退内置模板。

    Args:
        prompt_path: 默认 Prompt 文件路径。

    Returns:
        默认 System Prompt 文本。
    """
    try:
        content = prompt_path.read_text(encoding="utf-8").strip()
        if content:
            return content
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取默认 Prompt 文件失败，回退内置 Prompt：%s", exc)
        return _FALLBACK_SYSTEM_PROMPT

    logger.warning("默认 Prompt 文件为空，已回退内置 Prompt：%s", prompt_path)
    return _FALLBACK_SYSTEM_PROMPT


_DEFAULT_SYSTEM_PROMPT = _load_default_system_prompt()


def get_default_system_prompt() -> str:
    """返回当前默认 System Prompt。"""
    return _DEFAULT_SYSTEM_PROMPT


def generate_summary(
    text: str,
    *,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """
    将长文本发送至外部 LLM，返回结构化总结。

    Args:
        text: 待总结的完整转录文本。
        system_prompt: 可选自定义系统提示词，缺省使用 docs/default_prompt.md 的默认提示。
        model: 模型名称。
        api_key: API 密钥，默认优先读取环境变量 X666_API_KEY，其次使用文档提供的默认 key。
        temperature: 采样温度。
        max_tokens: 最大输出 token 数。
        timeout: 单次请求超时时间（秒）。
        api_url: Chat Completions 接口地址。

    Returns:
        模型返回的总结文本。

    Raises:
        RuntimeError: 当请求失败或响应格式异常时抛出。
    """
    sanitized_text = _sanitize_text(text)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt or get_default_system_prompt()},
            {"role": "user", "content": sanitized_text},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    chosen_key = api_key or _get_env_key() or DEFAULT_API_KEY
    if not chosen_key:
        raise RuntimeError("缺少 API Key：请设置环境变量 X666_API_KEY 或在调用时显式传入。")
    _respect_rate_limit()
    return _call_api(payload, chosen_key, timeout, api_url)


@api_retry_decorator
def _call_api(payload: dict, api_key: str, timeout: int, api_url: str) -> str:
    """
    执行 HTTP 请求并解析响应。

    自动重试策略：
    - 最大重试 4 次
    - 指数退避：1-20 秒
    - 可重试错误：429、5xx、网络超时
    - 不可重试错误：4xx（除 429）
    """
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
        ),
    }
    request = urllib.request.Request(api_url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.warning(f"HTTP {exc.code} 错误: {detail}")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        logger.warning(f"网络请求失败: {exc}")
        raise RuntimeError(f"Request failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"请求异常: {exc}")
        raise RuntimeError(f"Request error: {exc}") from exc

    try:
        parsed = json.loads(raw)
        return parsed["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unexpected response: {raw!r}") from exc


def _respect_rate_limit() -> None:
    """Token Bucket 速率限制：允许最多 BURST_SIZE 个突发请求。"""
    if not _bucket.acquire(timeout=60):
        raise RuntimeError("API 速率限制超时：请减少并发请求数")


def _get_env_key() -> Optional[str]:
    """读取环境变量中的密钥，未设置则返回 None。"""
    return os.getenv("YUNWU_API_KEY") or YUNWU_API_KEY or os.getenv("X666_API_KEY") or X666_API_KEY


def _sanitize_text(text: str, max_url_len: int = 120) -> str:
    """
    对输入文本进行简单清洗：截断过长 URL，避免上游因异常参数报错。

    Args:
        text: 原始转录文本。
        max_url_len: URL 允许的最大长度，超出将被截断并追加占位提示。
    """

    def _truncate(match: re.Match[str]) -> str:
        url = match.group(0)
        if len(url) <= max_url_len:
            return url
        return url[:max_url_len] + "...[truncated]"

    return re.sub(r"https?://\\S+", _truncate, text)
