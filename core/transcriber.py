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
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydub import AudioSegment
import torch
import whisper

# 规避 Streamlit 文件监控在检查 torch.classes.__path__ 时触发的 RuntimeError
# 参考：https://discuss.streamlit.io/t/error-in-torch-with-streamlit/90908
try:
    torch.classes.__path__ = []  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    # 若未来 PyTorch 调整实现，确保应用不因兜底处理失败而中断
    pass

DEFAULT_MODEL_SIZE = "base"
CHUNK_DURATION_SECONDS = 300  # 每段 5 分钟，兼顾稳定性与性能
FILE_SIZE_LIMIT_MB = 25
_MODEL_CACHE: Dict[Tuple[str, str], whisper.Whisper] = {}


def audio_to_text(
    file_path: Path | str,
    model_size: str = DEFAULT_MODEL_SIZE,
    language: Optional[str] = None,
    chunk_duration_sec: int = CHUNK_DURATION_SECONDS,
    file_size_limit_mb: int = FILE_SIZE_LIMIT_MB,
    device: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, str, float, float], None]] = None,
    resume_from_chunks: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    将音频文件转录为文本（离线 Whisper）。

    Args:
        file_path: 输入音频文件路径。
        model_size: Whisper 模型规格（tiny/base/small/medium/large-v3）。
        language: 可选语言代码，None 时交由 Whisper 自动检测。
        chunk_duration_sec: 切片时长阈值（秒），超过则分片转录。
        file_size_limit_mb: 文件大小阈值（MB），超过则分片转录。
        device: 推理设备（'cuda'/'mps'/'cpu'），None 时自动选择。
        progress_callback: 进度回调函数，签名为 (current, total, chunk_text, start_sec, end_sec)。
        resume_from_chunks: 断点续传数据，包含已完成切片的信息。

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
        resolved_device = _auto_device(device)
        model = _load_model_cached(model_size, resolved_device)
        audio = AudioSegment.from_file(path)

        needs_chunk = (
            audio.duration_seconds > chunk_duration_sec
            or path.stat().st_size > file_size_limit_mb * 1024 * 1024
        )

        if not needs_chunk:
            result = model.transcribe(str(path), language=language, fp16=False)
            text = result.get("text", "").strip()
            # 即使不分片，也触发回调（作为单个完整切片）
            if progress_callback:
                progress_callback(1, 1, text, 0, audio.duration_seconds)
            return text

        segments = _split_audio(audio, chunk_duration_sec)
        total_chunks = len(segments)
        texts: List[str] = []

        # 断点续传：跳过已完成切片
        start_index = 0
        if resume_from_chunks:
            completed_chunks = [c for c in resume_from_chunks if c.get("completed")]
            completed_texts = [c["text"] for c in completed_chunks if c.get("text")]
            texts.extend(completed_texts)
            start_index = len(completed_chunks)

        for i in range(start_index, total_chunks):
            segment = segments[i]
            start_sec = i * chunk_duration_sec
            end_sec = min((i + 1) * chunk_duration_sec, audio.duration_seconds)

            with NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                segment.export(tmp_file.name, format="wav")
                result = model.transcribe(tmp_file.name, language=language, fp16=False)
            Path(tmp_file.name).unlink(missing_ok=True)

            chunk_text = result.get("text", "").strip()
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


def _auto_device(user_choice: Optional[str]) -> str:
    """
    自动推断可用设备。优先级：用户指定 > CUDA > MPS > CPU。
    """
    if user_choice:
        return user_choice
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _load_model_cached(model_size: str, device: str) -> whisper.Whisper:
    """基于 (模型大小, 设备) 进行缓存，避免重复加载耗时。"""
    key = (model_size, device)
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        model = whisper.load_model(model_size, device=device)
    except NotImplementedError:
        if device != "cpu":
            model = whisper.load_model(model_size, device="cpu")
            key = (model_size, "cpu")
        else:
            raise
    _MODEL_CACHE[key] = model
    return model
