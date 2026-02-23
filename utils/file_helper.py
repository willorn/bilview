"""
文件与目录通用工具。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def ensure_dir(path: Path | str) -> Path:
    """确保目录存在并返回 Path。"""
    p = Path(path).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def list_files(directory: Path | str, suffixes: Iterable[str] | None = None) -> list[Path]:
    """列出目录下指定后缀的文件。suffixes 为空则返回全部文件。"""
    dir_path = Path(directory).expanduser().resolve()
    if not dir_path.is_dir():
        return []
    if not suffixes:
        return sorted([p for p in dir_path.iterdir() if p.is_file()])
    suffix_set = {s.lower() for s in suffixes}
    return sorted([p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in suffix_set])
