"""
模块描述：SQLite 持久化层，负责初始化数据表以及提供任务级 CRUD 接口。

@author 开发
@date 2026-02-23
@version v1.0
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from config import DB_PATH

CONNECTION_TIMEOUT_SECONDS = 30
DATA_DIR_NAME = "data"
DB_FILE_NAME = "app.db"
DEFAULT_DB_PATH = DB_PATH


class TaskStatus(str, Enum):
    """任务状态枚举，限定合法取值。"""

    WAITING = "waiting"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def values(cls) -> List[str]:
        """返回所有合法状态字符串。"""
        return [item.value for item in cls]


@dataclass
class Task:
    """任务实体的简单数据结构。"""

    id: int
    bilibili_url: str
    video_title: str
    video_duration_seconds: Optional[int]
    audio_file_path: Optional[str]
    transcript_text: Optional[str]
    summary_text: Optional[str]
    status: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        """将 sqlite3.Row 转换为 Task 数据类。"""
        return cls(
            id=row["id"],
            bilibili_url=row["bilibili_url"],
            video_title=row["video_title"],
            video_duration_seconds=row["video_duration_seconds"],
            audio_file_path=row["audio_file_path"],
            transcript_text=row["transcript_text"],
            summary_text=row["summary_text"],
            status=row["status"],
            created_at=row["created_at"],
        )


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """
    初始化 SQLite 数据库，创建 tasks 表和必要索引。

    Args:
        db_path: 数据库文件路径，默认使用项目根目录下 data/app.db。
    """
    path = _normalize_db_path(db_path)
    with get_connection(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bilibili_url TEXT NOT NULL,
                video_title TEXT NOT NULL,
                video_duration_seconds INTEGER,
                audio_file_path TEXT,
                transcript_text TEXT,
                summary_text TEXT,
                status TEXT NOT NULL DEFAULT 'waiting',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
            """
        )
        connection.commit()
        _ensure_extra_columns(connection)


