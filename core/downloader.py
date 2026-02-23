"""
模块描述：使用 yt-dlp 仅提取 B 站视频的音频流并保存至本地。

功能要点：
1. 仅下载最佳音质的音频流（默认提取为 M4A）。
2. 自动创建 downloads 目录并生成规范文件名。
3. 预留 cookie 文件支持，处理需要登录态的会员视频。

@author 开发
@date 2026-02-23
@version v1.0
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL
from utils.file_helper import ensure_dir

DOWNLOAD_DIR_NAME = "downloads"
PREFERRED_CODEC = "m4a"
PREFERRED_QUALITY = "192"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR_NAME
DEFAULT_COOKIE_FILE = Path("cookie.txt")

INVALID_FILENAME_CHARS = r'[^a-zA-Z0-9\\-_\\.]'


def download_audio(
    url: str,
    download_dir: Path | str = DEFAULT_DOWNLOAD_DIR,
    cookie_file: Optional[Path | str] = DEFAULT_COOKIE_FILE,
) -> Path:
    """
    下载指定 B 站链接的音频流并返回本地文件路径。

    Args:
        url: B 站视频链接。
        download_dir: 音频保存目录，默认使用项目根目录下 downloads/。
        cookie_file: 可选的 cookie 文件路径，用于会员或受限视频。

    Returns:
        下载完成后的音频文件绝对路径。

    Raises:
        RuntimeError: 下载或后处理失败时抛出异常。
    """
    target_dir = ensure_dir(download_dir)

    ydl_opts = _build_options(target_dir, cookie_file)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt-dlp 会在后处理阶段将扩展名替换为首选编解码格式
            raw_path = Path(ydl.prepare_filename(info))
            final_path = raw_path.with_suffix(f".{PREFERRED_CODEC}")
            return final_path
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"音频下载失败：{exc}") from exc


def _build_options(download_dir: Path, cookie_file: Optional[Path | str]) -> dict:
    """封装 yt-dlp 配置，确保只下载音频。"""
    cookie_path: Optional[Path] = None
    if cookie_file is not None:
        candidate = Path(cookie_file).expanduser().resolve()
        if candidate.is_file():
            cookie_path = candidate

    outtmpl = str(download_dir / "%(title).80s_%(epoch)s.%(ext)s")

    return {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "restrictfilenames": True,
        "merge_output_format": PREFERRED_CODEC,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": PREFERRED_CODEC,
                "preferredquality": PREFERRED_QUALITY,
            }
        ],
        "trim_file_name": 240,
        "cookiefile": str(cookie_path) if cookie_path else None,
        "cachedir": False,
        "outtmpl_na_placeholder": "unknown",
        "paths": {"home": str(download_dir)},
    }


def sanitize_title(title: str) -> str:
    """
    将视频标题清洗为安全的文件名片段（兜底工具函数，未在默认流程中调用）。

    Args:
        title: 原始标题。

    Returns:
        仅包含字母、数字、下划线、短横线和点号的字符串。
    """
    cleaned = re.sub(INVALID_FILENAME_CHARS, "_", title)
    return cleaned.strip("._") or "audio"
