"""
模块描述：语音识别统一封装，默认使用 Groq ASR，并支持多 API Key 轮询。

设计要点：
1. provider 层与分片层解耦，转写流程只依赖 `transcribe_file` 接口。
2. Groq Provider 支持多 key 轮询；当单 key 限流或鉴权异常时自动切换。
3. 未配置 Groq key 时自动回退本地 Whisper，保证离线可用性。

@author 开发
@date 2026-03-04
@version v1.0
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Protocol, Sequence, Tuple

import openai
from openai import OpenAI
import torch
import whisper

import config as app_config

logger = logging.getLogger(__name__)


def _get_config_int(name: str, default: int) -> int:
    raw_value = getattr(app_config, name, default)
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return default


def _get_config_str(name: str, default: str, *, to_lower: bool = False) -> str:
    raw_value = getattr(app_config, name, default)
    normalized = str(raw_value).strip() if raw_value is not None else ""
    if to_lower:
        normalized = normalized.lower()
    return normalized or default


def _get_config_keys(name: str) -> List[str]:
    raw_value = getattr(app_config, name, [])
    if isinstance(raw_value, str):
        normalized = raw_value.replace("\n", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]
    if isinstance(raw_value, Sequence):
        return [
            normalized
            for normalized in (str(item).strip() for item in raw_value)
            if normalized
        ]
    return []


ASR_REQUEST_TIMEOUT_SECONDS = _get_config_int("ASR_REQUEST_TIMEOUT_SECONDS", 120)
DEFAULT_ASR_PROVIDER = _get_config_str("DEFAULT_ASR_PROVIDER", "groq", to_lower=True)
DEFAULT_GROQ_ASR_BASE_URL = _get_config_str(
    "DEFAULT_GROQ_ASR_BASE_URL",
    "https://api.groq.com/openai/v1",
)
DEFAULT_GROQ_ASR_MODEL = _get_config_str(
    "DEFAULT_GROQ_ASR_MODEL",
    "whisper-large-v3-turbo",
)
GROQ_API_KEYS = _get_config_keys("GROQ_API_KEYS")

# 规避 Streamlit 文件监控在检查 torch.classes.__path__ 时触发的 RuntimeError
try:
    torch.classes.__path__ = []  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

LOCAL_WHISPER_DEFAULT_MODEL = "base"
_LOCAL_MODEL_CACHE: Dict[Tuple[str, str], whisper.Whisper] = {}
_GROQ_RETRYABLE_STATUS_CODES = {401, 403, 408, 409, 429, 500, 502, 503, 504}


class SpeechRecognizer(Protocol):
    """统一语音识别接口。"""

    def transcribe_file(
        self,
        file_path: Path | str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """识别单个音频文件并返回纯文本。"""


class ApiKeyRoundRobin:
    """线程安全的 API Key 轮询池。"""

    def __init__(self, api_keys: Sequence[str]) -> None:
        cleaned = [key.strip() for key in api_keys if key and key.strip()]
        if not cleaned:
            raise ValueError("API Key 列表不能为空。")
        self._api_keys = cleaned
        self._next_index = 0
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """返回 key 数量。"""
        return len(self._api_keys)

    def get_next_key(self) -> str:
        """按轮询顺序返回下一个 key。"""
        with self._lock:
            key = self._api_keys[self._next_index]
            self._next_index = (self._next_index + 1) % len(self._api_keys)
            return key


class GroqSpeechRecognizer:
    """Groq 语音识别 Provider，支持多 key 轮询与故障切换。"""

    def __init__(
        self,
        api_keys: Sequence[str],
        model: str = DEFAULT_GROQ_ASR_MODEL,
        base_url: str = DEFAULT_GROQ_ASR_BASE_URL,
        timeout: int = ASR_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self._key_pool = ApiKeyRoundRobin(api_keys)
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def transcribe_file(
        self,
        file_path: Path | str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """使用 Groq 接口识别单个音频文件。"""
        path = _resolve_audio_path(file_path)
        error_messages = []

        for _ in range(self._key_pool.size):
            current_key = self._key_pool.get_next_key()
            try:
                return self._transcribe_with_key(path, current_key, language, prompt)
            except Exception as exc:  # noqa: BLE001
                masked_key = _mask_api_key(current_key)
                error_messages.append(f"{masked_key}: {exc}")
                if not _is_recoverable_groq_error(exc):
                    raise RuntimeError(f"Groq 语音识别失败：{exc}") from exc
                logger.warning("Groq key %s 调用失败，准备切换下一个 key：%s", masked_key, exc)

        merged_errors = " | ".join(error_messages)
        raise RuntimeError(f"Groq 语音识别失败，所有 API Key 均不可用：{merged_errors}")

    def _transcribe_with_key(
        self,
        path: Path,
        api_key: str,
        language: Optional[str],
        prompt: Optional[str],
    ) -> str:
        client = OpenAI(
            api_key=api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )
        request_payload = {
            "model": self._model,
            "language": language,
            "prompt": prompt,
        }
        payload = {key: value for key, value in request_payload.items() if value}
        with path.open("rb") as audio_file:
            response = client.audio.transcriptions.create(
                file=audio_file,
                **payload,
            )
        return _extract_transcript_text(response)


class LocalWhisperSpeechRecognizer:
    """本地 Whisper Provider。"""

    def __init__(self, model_size: str = LOCAL_WHISPER_DEFAULT_MODEL, device: Optional[str] = None) -> None:
        self._model_size = model_size
        self._device = _auto_device(device)

    def transcribe_file(
        self,
        file_path: Path | str,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        path = _resolve_audio_path(file_path)
        model = _load_local_model_cached(self._model_size, self._device)
        result = model.transcribe(
            str(path),
            language=language,
            fp16=False,
            initial_prompt=prompt,
        )
        return str(result.get("text", "")).strip()


def create_speech_recognizer(
    provider: Optional[str] = DEFAULT_ASR_PROVIDER,
    *,
    model_size: str = LOCAL_WHISPER_DEFAULT_MODEL,
    device: Optional[str] = None,
    groq_model: str = DEFAULT_GROQ_ASR_MODEL,
    groq_api_keys: Optional[Sequence[str]] = None,
    groq_base_url: str = DEFAULT_GROQ_ASR_BASE_URL,
    timeout: int = ASR_REQUEST_TIMEOUT_SECONDS,
) -> SpeechRecognizer:
    """
    创建语音识别实例。

    Args:
        provider: 识别引擎，支持 `groq` / `local_whisper`。
        model_size: 本地 Whisper 模型规格。
        device: 本地 Whisper 推理设备（cuda/mps/cpu）。
        groq_model: Groq 语音模型名称。
        groq_api_keys: Groq API Key 列表（为空则读取配置）。
        groq_base_url: Groq OpenAI 兼容接口 base_url。
        timeout: 单次请求超时（秒）。
    """
    normalized_provider = (provider or DEFAULT_ASR_PROVIDER).strip().lower()
    if normalized_provider == "groq":
        key_list = _normalize_api_keys(groq_api_keys if groq_api_keys is not None else GROQ_API_KEYS)
        if key_list:
            return GroqSpeechRecognizer(
                api_keys=key_list,
                model=groq_model,
                base_url=groq_base_url,
                timeout=timeout,
            )
        logger.warning("ASR provider=groq 但未配置 API Key，自动回退本地 Whisper。")
        return LocalWhisperSpeechRecognizer(model_size=model_size, device=device)

    if normalized_provider in {"local_whisper", "whisper", "local"}:
        return LocalWhisperSpeechRecognizer(model_size=model_size, device=device)

    raise ValueError(f"不支持的 ASR provider：{provider}")


def _normalize_api_keys(api_keys: Sequence[str]) -> List[str]:
    return [key.strip() for key in api_keys if key and key.strip()]


def _resolve_audio_path(file_path: Path | str) -> Path:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在：{path}")
    return path


def _mask_api_key(api_key: str) -> str:
    stripped = api_key.strip()
    if len(stripped) <= 8:
        return "***"
    return f"{stripped[:4]}...{stripped[-4:]}"


def _is_recoverable_groq_error(exc: Exception) -> bool:
    if isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.AuthenticationError,
            openai.PermissionDeniedError,
            openai.APIConnectionError,
            openai.APITimeoutError,
        ),
    ):
        return True

    if isinstance(exc, openai.APIStatusError):
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            return True
        return int(status_code) in _GROQ_RETRYABLE_STATUS_CODES

    return False


def _extract_transcript_text(response: object) -> str:
    if isinstance(response, dict):
        text = response.get("text", "")
        return str(text).strip()

    text = getattr(response, "text", "")
    return str(text).strip()


def _auto_device(user_choice: Optional[str]) -> str:
    if user_choice:
        return user_choice
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_local_model_cached(model_size: str, device: str) -> whisper.Whisper:
    key = (model_size, device)
    model = _LOCAL_MODEL_CACHE.get(key)
    if model is not None:
        return model

    try:
        loaded_model = whisper.load_model(model_size, device=device)
    except NotImplementedError:
        if device != "cpu":
            loaded_model = whisper.load_model(model_size, device="cpu")
            key = (model_size, "cpu")
        else:
            raise

    _LOCAL_MODEL_CACHE[key] = loaded_model
    return loaded_model
