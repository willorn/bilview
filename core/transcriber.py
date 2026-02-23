"""
模块描述：离线使用 Whisper 模型将音频转录为文本，可自动按时长分片。

设计取舍：
1. 默认使用本地 Whisper 模型（避免在线 API 成本），支持自定义模型体积。
2. 当音频超过指定时长/体积时，利用 pydub 先切片，再逐段调用 Whisper 并拼接结果。
3. 依赖 ffmpeg（已存在于环境中）完成音频解码。

@author 开发
@date 2026-02-23
@version v1.0
"""
from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import List, Optional

from pydub import AudioSegment
import whisper

DEFAULT_MODEL_SIZE = "base"
CHUNK_DURATION_SECONDS = 300  # 每段 5 分钟，兼顾稳定性与性能
FILE_SIZE_LIMIT_MB = 25


def audio_to_text(
    file_path: Path | str,
    model_size: str = DEFAULT_MODEL_SIZE,
    language: Optional[str] = None,
    chunk_duration_sec: int = CHUNK_DURATION_SECONDS,
    file_size_limit_mb: int = FILE_SIZE_LIMIT_MB,
) -> str:
    """
    将音频文件转录为文本（离线 Whisper）。

    Args:
        file_path: 输入音频文件路径。
        model_size: Whisper 模型规格（tiny/base/small/medium/large-v3）。
        language: 可选语言代码，None 时交由 Whisper 自动检测。
        chunk_duration_sec: 切片时长阈值（秒），超过则分片转录。
        file_size_limit_mb: 文件大小阈值（MB），超过则分片转录。

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
        model = whisper.load_model(model_size)
        audio = AudioSegment.from_file(path)

        needs_chunk = (
            audio.duration_seconds > chunk_duration_sec
            or path.stat().st_size > file_size_limit_mb * 1024 * 1024
        )

        if not needs_chunk:
            result = model.transcribe(str(path), language=language, fp16=False)
            return result.get("text", "").strip()

        segments = _split_audio(audio, chunk_duration_sec)
        texts: List[str] = []

        for segment in segments:
            with NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                segment.export(tmp_file.name, format="wav")
                result = model.transcribe(tmp_file.name, language=language, fp16=False)
            Path(tmp_file.name).unlink(missing_ok=True)
            texts.append(result.get("text", "").strip())

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

