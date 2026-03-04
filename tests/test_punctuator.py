"""自动补标点模块测试。"""
from __future__ import annotations

import unicodedata

from core.punctuator import punctuate_transcript


def _normalize_for_compare(text: str) -> str:
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def test_punctuate_transcript_preserves_existing_punctuation() -> None:
    source = "今天我们来聊自动补标点。先看稳定性，再看可读性。"
    assert punctuate_transcript(source) == source


def test_punctuate_transcript_adds_punctuation_without_changing_words() -> None:
    source = (
        "今天我们来聊一下自动补标点这个功能它的目标是提高可读性同时不改变原始转录中的任何词语和顺序"
        "这样后续总结会更稳定也更容易让人快速阅读"
    )
    punctuated = punctuate_transcript(source)
    assert punctuated != source
    assert any(mark in punctuated for mark in "，。？！")
    assert _normalize_for_compare(punctuated) == _normalize_for_compare(source)
