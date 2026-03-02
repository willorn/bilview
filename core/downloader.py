"""
模块描述：使用 yt-dlp 仅提取 B 站视频的音频流并保存至本地。

功能要点：
1. 仅下载最佳音质的音频流（默认提取为 M4A）。
2. 自动创建 downloads 目录并生成规范文件名。
3. 预留 cookie 文件支持，处理需要登录态的会员视频。
4. 自动重试机制：区分网络临时性错误和永久性错误，使用指数退避策略。

@author 开发
@date 2026-02-23
@version v1.1 (新增重试机制)
"""
from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, Union

from yt_dlp import YoutubeDL
from utils.file_helper import ensure_dir
from utils.retry_helper import download_retry_decorator

logger = logging.getLogger(__name__)

DOWNLOAD_DIR_NAME = "downloads"
PREFERRED_CODEC = "m4a"
PREFERRED_QUALITY = "192"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR_NAME
DEFAULT_COOKIE_FILE = Path("cookie.txt")

INVALID_FILENAME_CHARS = r'[^a-zA-Z0-9\\-_\\.]'
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
SHORT_URL_DOMAIN = "b23.tv"
DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


@download_retry_decorator
def download_audio(
    url: str,
    download_dir: Path | str = DEFAULT_DOWNLOAD_DIR,
    cookie_file: Optional[Path | str] = DEFAULT_COOKIE_FILE,
    return_info: bool = False,
) -> Union[Path, Tuple[Path, dict]]:
    """
    下载指定 B 站链接的音频流并返回本地文件路径。

    自动重试策略：
    - 最大重试 3 次
    - 指数退避：2-30 秒
    - 可重试错误：网络超时、连接失败、临时不可用
    - 不可重试错误：视频不存在、权限问题、地区限制

    Args:
        url: B 站视频链接。
        download_dir: 音频保存目录，默认使用项目根目录下 downloads/。
        cookie_file: 可选的 cookie 文件路径，用于会员或受限视频。
        return_info: True 时同时返回 yt-dlp 抽取的 info 字典。

    Returns:
        下载完成后的音频文件绝对路径；若 return_info=True，则返回 (路径, info)。

    Raises:
        RuntimeError: 下载或后处理失败时抛出异常。
    """
    target_dir = ensure_dir(download_dir)
    normalized_url = _normalize_bilibili_url(url)
    cookie_path = _resolve_cookie_file(cookie_file)

    ydl_opts = _build_options(target_dir, cookie_path)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            # yt-dlp 会在后处理阶段将扩展名替换为首选编解码格式
            raw_path = Path(ydl.prepare_filename(info))
            final_path = raw_path.with_suffix(f".{PREFERRED_CODEC}")
            logger.info("音频下载成功: %s", final_path)
            return (final_path, info) if return_info else final_path
    except Exception as exc:  # noqa: BLE001
        clean_error = _sanitize_exception_message(str(exc))
        logger.warning("音频下载失败: %s", clean_error)
        raise RuntimeError(
            _build_download_error_message(
                error_text=clean_error,
                has_cookie=bool(cookie_path),
                source_url=normalized_url,
            )
        ) from exc


def _resolve_cookie_file(cookie_file: Optional[Path | str]) -> Optional[Path]:
    """解析 cookie 文件路径，存在时返回绝对路径。"""
    if cookie_file is None:
        return None
    candidate = Path(cookie_file).expanduser().resolve()
    if candidate.is_file():
        return candidate
    return None


def _build_options(download_dir: Path, cookie_path: Optional[Path]) -> dict:
    """封装 yt-dlp 配置，确保只下载音频。"""
    outtmpl = str(download_dir / "%(title).80s_%(epoch)s.%(ext)s")
    options = {
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
        "cachedir": False,
        "outtmpl_na_placeholder": "unknown",
        "paths": {"home": str(download_dir)},
        "http_headers": DEFAULT_HTTP_HEADERS,
    }
    if cookie_path:
        options["cookiefile"] = str(cookie_path)
    return options


def _normalize_bilibili_url(url: str) -> str:
    """尽量将 b23.tv 短链解析为完整链接，失败则回退原地址。"""
    if SHORT_URL_DOMAIN not in url.lower():
        return url

    request = urllib.request.Request(url, headers=DEFAULT_HTTP_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            redirected = response.geturl()
        if redirected:
            return redirected
    except Exception as exc:  # noqa: BLE001
        logger.warning("短链解析失败，继续使用原链接: %s", exc)
    return url


def _sanitize_exception_message(message: str) -> str:
    """移除 ANSI 控制符并压缩空白字符，避免错误信息污染页面。"""
    cleaned = ANSI_ESCAPE_PATTERN.sub("", message)
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_download_error_message(error_text: str, has_cookie: bool, source_url: str) -> str:
    """构造可读且可操作的下载错误提示。"""
    lowered_error = error_text.lower()
    is_http_403 = "http error 403" in lowered_error or "403: forbidden" in lowered_error
    if not is_http_403:
        return f"音频下载失败：{error_text}"

    hints = ["B站返回 HTTP 403，可能触发风控、访问频率限制或需要登录态。"]
    if SHORT_URL_DOMAIN in source_url.lower():
        hints.append("建议改用完整 BV 链接再试。")
    if has_cookie:
        hints.append("已检测到 cookie.txt，请确认 cookie 未过期且导出为 Netscape 格式。")
    else:
        hints.append("请在项目根目录放置有效的 cookie.txt 后重试。")
    hints.append("若持续报错，请升级 yt-dlp 后重试。")

    hint_text = " ".join(hints)
    return f"音频下载失败（HTTP 403）：{hint_text} 原始错误：{error_text}"


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
