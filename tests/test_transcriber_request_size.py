"""
转写请求体大小相关测试：避免 Groq 413。
"""
from __future__ import annotations

from core.transcriber import _is_payload_too_large_error


def test_detect_payload_too_large_error() -> None:
    assert _is_payload_too_large_error(RuntimeError("Error code: 413 - request_too_large"))
    assert _is_payload_too_large_error(RuntimeError("Request Entity Too Large"))
    assert not _is_payload_too_large_error(RuntimeError("network timeout"))
