"""
转写请求体大小相关测试：避免 Groq 413。
"""
from __future__ import annotations

from core.transcriber import _is_payload_too_large_error, _resolve_chunk_export


def test_resolve_chunk_export_for_groq() -> None:
    fmt, suffix, options = _resolve_chunk_export("groq")
    assert fmt == "mp3"
    assert suffix == ".mp3"
    assert options.get("bitrate") == "64k"


def test_resolve_chunk_export_for_local_whisper() -> None:
    fmt, suffix, options = _resolve_chunk_export("local_whisper")
    assert fmt == "wav"
    assert suffix == ".wav"
    assert options == {}


def test_detect_payload_too_large_error() -> None:
    assert _is_payload_too_large_error(RuntimeError("Error code: 413 - request_too_large"))
    assert _is_payload_too_large_error(RuntimeError("Request Entity Too Large"))
    assert not _is_payload_too_large_error(RuntimeError("network timeout"))
