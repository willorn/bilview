"""
模块描述：音频转录编排层，负责切片、进度回调与断点续传。

设计取舍：
1. 默认使用 Groq 语音识别（可通过 provider 切换到本地 Whisper）。
2. 当音频超过指定时长/体积时，利用 pydub 先切片，再逐段调用识别引擎并拼接结果。
3. 依赖 ffmpeg（已存在于环境中）完成音频解码。

@author 开发
@date 2026-03-04
@version v2.0
"""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Dict, List, Optional, Sequence

from pydub import AudioSegment

from config import DEFAULT_ASR_PROVIDER, DEFAULT_GROQ_ASR_MODEL
from core.speech_recognition import create_speech_recognizer

DEFAULT_MODEL_SIZE = "base"
DEFAULT_PROVIDER = DEFAULT_ASR_PROVIDER
DEFAULT_ASR_MODEL = DEFAULT_GROQ_ASR_MODEL
CHUNK_DURATION_SECONDS = 300  # 每段 5 分钟，兼顾稳定性与性能
FILE_SIZE_LIMIT_MB = 25
GROQ_PROVIDER = "groq"
GROQ_CHUNK_FORMAT = "mp3"
GROQ_CHUNK_SUFFIX = ".mp3"
GROQ_CHUNK_BITRATE = "64k"


def audio_to_text(
    file_path: Path | str,
    model_size: str = DEFAULT_MODEL_SIZE,
    language: Optional[str] = None,
    chunk_duration_sec: int = CHUNK_DURATION_SECONDS,
    file_size_limit_mb: int = FILE_SIZE_LIMIT_MB,
    device: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str, float, float], None]] = None,
    resume_from_chunks: Optional[List[Dict[str, Any]]] = None,
    provider: str = DEFAULT_PROVIDER,
    asr_model: str = DEFAULT_ASR_MODEL,
    api_keys: Optional[Sequence[str]] = None,
) -> str:
    """
    将音频文件转录为文本（支持 Groq / 本地 Whisper）。

    Args:
        file_path: 输入音频文件路径。
        model_size: 本地 Whisper 模型规格（provider=local_whisper 时生效）。
        language: 可选语言代码，None 时交由 Whisper 自动检测。
        chunk_duration_sec: 切片时长阈值（秒），超过则分片转录。
        file_size_limit_mb: 文件大小阈值（MB），超过则分片转录。
        device: 本地推理设备（'cuda'/'mps'/'cpu'），None 时自动选择。
        progress_callback: 进度回调函数，签名为 (current, total, chunk_text, start_sec, end_sec)。
        resume_from_chunks: 断点续传数据，包含已完成切片的信息。
        provider: 语音识别 provider，默认 `groq`。
        asr_model: Groq 语音模型名（provider=groq 时生效）。
        api_keys: 可选 API Key 列表（用于覆盖默认配置，支持轮询）。

    Returns:
        逐字稿文本（去除首尾空白）。

    Raises:
        FileNotFoundError: 当音频文件不存在时。
        RuntimeError: 转录过程中出现异常。
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在：{path}")

    try:
        recognizer = create_speech_recognizer(
            provider=provider,
            model_size=model_size,
            device=device,
            groq_model=asr_model,
            groq_api_keys=api_keys,
        )
        audio = AudioSegment.from_file(path)

        needs_chunk = (
            audio.duration_seconds > chunk_duration_sec
            or path.stat().st_size > file_size_limit_mb * 1024 * 1024
        )

        if not needs_chunk:
            try:
                text = recognizer.transcribe_file(path, language=language)
                # 即使不分片，也触发回调（作为单个完整切片）
                if progress_callback:
                    progress_callback(1, 1, text, 0, audio.duration_seconds)
                return text
            except Exception as exc:  # noqa: BLE001
                if not _is_payload_too_large_error(exc):
                    raise
                # API 提示请求过大时，改走分片并压缩上传，避免 413 失败。
                needs_chunk = True

        segments = _split_audio(audio, chunk_duration_sec)
        total_chunks = len(segments)
        texts: List[str] = []
        chunk_format, chunk_suffix, export_kwargs = _resolve_chunk_export(provider)

        # 断点续传：跳过已完成切片
        start_index = 0
        if resume_from_chunks:
            completed_chunks = sorted(
                [chunk for chunk in resume_from_chunks if chunk.get("completed")],
                key=lambda chunk: int(chunk.get("index", 0)),
            )
            completed_texts = [c["text"] for c in completed_chunks if c.get("text")]
            texts.extend(completed_texts)
            start_index = len(completed_chunks)

        for i in range(start_index, total_chunks):
            segment = segments[i]
            start_sec = i * chunk_duration_sec
            end_sec = min((i + 1) * chunk_duration_sec, audio.duration_seconds)

            with NamedTemporaryFile(suffix=chunk_suffix, delete=False) as tmp_file:
                temp_path = Path(tmp_file.name)
            try:
                segment.export(temp_path, format=chunk_format, **export_kwargs)
                chunk_text = recognizer.transcribe_file(temp_path, language=language)
            finally:
                temp_path.unlink(missing_ok=True)

            texts.append(chunk_text)

            # 触发进度回调
            if progress_callback:
                progress_callback(i + 1, total_chunks, chunk_text, start_sec, end_sec)

        return " ".join(filter(None, texts)).strip()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"音频转文字失败：{exc}") from exc


def _split_audio(audio: AudioSegment, chunk_duration_sec: int) -> List[AudioSegment]:
    """按固定时长切分音频，最后一段可能不足该时长。"""
    if chunk_duration_sec <= 0:
        return [audio]

    chunk_ms = chunk_duration_sec * 1000
    total_ms = len(audio)
    return [
        audio[start:start + chunk_ms]
        for start in range(0, total_ms, chunk_ms)
    ]


def _resolve_chunk_export(provider: str) -> tuple[str, str, Dict[str, str]]:
    """根据 provider 选择临时分片的导出格式。"""
    if provider.strip().lower() == GROQ_PROVIDER:
        return GROQ_CHUNK_FORMAT, GROQ_CHUNK_SUFFIX, {"bitrate": GROQ_CHUNK_BITRATE}
    return "wav", ".wav", {}


def _is_payload_too_large_error(exc: Exception) -> bool:
    """判断错误是否为请求体过大（413）。"""
    error_text = str(exc).lower()
    return (
        "413" in error_text
        or "request entity too large" in error_text
        or "payload too large" in error_text
        or "request_too_large" in error_text
    )
