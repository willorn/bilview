"""
模块描述：调用外部大模型接口（x666.me / gemini-2.5-pro-1m）对长转录文本进行总结。

特点与约束：
1. 采用同步 HTTP 请求，默认 20 秒速率限制（简单全局限流）。
2. 支持自定义 System Prompt、温度、模型名与超时。
3. 支持从环境变量 `X666_API_KEY` 读取密钥，若缺省则使用文档提供的默认 key。

@author 开发
@date 2026-02-23
@version v1.0
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

from config import DEFAULT_LLM_API_URL, DEFAULT_LLM_MODEL, X666_API_KEY

DEFAULT_API_URL = DEFAULT_LLM_API_URL
DEFAULT_MODEL = DEFAULT_LLM_MODEL
DEFAULT_API_KEY = None  # 不再内置默认密钥，必须由环境变量或显式传入
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TIMEOUT_SECONDS = 150
RATE_LIMIT_SECONDS = 20

_DEFAULT_SYSTEM_PROMPT = """你是一个专业的长视频笔记助手，请将输入的完整转录文本，提炼为结构化笔记，需包含：
1) 内容摘要：3-5 条
2) 核心亮点/金句：2-4 条
3) 结论与行动建议：2-3 条
要求：用中文输出；保持事实准确，不臆测；必要时保留数字、公式或关键引用。"""

_lock = threading.Lock()
_last_call_ts = 0.0


def generate_summary(
    text: str,
    *,
    system_prompt: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """
    将长文本发送至外部 LLM，返回结构化总结。

    Args:
        text: 待总结的完整转录文本。
        system_prompt: 可选自定义系统提示词，缺省使用内置默认提示。
        model: 模型名称。
        api_key: API 密钥，默认优先读取环境变量 X666_API_KEY，其次使用文档提供的默认 key。
        temperature: 采样温度。
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
            {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": sanitized_text},
        ],
        "temperature": temperature,
    }
    chosen_key = api_key or _get_env_key() or DEFAULT_API_KEY
    if not chosen_key:
        raise RuntimeError("缺少 API Key：请设置环境变量 X666_API_KEY 或在调用时显式传入。")
    _respect_rate_limit()
    return _call_api(payload, chosen_key, timeout, api_url)


def _call_api(payload: dict, api_key: str, timeout: int, api_url: str) -> str:
    """执行 HTTP 请求并解析响应。"""
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
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Request error: {exc}") from exc

    try:
        parsed = json.loads(raw)
        return parsed["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Unexpected response: {raw!r}") from exc


def _respect_rate_limit() -> None:
    """简单全局速率限制：两次调用间隔至少 RATE_LIMIT_SECONDS。"""
    global _last_call_ts
    with _lock:
        now = time.time()
        wait_for = _last_call_ts + RATE_LIMIT_SECONDS - now
        if wait_for > 0:
            time.sleep(wait_for)
        _last_call_ts = time.time()


def _get_env_key() -> Optional[str]:
    """读取环境变量中的密钥，未设置则返回 None。"""
    return os.getenv("X666_API_KEY") or X666_API_KEY


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
import os
