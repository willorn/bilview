"""
默认 Prompt 来源测试：确保总结默认 Prompt 来自 docs/default_prompt.md。
"""
from __future__ import annotations

from core.summarizer import DEFAULT_PROMPT_PATH, get_default_system_prompt


def test_default_prompt_loaded_from_docs_file() -> None:
    """默认 Prompt 应与 docs/default_prompt.md 内容一致。"""
    expected = DEFAULT_PROMPT_PATH.read_text(encoding="utf-8").strip()
    assert expected
    assert get_default_system_prompt() == expected
