"""
下载器错误处理测试：验证错误信息清洗与 403 提示构造。
"""
from __future__ import annotations

from core.downloader import _build_download_error_message, _sanitize_exception_message


def test_sanitize_exception_message_removes_ansi() -> None:
    """应移除 ANSI 控制字符，避免页面展示乱码。"""
    raw = "\x1b[0;31mERROR:\x1b[0m [BiliBili] xx: HTTP Error 403: Forbidden"
    cleaned = _sanitize_exception_message(raw)
    assert "\x1b" not in cleaned
    assert "ERROR:" in cleaned
    assert "403: Forbidden" in cleaned


def test_build_download_error_message_403_without_cookie_for_short_url() -> None:
    """短链 + 403 + 无 cookie 时，提示应包含完整 BV 和 cookie 指引。"""
    message = _build_download_error_message(
        error_text="ERROR: HTTP Error 403: Forbidden",
        has_cookie=False,
        source_url="https://b23.tv/1uwf1BdEUU",
    )
    assert "HTTP 403" in message
    assert "完整 BV 链接" in message
    assert "cookie.txt" in message
    assert "原始错误" in message


def test_build_download_error_message_non_403() -> None:
    """非 403 错误保持通用提示。"""
    message = _build_download_error_message(
        error_text="ERROR: HTTP Error 404: Not Found",
        has_cookie=False,
        source_url="https://www.bilibili.com/video/BV123",
    )
    assert message == "音频下载失败：ERROR: HTTP Error 404: Not Found"
