"""
配置管理：集中加载 .env 并暴露项目级常量。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOWNLOAD_DIR = BASE_DIR / "downloads"
DB_PATH = DATA_DIR / "app.db"

DEFAULT_LLM_API_URL = "https://x666.me/v1/chat/completions"
DEFAULT_LLM_MODEL = "gemini-2.5-pro-1m"

# 读取 .env（若不存在不报错）
load_dotenv(BASE_DIR / ".env")


def get_api_key(env_name: str, default: Optional[str] = None) -> Optional[str]:
    """通用读取 API Key 的函数。"""
    return os.getenv(env_name, default)


# 项目内默认使用的 key（可在 .env 中覆盖）
X666_API_KEY = get_api_key("X666_API_KEY")


def ensure_api_key_present() -> None:
    """启动时校验 API Key 是否存在，缺失则抛出友好异常。"""
    if not X666_API_KEY:
        raise RuntimeError(
            "缺少 X666_API_KEY，请在 .env 中配置或设置环境变量。"
        )
