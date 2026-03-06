"""
配置管理：集中加载 .env 并暴露项目级常量。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

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
TURSO_LOCAL_REPLICA_PATH = Path(
    os.getenv("TURSO_LOCAL_REPLICA_PATH", DATA_DIR / "turso_replica.db")
).expanduser().resolve()

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


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def get_api_key(env_name: str, default: Optional[str] = None) -> Optional[str]:
    """通用读取 API Key 的函数。"""
    return os.getenv(env_name, default)


def get_api_keys(env_name: str) -> List[str]:
    """读取逗号分隔的 API Key 列表。"""
    raw_value = os.getenv(env_name, "")
    if not raw_value:
        return []
    normalized = raw_value.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _merge_unique_keys(*groups: List[str]) -> List[str]:
    merged: List[str] = []
    for group in groups:
        for key in group:
            if key not in merged:
                merged.append(key)
    return merged


# 项目内默认使用的 key（可在 .env 中覆盖）
X666_API_KEY = get_api_key("X666_API_KEY")
GROQ_API_KEY = get_api_key("GROQ_API_KEY")
GROQ_API_KEYS = _merge_unique_keys(
    get_api_keys("GROQ_API_KEYS"),
    [GROQ_API_KEY] if GROQ_API_KEY else [],
)
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CLOUDFLARE_D1_DATABASE_ID = os.getenv("CLOUDFLARE_D1_DATABASE_ID")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN")
DB_AUTO_INIT_ON_STARTUP = _env_bool("DB_AUTO_INIT_ON_STARTUP", default=False)
DEFAULT_ASR_PROVIDER = os.getenv("ASR_PROVIDER", "groq").strip().lower() or "groq"
DEFAULT_GROQ_ASR_BASE_URL = (
    os.getenv("GROQ_ASR_BASE_URL", "https://api.groq.com/openai/v1").strip()
    or "https://api.groq.com/openai/v1"
)
DEFAULT_GROQ_ASR_MODEL = os.getenv("GROQ_ASR_MODEL", "whisper-large-v3-turbo").strip() or "whisper-large-v3-turbo"
ASR_REQUEST_TIMEOUT_SECONDS = _env_int("ASR_REQUEST_TIMEOUT_SECONDS", default=120)
TASK_EXECUTOR_MAX_WORKERS = _env_int("TASK_EXECUTOR_MAX_WORKERS", default=1)
TASK_EXECUTOR_POLL_INTERVAL_SECONDS = _env_float("TASK_EXECUTOR_POLL_INTERVAL_SECONDS", default=1.0)
TASK_EXECUTOR_TASK_TIMEOUT_SECONDS = _env_int("TASK_EXECUTOR_TASK_TIMEOUT_SECONDS", default=5400)
TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS = max(
    _env_int("TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS", default=1),
    0,
)


def ensure_api_key_present() -> None:
    """启动时校验 API Key 是否存在，缺失则抛出友好异常。"""
    if not X666_API_KEY:
        raise RuntimeError(
            "缺少 X666_API_KEY，请在 .env 中配置或设置环境变量。"
        )
