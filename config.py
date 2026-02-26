"""
配置管理：集中加载 .env 并暴露项目级常量。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# 读取 .env（若不存在不报错）
load_dotenv(BASE_DIR / ".env")

# 优先使用 /data（HF Spaces 持久卷）或环境变量 BILVIEW_STORAGE_DIR，自适应本地/云端
DEFAULT_STORAGE_ROOT = Path("/data") if Path("/data").exists() else BASE_DIR
STORAGE_ROOT = Path(
    os.getenv("BILVIEW_STORAGE_DIR", DEFAULT_STORAGE_ROOT)
).expanduser().resolve()
DATA_DIR = STORAGE_ROOT / "data"
DOWNLOAD_DIR = STORAGE_ROOT / "downloads"
DB_PATH = DATA_DIR / "app.db"

DEFAULT_LLM_API_URL = "https://x666.me/v1/chat/completions"
DEFAULT_LLM_MODEL = "gemini-2.5-pro-1m"

# 重试配置
RETRY_MAX_ATTEMPTS_DOWNLOAD = 3  # 下载最大重试次数
RETRY_MAX_ATTEMPTS_API = 4  # API 调用最大重试次数
RETRY_WAIT_MIN = 1  # 最小等待时间（秒）
RETRY_WAIT_MAX_DOWNLOAD = 30  # 下载最大等待时间（秒）
RETRY_WAIT_MAX_API = 20  # API 最大等待时间（秒）

# 确保数据与下载目录存在（兼容 /data 持久卷）
DATA_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


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
