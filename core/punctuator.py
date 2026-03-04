"""
模块描述：转录文本自动补标点（仅插入标点，不改动原有字词）。

设计目标：
1. 当 ASR 输出标点稀缺时，提供可读性更好的“阅读版”文本。
2. 严格保证不修改原始词序与字词，仅允许补充标点和规范空白。
3. 若补标点结果未通过一致性校验，自动回退原文。
"""
from __future__ import annotations

import re
import unicodedata

MIN_EXISTING_PUNCTUATION_DENSITY = 0.008
CLAUSE_MAX_LENGTH = 20
SENTENCE_MAX_LENGTH = 46
QUESTION_END_CHARS = {"吗", "么", "呢", "嘛", "吧", "否"}
CLAUSE_BREAK_HINTS = {
    "但",
    "并",
    "而",
    "且",
    "和",
    "或",
    "又",
    "再",
    "也",
    "就",
    "还",
    "先",
    "再",
}
SENTENCE_BREAK_HINTS = {"然后", "所以", "因此", "另外", "最后", "总之"}


def punctuate_transcript(raw_text: str) -> str:
    """
    将转录文本补充为更易读的版本。

    说明：
    - 若检测到原文已有较丰富标点，则直接返回原文。
    - 仅进行“加标点/规范空白”，不改写字词。
    """
    normalized = _normalize_spaces(raw_text)
    if not normalized:
        return ""
    if _has_enough_punctuation(normalized):
        return normalized

    punctuated = _insert_punctuation(normalized)
    if _normalize_for_compare(punctuated) != _normalize_for_compare(normalized):
        return normalized
    return punctuated


def _normalize_spaces(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return re.sub(r"\s+", " ", stripped)


def _has_enough_punctuation(text: str) -> bool:
    content_chars = [char for char in text if not char.isspace()]
    if not content_chars:
        return True

    punct_count = sum(1 for char in content_chars if _is_punctuation(char))
    density = punct_count / max(len(content_chars), 1)
    has_sentence_end = any(char in "。！？!?；;" for char in text)
    return density >= MIN_EXISTING_PUNCTUATION_DENSITY and has_sentence_end


def _insert_punctuation(text: str) -> str:
    result: list[str] = []
    clause_len = 0
    sentence_len = 0
    pending_space = False

    for index, char in enumerate(text):
        if char.isspace():
            pending_space = True
            continue

        if pending_space and result:
            next_hint = _peek_next_non_space(text, index + 1)
            current_tail = result[-1]
            if _is_punctuation(current_tail):
                pending_space = False
            elif sentence_len >= SENTENCE_MAX_LENGTH:
                result.append("？" if current_tail in QUESTION_END_CHARS else "。")
                clause_len = 0
                sentence_len = 0
                pending_space = False
            elif clause_len >= CLAUSE_MAX_LENGTH and (
                char in CLAUSE_BREAK_HINTS or _starts_with_sentence_hint(text, index)
            ):
                result.append("，")
                clause_len = 0
                pending_space = False
            elif next_hint is None and sentence_len >= max(8, CLAUSE_MAX_LENGTH // 2):
                result.append("？" if current_tail in QUESTION_END_CHARS else "。")
                clause_len = 0
                sentence_len = 0
                pending_space = False
            else:
                result.append(" ")
                pending_space = False

        result.append(char)
        if _is_punctuation(char):
            clause_len = 0
            sentence_len = 0
            continue

        clause_len += 1
        sentence_len += 1

        next_char = _peek_next_non_space(text, index + 1)
        if next_char is None:
            if not _is_punctuation(result[-1]):
                result.append("？" if char in QUESTION_END_CHARS else "。")
            break

        if sentence_len >= SENTENCE_MAX_LENGTH:
            result.append("？" if char in QUESTION_END_CHARS else "。")
            clause_len = 0
            sentence_len = 0
            continue

        if clause_len >= CLAUSE_MAX_LENGTH and (
            next_char in CLAUSE_BREAK_HINTS or _starts_with_sentence_hint(text, index + 1)
        ):
            result.append("，")
            clause_len = 0
            continue

        if sentence_len >= int(SENTENCE_MAX_LENGTH * 0.7) and _starts_with_sentence_hint(text, index + 1):
            result.append("。")
            clause_len = 0
            sentence_len = 0
            continue

    return _cleanup_punctuation("".join(result))


def _peek_next_non_space(text: str, start_index: int) -> str | None:
    for index in range(start_index, len(text)):
        if not text[index].isspace():
            return text[index]
    return None


def _starts_with_sentence_hint(text: str, index: int) -> bool:
    candidate = text[index : index + 2]
    if candidate and candidate in SENTENCE_BREAK_HINTS:
        return True
    return False


def _cleanup_punctuation(text: str) -> str:
    cleaned = re.sub(r"[，,]{2,}", "，", text)
    cleaned = re.sub(r"[。\.]{2,}", "。", cleaned)
    cleaned = re.sub(r"[？！\?!]{2,}", "？", cleaned)
    cleaned = re.sub(r"\s+([，。！？；：])", r"\1", cleaned)
    cleaned = re.sub(r"([（【《“‘])\s+", r"\1", cleaned)
    cleaned = re.sub(r"\s+([）】》”’])", r"\1", cleaned)
    return cleaned.strip()


def _normalize_for_compare(text: str) -> str:
    return "".join(
        char
        for char in text
        if not char.isspace() and not _is_punctuation(char)
    )


def _is_punctuation(char: str) -> bool:
    if not char:
        return False
    return unicodedata.category(char).startswith("P")