def create_task(
    bilibili_url: str,
    video_title: str,
    video_duration_seconds: Optional[int] = None,
    audio_file_path: Optional[str] = None,
    status: str = TaskStatus.WAITING.value,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    """
    创建新任务记录。

    Args:
        bilibili_url: B站原始链接。
        video_title: 视频标题。
        video_duration_seconds: 视频时长（秒）。
        audio_file_path: 本地音频文件相对路径。
        status: 任务初始状态，默认为 waiting。
        db_path: 数据库文件路径。

    Returns:
        新任务的自增主键 ID。
    """
    normalized_status = _validate_status(status)
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO tasks (
                bilibili_url, video_title, video_duration_seconds, audio_file_path,
                transcript_text, summary_text, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bilibili_url,
                video_title,
                video_duration_seconds,
                audio_file_path,
                None,
                None,
                normalized_status,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def update_task_status(
    task_id: int, status: str, db_path: Path | str = DEFAULT_DB_PATH
) -> None:
    """
    更新任务状态字段。

    Args:
        task_id: 任务主键。
        status: 新状态值。
        db_path: 数据库文件路径。
    """
    normalized_status = _validate_status(status)
    _update_fields(task_id, {"status": normalized_status}, db_path)


def update_task_content(
    task_id: int,
    transcript_text: Optional[str] = None,
    summary_text: Optional[str] = None,
    audio_file_path: Optional[str] = None,
    video_title: Optional[str] = None,
    video_duration_seconds: Optional[int] = None,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """
    更新任务的文本结果或音频路径。

    Args:
        task_id: 任务主键。
        transcript_text: 逐字稿文本。
        summary_text: 总结文本。
        audio_file_path: 音频文件相对路径。
        video_title: 视频标题（可更新）。
        video_duration_seconds: 视频时长（秒）。
        db_path: 数据库文件路径。
    """
    fields: Dict[str, Any] = {}
    if transcript_text is not None:
        fields["transcript_text"] = transcript_text
    if summary_text is not None:
        fields["summary_text"] = summary_text
    if audio_file_path is not None:
        fields["audio_file_path"] = audio_file_path
    if video_title is not None:
        fields["video_title"] = video_title
    if video_duration_seconds is not None:
        fields["video_duration_seconds"] = video_duration_seconds
    _update_fields(task_id, fields, db_path)


def get_task(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[Task]:
    """
    按主键获取单条任务记录。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。

    Returns:
        对应的 Task 对象，未找到时返回 None。
    """
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            """
            SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
                   transcript_text, summary_text, status, created_at
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        )
        row = cursor.fetchone()
        return Task.from_row(row) if row else None


def list_tasks(
    limit: Optional[int] = None, db_path: Path | str = DEFAULT_DB_PATH
) -> List[Task]:
    """
    获取任务列表，按创建时间倒序排列。

    Args:
        limit: 限制返回的最大行数，None 时返回全部。
        db_path: 数据库文件路径。

    Returns:
        Task 对象列表。
    """
    sql = """
    SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
           transcript_text, summary_text, status, created_at
    FROM tasks
    ORDER BY datetime(created_at) DESC
    """
    params: tuple[Any, ...] = ()
    if isinstance(limit, int) and limit > 0:
        sql += " LIMIT ?"
        params = (limit,)

    with get_connection(db_path) as connection:
        cursor = connection.execute(sql, params)
        rows = cursor.fetchall()
        return [Task.from_row(row) for row in rows]


def delete_task(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """
    删除单条任务记录。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。
    """
    with get_connection(db_path) as connection:
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        connection.commit()


def delete_tasks_before(days: int, db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """
    删除早于指定天数的任务，返回删除条数。

    Args:
        days: 天数阈值（>0）。
        db_path: 数据库文件路径。
    """
    if days <= 0:
        return 0
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM tasks WHERE created_at < datetime('now', ?)",
            (f"-{int(days)} days",),
        )
        connection.commit()
        return cursor.rowcount


def delete_tasks_by_status(
    statuses: List[str], db_path: Path | str = DEFAULT_DB_PATH
) -> int:
    """
    按状态批量删除任务，返回删除条数。

    Args:
        statuses: 需删除的状态列表。
        db_path: 数据库文件路径。
    """
    if not statuses:
        return 0
    placeholders = ",".join("?" for _ in statuses)
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            f"DELETE FROM tasks WHERE status IN ({placeholders})", tuple(statuses)
        )
        connection.commit()
        return cursor.rowcount


@contextmanager
def get_connection(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Generator[sqlite3.Connection, None, None]:
    """
    生成带有基础配置的数据库连接上下文。

    Args:
        db_path: 数据库文件路径。

    Yields:
        已配置 row_factory 的 sqlite3.Connection。
    """
    path = _normalize_db_path(db_path)
    connection = sqlite3.connect(
        str(path),
        timeout=CONNECTION_TIMEOUT_SECONDS,
        check_same_thread=False,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _update_fields(
    task_id: int, fields: Dict[str, Any], db_path: Path | str = DEFAULT_DB_PATH
) -> None:
    """通用字段更新器，内部确保列名白名单。"""
    if not fields:
        return

    allowed_fields = {
        "bilibili_url",
        "video_title",
        "audio_file_path",
        "transcript_text",
        "summary_text",
        "video_title",
        "video_duration_seconds",
        "status",
    }
    invalid_fields = set(fields.keys()) - allowed_fields
    if invalid_fields:
        raise ValueError(f"不支持更新的字段: {invalid_fields}")

    columns = ", ".join(f"{col} = ?" for col in fields.keys())
    values = [fields[key] for key in fields.keys()]
    values.append(task_id)

    with get_connection(db_path) as connection:
        connection.execute(
            f"UPDATE tasks SET {columns} WHERE id = ?",  # 安全：列名已通过白名单校验
            tuple(values),
        )
        connection.commit()


def _normalize_db_path(db_path: Path | str) -> Path:
    """确保数据库目录存在并返回规范化后的 Path。"""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _validate_status(status: str) -> str:
    """校验并返回合法状态值。"""
    if status not in TaskStatus.values():
        raise ValueError(f"非法状态值: {status}，合法取值: {TaskStatus.values()}")
    return status


def _ensure_extra_columns(connection: sqlite3.Connection) -> None:
    """为旧表补充新增列，避免因 schema 变更导致异常。"""
    cursor = connection.execute("PRAGMA table_info(tasks);")
    existing = {row["name"] for row in cursor.fetchall()}
    if "video_duration_seconds" not in existing:
        connection.execute("ALTER TABLE tasks ADD COLUMN video_duration_seconds INTEGER;")
