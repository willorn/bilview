"""
模块描述：SQLite 持久化层，负责初始化数据表以及提供任务级 CRUD 接口。

@author 开发
@date 2026-02-23
@version v1.0
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Sequence

# 设置时区为北京时区（Asia/Shanghai）
os.environ["TZ"] = "Asia/Shanghai"
try:
    time.tzset()
except AttributeError:
    pass  # Windows 不支持 tzset

from config import (
    CLOUDFLARE_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN,
    CLOUDFLARE_D1_DATABASE_ID,
    DB_PATH,
    SUPABASE_POSTGRES_URL,
    SOCKS5_PROXY,
)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - 由运行环境决定是否安装 psycopg2
    psycopg2 = None

CONNECTION_TIMEOUT_SECONDS = 30
HTTP_CONNECTION_TIMEOUT_SECONDS = 30
DATA_DIR_NAME = "data"
DB_FILE_NAME = "app.db"
DEFAULT_DB_PATH = DB_PATH
REMOTE_DB_SCHEMES = ("https://", "http://")
D1_API_BASE_URL = "https://api.cloudflare.com/client/v4"
LOGGER = logging.getLogger(__name__)
_INIT_DB_LOCK = threading.Lock()
_INITIALIZED_DB_TARGETS: set[str] = set()


class TaskStatus(str, Enum):
    """任务状态枚举，限定合法取值。"""

    WAITING = "waiting"
    DOWNLOADING = "downloading"
    TRANSCRIBING = "transcribing"
    SUMMARIZING = "summarizing"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
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
    def from_row(cls, row: Any) -> "Task":
        """将 sqlite3.Row 转换为 Task 数据类。"""
        if _is_sequence_row(row):
            return cls(
                id=row[0],
                bilibili_url=row[1],
                video_title=row[2],
                video_duration_seconds=row[3],
                audio_file_path=row[4],
                transcript_text=row[5],
                summary_text=row[6],
                status=row[7],
                created_at=row[8],
            )
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


@dataclass(frozen=True)
class TaskQueueItem:
    """任务队列中的轻量任务描述。"""

    id: int
    bilibili_url: str


@dataclass(frozen=True)
class D1Credentials:
    """Cloudflare D1 连接所需凭据。"""

    account_id: str
    database_id: str
    api_token: str


class D1Cursor:
    """模拟 sqlite 游标接口，适配现有调用逻辑。"""

    def __init__(
        self,
        rows: Optional[List[Dict[str, Any]]] = None,
        *,
        last_row_id: int = 0,
        changes: int = 0,
    ) -> None:
        self._rows = rows or []
        self._index = 0
        self.lastrowid = int(last_row_id or 0)
        self.rowcount = int(changes or 0)

    def fetchone(self) -> Optional[Dict[str, Any]]:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchall(self) -> List[Dict[str, Any]]:
        if self._index >= len(self._rows):
            return []
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows


class D1Connection:
    """Cloudflare D1 HTTP 连接封装，暴露 execute/commit 接口。"""

    def __init__(self, credentials: D1Credentials) -> None:
        self._credentials = credentials
        self._api_url = (
            f"{D1_API_BASE_URL}/accounts/{credentials.account_id}/d1/"
            f"database/{credentials.database_id}/query"
        )

    def execute(self, sql: str, params: Optional[Sequence[Any]] = None) -> D1Cursor:
        payload: Dict[str, Any] = {"sql": sql}
        if params:
            payload["params"] = list(params)

        request = urllib.request.Request(
            self._api_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._credentials.api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=HTTP_CONNECTION_TIMEOUT_SECONDS
            ) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Cloudflare D1 请求失败（HTTP {exc.code}）：{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Cloudflare D1 网络异常：{exc.reason}") from exc

        parsed = json.loads(body)
        if not parsed.get("success"):
            raise RuntimeError(f"Cloudflare D1 API 返回失败：{parsed}")

        result = (parsed.get("result") or [{}])[0]
        if not result.get("success", True):
            raise RuntimeError(f"Cloudflare D1 SQL 执行失败：{result}")

        rows = result.get("results") or []
        meta = result.get("meta") or {}
        return D1Cursor(
            rows=rows,
            last_row_id=int(meta.get("last_row_id") or 0),
            changes=int(meta.get("changes") or 0),
        )

    def commit(self) -> None:
        """D1 每次 execute 即完成提交，此处保持接口兼容。"""
        return None

    def close(self) -> None:
        """HTTP 无持久连接对象，保持接口兼容。"""
        return None


def _is_sequence_row(row: Any) -> bool:
    return isinstance(row, (tuple, list))


def _table_info_name(row: Any) -> str:
    if _is_sequence_row(row):
        return str(row[1])
    return str(row["name"])


def _single_column_value(row: Any, key: str) -> Any:
    if _is_sequence_row(row):
        return row[0]
    return row[key]


def _build_like_pattern(keyword: str) -> str:
    """构造 LIKE 模糊匹配模式，并转义特殊字符。"""
    escaped_keyword = (
        keyword.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped_keyword}%"


def _is_missing_column_error(exc: Exception, column_name: str) -> bool:
    """判断异常是否由缺失列导致（兼容不同数据库报错文案）。"""
    lowered = str(exc).lower()
    col = column_name.strip().lower()
    if not col:
        return False
    return (
        f"no such column: {col}" in lowered
        or f"has no column named {col}" in lowered
    )


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """
    初始化数据库，创建 tasks 表和必要索引。

    Args:
        db_path: 数据库文件路径，默认使用项目根目录下 data/app.db。
    """
    init_key = _build_init_key(db_path)
    with _INIT_DB_LOCK:
        if init_key in _INITIALIZED_DB_TARGETS:
            return

        # PostgreSQL 场景优先检查是否已有 schema。
        if _should_use_postgres(db_path) and _is_postgres_schema_ready(db_path):
            _INITIALIZED_DB_TARGETS.add(init_key)
            return

        with get_connection(db_path) as connection:
            if _should_use_postgres(db_path):
                _init_postgres_schema(connection)
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        bilibili_url TEXT NOT NULL,
                        video_title TEXT NOT NULL,
                        video_duration_seconds INTEGER,
                        audio_file_path TEXT,
                        transcript_text TEXT,
                        transcript_raw_text TEXT,
                        summary_text TEXT,
                        cancel_requested INTEGER NOT NULL DEFAULT 0,
                        error_stage TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        error_updated_at DATETIME,
                        status TEXT NOT NULL DEFAULT 'waiting',
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status)"
                )
                _commit_connection(connection, sync_remote=False)
                _ensure_extra_columns(connection)

        _INITIALIZED_DB_TARGETS.add(init_key)


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
    # 使用北京时间（而不是 SQLite 的 UTC CURRENT_TIMESTAMP）
    beijing_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                """
                INSERT INTO tasks (
                    bilibili_url, video_title, video_duration_seconds, audio_file_path,
                    transcript_text, summary_text, status, cancel_requested, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bilibili_url,
                    video_title,
                    video_duration_seconds,
                    audio_file_path,
                    None,
                    None,
                    normalized_status,
                    0,
                    beijing_time,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容旧 schema（尚未添加 cancel_requested 列）
            if "cancel_requested" not in str(exc):
                raise
            cursor = connection.execute(
                """
                INSERT INTO tasks (
                    bilibili_url, video_title, video_duration_seconds, audio_file_path,
                    transcript_text, summary_text, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bilibili_url,
                    video_title,
                    video_duration_seconds,
                    audio_file_path,
                    None,
                    None,
                    normalized_status,
                    beijing_time,
                ),
            )
        _commit_connection(connection, sync_remote=True)
        return int(cursor.lastrowid)


def claim_next_waiting_task(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Optional[TaskQueueItem]:
    """
    原子认领一个 waiting 任务，并将其状态切换为 downloading。

    Returns:
        成功认领时返回 TaskQueueItem；队列为空或竞争失败时返回 None。
    """
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                """
                SELECT id, bilibili_url
                FROM tasks
                WHERE status = ? AND cancel_requested = 0
                ORDER BY datetime(created_at) ASC, id ASC
                LIMIT 1
                """,
                (TaskStatus.WAITING.value,),
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容旧 schema（尚未添加 cancel_requested 列）
            if "cancel_requested" not in str(exc):
                raise
            cursor = connection.execute(
                """
                SELECT id, bilibili_url
                FROM tasks
                WHERE status = ?
                ORDER BY datetime(created_at) ASC, id ASC
                LIMIT 1
                """,
                (TaskStatus.WAITING.value,),
            )
        row = cursor.fetchone()
        if not row:
            return None

        if _is_sequence_row(row):
            task_id = int(row[0])
            task_url = str(row[1])
        else:
            task_id = int(row["id"])
            task_url = str(row["bilibili_url"])

        try:
            update_cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?, cancel_requested = 0
                WHERE id = ? AND status = ? AND cancel_requested = 0
                """,
                (TaskStatus.DOWNLOADING.value, task_id, TaskStatus.WAITING.value),
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容旧 schema（尚未添加 cancel_requested 列）
            if "cancel_requested" not in str(exc):
                raise
            update_cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?
                WHERE id = ? AND status = ?
                """,
                (TaskStatus.DOWNLOADING.value, task_id, TaskStatus.WAITING.value),
            )
        claimed_rows = int(getattr(update_cursor, "rowcount", 0) or 0)
        if claimed_rows <= 0:
            _commit_connection(connection, sync_remote=False)
            return None

        _commit_connection(connection, sync_remote=True)
        return TaskQueueItem(id=task_id, bilibili_url=task_url)


def recover_interrupted_tasks(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> int:
    """
    启动时回收异常中断的任务：将中间态回退到 waiting/cancelled。

    Returns:
        被回收的任务条数。
    """
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = CASE
                        WHEN cancel_requested = 1 THEN ?
                        ELSE ?
                    END,
                    cancel_requested = 0
                WHERE status IN (?, ?, ?)
                """,
                (
                    TaskStatus.CANCELLED.value,
                    TaskStatus.WAITING.value,
                    TaskStatus.DOWNLOADING.value,
                    TaskStatus.TRANSCRIBING.value,
                    TaskStatus.SUMMARIZING.value,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容旧 schema（尚未添加 cancel_requested 列）
            if "cancel_requested" not in str(exc):
                raise
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?
                WHERE status IN (?, ?, ?)
                """,
                (
                    TaskStatus.WAITING.value,
                    TaskStatus.DOWNLOADING.value,
                    TaskStatus.TRANSCRIBING.value,
                    TaskStatus.SUMMARIZING.value,
                ),
            )
        _commit_connection(connection, sync_remote=True)
        return int(getattr(cursor, "rowcount", 0) or 0)


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


def request_task_cancel(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> bool:
    """
    请求取消任务。

    waiting 任务会直接进入 cancelled，进行中的任务会置 cancel_requested=1 等待协作中止。
    """
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET cancel_requested = CASE
                        WHEN status = ? THEN 0
                        ELSE 1
                    END,
                    status = CASE
                        WHEN status = ? THEN ?
                        ELSE status
                    END
                WHERE id = ?
                  AND status IN (?, ?, ?, ?)
                """,
                (
                    TaskStatus.WAITING.value,
                    TaskStatus.WAITING.value,
                    TaskStatus.CANCELLED.value,
                    task_id,
                    TaskStatus.WAITING.value,
                    TaskStatus.DOWNLOADING.value,
                    TaskStatus.TRANSCRIBING.value,
                    TaskStatus.SUMMARIZING.value,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # 兼容旧 schema：仅支持 waiting -> cancelled
            if "cancel_requested" not in str(exc):
                raise
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?
                WHERE id = ? AND status = ?
                """,
                (TaskStatus.CANCELLED.value, task_id, TaskStatus.WAITING.value),
            )
        _commit_connection(connection, sync_remote=True)
        return int(getattr(cursor, "rowcount", 0) or 0) > 0


def clear_task_cancel_request(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """清除取消请求标记。"""
    try:
        _update_fields(task_id, {"cancel_requested": 0}, db_path)
    except Exception as exc:  # noqa: BLE001
        if "cancel_requested" in str(exc):
            return
        raise


def is_task_cancel_requested(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> bool:
    """检查任务是否收到取消请求。"""
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                "SELECT cancel_requested FROM tasks WHERE id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if not row:
                return False
            value = row[0] if _is_sequence_row(row) else row["cancel_requested"]
            return bool(int(value or 0))
        except Exception as exc:  # noqa: BLE001
            if "cancel_requested" in str(exc):
                return False
            raise


def update_task_error(
    task_id: int,
    *,
    error_stage: Optional[str],
    error_code: Optional[str],
    error_message: Optional[str],
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """写入任务结构化错误信息。"""
    try:
        _update_fields(
            task_id,
            {
                "error_stage": (error_stage or "").strip() or None,
                "error_code": (error_code or "").strip() or None,
                "error_message": (error_message or "").strip() or None,
                "error_updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            },
            db_path,
        )
    except Exception as exc:  # noqa: BLE001
        # 兼容旧 schema（尚未添加 error_* 列）
        if (
            _is_missing_column_error(exc, "error_stage")
            or _is_missing_column_error(exc, "error_code")
            or _is_missing_column_error(exc, "error_message")
            or _is_missing_column_error(exc, "error_updated_at")
        ):
            return
        raise


def clear_task_error(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """清空任务结构化错误信息。"""
    try:
        _update_fields(
            task_id,
            {
                "error_stage": None,
                "error_code": None,
                "error_message": None,
                "error_updated_at": None,
            },
            db_path,
        )
    except Exception as exc:  # noqa: BLE001
        # 兼容旧 schema（尚未添加 error_* 列）
        if (
            _is_missing_column_error(exc, "error_stage")
            or _is_missing_column_error(exc, "error_code")
            or _is_missing_column_error(exc, "error_message")
            or _is_missing_column_error(exc, "error_updated_at")
        ):
            return
        raise


def update_task_content(
    task_id: int,
    transcript_text: Optional[str] = None,
    transcript_raw_text: Optional[str] = None,
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
        transcript_raw_text: 原始逐字稿文本（未补标点）。
        summary_text: 总结文本。
        audio_file_path: 音频文件相对路径。
        video_title: 视频标题（可更新）。
        video_duration_seconds: 视频时长（秒）。
        db_path: 数据库文件路径。
    """
    fields: Dict[str, Any] = {}
    if transcript_text is not None:
        fields["transcript_text"] = transcript_text
    if transcript_raw_text is not None:
        fields["transcript_raw_text"] = transcript_raw_text
    if summary_text is not None:
        fields["summary_text"] = summary_text
    if audio_file_path is not None:
        fields["audio_file_path"] = audio_file_path
    if video_title is not None:
        fields["video_title"] = video_title
    if video_duration_seconds is not None:
        fields["video_duration_seconds"] = video_duration_seconds
    try:
        _update_fields(task_id, fields, db_path)
    except Exception as exc:  # noqa: BLE001
        if "transcript_raw_text" not in fields:
            raise
        if "transcript_raw_text" not in str(exc):
            raise
        fallback_fields = dict(fields)
        fallback_fields.pop("transcript_raw_text", None)
        if fallback_fields:
            _update_fields(task_id, fallback_fields, db_path)


def get_task(
    task_id: int,
    db_path: Path | str = DEFAULT_DB_PATH,
    include_content: bool = True,
) -> Optional[Task]:
    """
    按主键获取单条任务记录。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。
        include_content: 是否返回 transcript/summary 全文。

    Returns:
        对应的 Task 对象，未找到时返回 None。
    """
    if include_content:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        WHERE id = ?
        """
    else:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
        FROM tasks
        WHERE id = ?
        """

    with get_connection(db_path) as connection:
        cursor = connection.execute(sql, (task_id,))
        row = cursor.fetchone()
        return Task.from_row(row) if row else None


def get_task_summary(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[str]:
    """按任务 ID 读取总结文本。"""
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "SELECT summary_text FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return row[0] if _is_sequence_row(row) else row["summary_text"]


def get_task_transcript(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[str]:
    """按任务 ID 读取转录文本。"""
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "SELECT transcript_text FROM tasks WHERE id = ?",
            (task_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return row[0] if _is_sequence_row(row) else row["transcript_text"]


def get_task_raw_transcript(task_id: int, db_path: Path | str = DEFAULT_DB_PATH) -> Optional[str]:
    """按任务 ID 读取原始转录文本（未补标点）。"""
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                "SELECT transcript_raw_text FROM tasks WHERE id = ?",
                (task_id,),
            )
        except Exception as exc:  # noqa: BLE001
            if "transcript_raw_text" in str(exc):
                return None
            raise
        row = cursor.fetchone()
        if not row:
            return None
        return row[0] if _is_sequence_row(row) else row["transcript_raw_text"]


def get_task_error_info(
    task_id: int, db_path: Path | str = DEFAULT_DB_PATH
) -> Optional[Dict[str, Optional[str]]]:
    """读取任务结构化错误信息。"""
    with get_connection(db_path) as connection:
        try:
            cursor = connection.execute(
                """
                SELECT error_stage, error_code, error_message, error_updated_at
                FROM tasks
                WHERE id = ?
                """,
                (task_id,),
            )
        except Exception as exc:  # noqa: BLE001
            if (
                _is_missing_column_error(exc, "error_stage")
                or _is_missing_column_error(exc, "error_code")
                or _is_missing_column_error(exc, "error_message")
                or _is_missing_column_error(exc, "error_updated_at")
            ):
                return None
            raise
        row = cursor.fetchone()
        if not row:
            return None

        if _is_sequence_row(row):
            stage, code, message, updated_at = row[0], row[1], row[2], row[3]
        else:
            stage = row["error_stage"]
            code = row["error_code"]
            message = row["error_message"]
            updated_at = row["error_updated_at"]

        if not (stage or code or message):
            return None
        return {
            "stage": str(stage) if stage is not None else None,
            "code": str(code) if code is not None else None,
            "message": str(message) if message is not None else None,
            "updated_at": str(updated_at) if updated_at is not None else None,
        }


def list_tasks(
    limit: Optional[int] = None,
    db_path: Path | str = DEFAULT_DB_PATH,
    include_content: bool = True,
) -> List[Task]:
    """
    获取任务列表，按创建时间倒序排列。

    Args:
        limit: 限制返回的最大行数，None 时返回全部。
        db_path: 数据库文件路径。
        include_content: 是否返回 transcript/summary 全文。

    Returns:
        Task 对象列表。
    """
    if include_content:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        ORDER BY datetime(created_at) DESC
        """
    else:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
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


def count_tasks(
    db_path: Path | str = DEFAULT_DB_PATH,
    title_keyword: Optional[str] = None,
) -> int:
    """
    获取任务总数。

    Args:
        db_path: 数据库文件路径。
        title_keyword: 标题模糊搜索关键词（可选）。

    Returns:
        当前任务总条数。
    """
    with get_connection(db_path) as connection:
        if title_keyword and title_keyword.strip():
            cursor = connection.execute(
                "SELECT COUNT(1) AS total FROM tasks WHERE video_title LIKE ? ESCAPE '\\'",
                (_build_like_pattern(title_keyword.strip()),),
            )
        else:
            cursor = connection.execute("SELECT COUNT(1) AS total FROM tasks")
        row = cursor.fetchone()
        if not row:
            return 0
        return int(_single_column_value(row, "total") or 0)


def list_tasks_paginated_with_total(
    page: int,
    page_size: int,
    db_path: Path | str = DEFAULT_DB_PATH,
    include_content: bool = False,
    title_keyword: Optional[str] = None,
) -> tuple[List[Task], int]:
    """
    在同一连接内读取分页数据与总数，减少页面渲染时的数据库往返次数。

    Args:
        page: 页码（从 1 开始）。
        page_size: 每页条数。
        db_path: 数据库文件路径。
        include_content: 是否返回 transcript/summary 全文。
        title_keyword: 标题模糊搜索关键词（可选）。

    Returns:
        (当前页任务列表, 总条数)。
    """
    normalized_page = max(int(page), 1)
    normalized_page_size = max(int(page_size), 1)
    pattern: Optional[str] = None
    if title_keyword and title_keyword.strip():
        pattern = _build_like_pattern(title_keyword.strip())

    if include_content and pattern is not None:
        page_sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        WHERE video_title LIKE ? ESCAPE '\\'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
    elif include_content:
        page_sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
    elif pattern is not None:
        page_sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
        FROM tasks
        WHERE video_title LIKE ? ESCAPE '\\'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
    else:
        page_sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
        FROM tasks
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """

    with get_connection(db_path) as connection:
        if pattern is not None:
            total_cursor = connection.execute(
                "SELECT COUNT(1) AS total FROM tasks WHERE video_title LIKE ? ESCAPE '\\'",
                (pattern,),
            )
        else:
            total_cursor = connection.execute("SELECT COUNT(1) AS total FROM tasks")

        total_row = total_cursor.fetchone()
        total_count = int(_single_column_value(total_row, "total") or 0) if total_row else 0
        if total_count <= 0:
            return [], 0

        total_pages = max((total_count + normalized_page_size - 1) // normalized_page_size, 1)
        normalized_page = min(normalized_page, total_pages)
        offset = (normalized_page - 1) * normalized_page_size

        if pattern is not None:
            params: tuple[Any, ...] = (pattern, normalized_page_size, offset)
        else:
            params = (normalized_page_size, offset)

        cursor = connection.execute(page_sql, params)
        rows = cursor.fetchall()
        tasks = [Task.from_row(row) for row in rows]
        return tasks, total_count


def list_tasks_paginated(
    page: int,
    page_size: int,
    db_path: Path | str = DEFAULT_DB_PATH,
    include_content: bool = False,
    title_keyword: Optional[str] = None,
) -> List[Task]:
    """
    获取分页任务列表，按创建时间倒序排列。

    Args:
        page: 页码（从 1 开始）。
        page_size: 每页条数。
        db_path: 数据库文件路径。
        include_content: 是否返回 transcript/summary 全文。
        title_keyword: 标题模糊搜索关键词（可选）。

    Returns:
        当前页 Task 对象列表。
    """
    normalized_page = max(int(page), 1)
    normalized_page_size = max(int(page_size), 1)
    offset = (normalized_page - 1) * normalized_page_size
    pattern: Optional[str] = None
    if title_keyword and title_keyword.strip():
        pattern = _build_like_pattern(title_keyword.strip())

    if include_content and pattern is not None:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        WHERE video_title LIKE ? ESCAPE '\\'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
        params: tuple[Any, ...] = (pattern, normalized_page_size, offset)
    elif include_content:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               transcript_text, summary_text, status, created_at
        FROM tasks
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
        params = (normalized_page_size, offset)
    elif pattern is not None:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
        FROM tasks
        WHERE video_title LIKE ? ESCAPE '\\'
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
        params = (pattern, normalized_page_size, offset)
    else:
        sql = """
        SELECT id, bilibili_url, video_title, video_duration_seconds, audio_file_path,
               NULL AS transcript_text, NULL AS summary_text, status, created_at
        FROM tasks
        ORDER BY datetime(created_at) DESC
        LIMIT ? OFFSET ?
        """
        params = (normalized_page_size, offset)

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
        _commit_connection(connection, sync_remote=True)


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
        _commit_connection(connection, sync_remote=True)
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
        _commit_connection(connection, sync_remote=True)
        return cursor.rowcount


@contextmanager
def get_connection(
    db_path: Path | str = DEFAULT_DB_PATH,
) -> Generator[Any, None, None]:
    """
    生成带有基础配置的数据库连接上下文。

    Args:
        db_path: 数据库文件路径。

    Yields:
        可执行 SQL 的连接对象（SQLite/Cloudflare D1/PostgreSQL）。
    """
    should_close = False
    if _should_use_cloudflare_d1(db_path):
        connection = _connect_cloudflare_d1(db_path)
        should_close = True
    elif _should_use_postgres(db_path):
        connection = _connect_postgres(db_path)
        should_close = True
    else:
        path = _normalize_db_path(db_path)
        connection = sqlite3.connect(
            str(path),
            timeout=CONNECTION_TIMEOUT_SECONDS,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        connection.row_factory = sqlite3.Row
        should_close = True
    try:
        yield connection
    finally:
        if should_close:
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
        "transcript_raw_text",
        "summary_text",
        "cancel_requested",
        "error_stage",
        "error_code",
        "error_message",
        "error_updated_at",
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
        _commit_connection(connection, sync_remote=True)


def _normalize_db_path(db_path: Path | str) -> Path:
    """确保数据库目录存在并返回规范化后的 Path。"""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _build_init_key(db_path: Path | str) -> str:
    if _should_use_cloudflare_d1(db_path):
        credentials = _resolve_cloudflare_d1_credentials(db_path)
        return f"d1::{credentials.account_id}::{credentials.database_id}"
    if _should_use_postgres(db_path):
        return f"postgres::{SUPABASE_POSTGRES_URL}"
    return f"sqlite::{_normalize_db_path(db_path)}"


_POSTGRES_REQUIRED_COLUMNS = {
    "id",
    "bilibili_url",
    "video_title",
    "video_duration_seconds",
    "audio_file_path",
    "transcript_text",
    "transcript_raw_text",
    "summary_text",
    "cancel_requested",
    "error_stage",
    "error_code",
    "error_message",
    "error_updated_at",
    "status",
    "created_at",
}


def _is_postgres_schema_ready(db_path: Path | str) -> bool:
    """检查 PostgreSQL 是否已有完整 tasks 表 schema。"""
    if not _should_use_postgres(db_path):
        return False
    try:
        conn = _connect_postgres(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = 'tasks'"
            )
            if int(cursor.fetchone()[0]) == 0:
                return False

            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tasks'"
            )
            columns = {str(row[0]) for row in cursor.fetchall()}
            return _POSTGRES_REQUIRED_COLUMNS.issubset(columns)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


def _init_postgres_schema(connection: Any) -> None:
    """初始化 PostgreSQL tasks 表 schema。"""
    cursor = connection.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            bilibili_url TEXT NOT NULL,
            video_title TEXT NOT NULL,
            video_duration_seconds INTEGER,
            audio_file_path TEXT,
            transcript_text TEXT,
            transcript_raw_text TEXT,
            summary_text TEXT,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            error_stage TEXT,
            error_code TEXT,
            error_message TEXT,
            error_updated_at TIMESTAMP,
            status TEXT NOT NULL DEFAULT 'waiting',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at DESC)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status)"
    )
    connection.commit()
    _ensure_extra_columns_postgres(connection)


def _ensure_extra_columns_postgres(connection: Any) -> None:
    """确保 PostgreSQL 表有额外列（兼容旧 schema）。"""
    extra_columns = {
        "transcription_progress": "TEXT",
        "transcription_total_chunks": "INTEGER",
        "transcription_completed_chunks": "INTEGER",
    }
    cursor = connection.cursor()
    cursor.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'tasks'"
    )
    existing_columns = {str(row[0]) for row in cursor.fetchall()}

    for col_name, col_type in extra_columns.items():
        if col_name not in existing_columns:
            cursor.execute(
                f"ALTER TABLE tasks ADD COLUMN {col_name} {col_type}"
            )
    connection.commit()


def _is_remote_target(db_path: Path | str) -> bool:
    target = str(db_path).strip()
    return any(target.startswith(scheme) for scheme in REMOTE_DB_SCHEMES)


def _is_default_db_path(db_path: Path | str) -> bool:
    try:
        return Path(db_path).expanduser().resolve() == Path(
            DEFAULT_DB_PATH
        ).expanduser().resolve()
    except Exception:  # noqa: BLE001
        return False


def _has_cloudflare_d1_config() -> bool:
    return bool(
        CLOUDFLARE_ACCOUNT_ID
        and CLOUDFLARE_D1_DATABASE_ID
        and CLOUDFLARE_API_TOKEN
    )


def _should_use_cloudflare_d1(db_path: Path | str) -> bool:
    if not _has_cloudflare_d1_config():
        return False
    return _is_default_db_path(db_path)


def _resolve_cloudflare_d1_credentials(db_path: Path | str) -> D1Credentials:
    if not _should_use_cloudflare_d1(db_path):
        raise RuntimeError("Cloudflare D1 未启用：缺少凭据或目标不是默认数据库。")

    return D1Credentials(
        account_id=str(CLOUDFLARE_ACCOUNT_ID),
        database_id=str(CLOUDFLARE_D1_DATABASE_ID),
        api_token=str(CLOUDFLARE_API_TOKEN),
    )


def _connect_cloudflare_d1(db_path: Path | str) -> D1Connection:
    credentials = _resolve_cloudflare_d1_credentials(db_path)
    return D1Connection(credentials)


def _should_use_postgres(db_path: Path | str) -> bool:
    """检查是否应使用 Supabase PostgreSQL。"""
    if _should_use_cloudflare_d1(db_path):
        return False
    if _is_default_db_path(db_path) and SUPABASE_POSTGRES_URL:
        return True
    return False


def _connect_postgres(db_path: Path | str) -> Any:
    """创建 PostgreSQL 连接。

    支持两种模式：
    1. 有 SOCKS5_PROXY 环境变量且代理可用：使用 SOCKS5 代理连接（本地开发）
    2. 无代理或代理不可用：直接连接 + SSL（Streamlit Cloud 等部署环境）
    """
    if psycopg2 is None:
        raise RuntimeError(
            "已启用 PostgreSQL 连接，但未安装 psycopg2。请先执行: pip install psycopg2-binary"
        )
    if not SUPABASE_POSTGRES_URL:
        raise RuntimeError("未配置 SUPABASE_POSTGRES_URL，无法连接 PostgreSQL。")

    from urllib.parse import urlparse

    # 检查是否配置了 SOCKS5 代理
    socks_proxy = os.getenv("SOCKS5_PROXY") or os.getenv("SOCKS_PROXY")

    # 解析 URL
    parsed = urlparse(SUPABASE_POSTGRES_URL)
    hostname = parsed.hostname
    port = parsed.port or 5432
    database = parsed.path.lstrip("/") or "postgres"
    user = parsed.username or "postgres"
    password = parsed.password or ""

    def try_direct_connect():
        """尝试直接连接（用于 Streamlit Cloud）"""
        try:
            conn = psycopg2.connect(
                host=hostname,
                port=port,
                database=database,
                user=user,
                password=password,
                sslmode="require",
            )
            conn.autocommit = False
            LOGGER.info(f"直接连接到 {hostname}:{port} (SSL)")
            return conn
        except Exception as exc:
            raise RuntimeError(f"PostgreSQL 直接连接失败: {exc}") from exc

    if socks_proxy and socks_proxy.startswith("socks5://"):
        # 本地开发环境：尝试使用 SOCKS5 代理
        import socks

        proxy_parts = socks_proxy.replace("socks5://", "").split(":")
        proxy_host = proxy_parts[0]
        proxy_port = int(proxy_parts[1]) if len(proxy_parts) > 1 else 7890

        try:
            # 先测试 SOCKS5 代理是否可用
            test_sock = socks.socksocket(socket.AF_INET, socket.SOCK_STREAM)
            test_sock.set_proxy(socks.SOCKS5, proxy_host, proxy_port, True)
            test_sock.settimeout(3)
            test_sock.connect(("127.0.0.1", proxy_port))
            test_sock.close()
            proxy_available = True
            LOGGER.info(f"SOCKS5 代理可用: {proxy_host}:{proxy_port}")
        except Exception:
            proxy_available = False
            LOGGER.warning(f"SOCKS5 代理不可用，降级到直接连接")

        if proxy_available:
            # 使用 SOCKS5 代理连接
            class Socks5Socket:
                """包装 socket，通过 SOCKS5 代理连接"""
                def __init__(self):
                    self._sock = None

                def __call__(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
                    if family != socket.AF_INET or type != socket.SOCK_STREAM:
                        return socket.socket(family, type, proto)
                    self._sock = socks.socksocket(family, type, proto)
                    self._sock.set_proxy(socks.SOCKS5, proxy_host, proxy_port, True)
                    self._sock.settimeout(30)
                    return self._sock

                def __getattr__(self, name):
                    return getattr(self._sock, name)

            original_socket = socket.socket
            original_socket = socket.socket
            try:
                socket.socket = Socks5Socket()
                conn = psycopg2.connect(
                    host=hostname,
                    port=port,
                    database=database,
                    user=user,
                    password=password,
                    sslmode="require",
                )
                conn.autocommit = False
                LOGGER.info(f"通过 SOCKS5 代理连接到 {hostname}:{port}")
                return conn
            except Exception as exc:
                LOGGER.warning(f"SOCKS5 代理连接失败: {exc}，尝试直接连接")
                socket.socket = original_socket
                return try_direct_connect()
        else:
            # 代理不可用，直接连接
            return try_direct_connect()

    # 无代理或非 SOCKS5 配置，直接连接
    return try_direct_connect()


def _commit_connection(connection: Any, sync_remote: bool) -> None:
    connection.commit()


def _validate_status(status: str) -> str:
    """校验并返回合法状态值。"""
    if status not in TaskStatus.values():
        raise ValueError(f"非法状态值: {status}，合法取值: {TaskStatus.values()}")
    return status


def _ensure_extra_columns(connection: Any) -> None:
    """为旧表补充新增列，避免因 schema 变更导致异常。"""
    cursor = connection.execute("PRAGMA table_info(tasks);")
    existing = {_table_info_name(row) for row in cursor.fetchall()}
    if "video_duration_seconds" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 video_duration_seconds")
        connection.execute("ALTER TABLE tasks ADD COLUMN video_duration_seconds INTEGER;")
    if "transcript_raw_text" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 transcript_raw_text")
        connection.execute("ALTER TABLE tasks ADD COLUMN transcript_raw_text TEXT;")
    if "cancel_requested" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 cancel_requested")
        connection.execute("ALTER TABLE tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;")
    _ensure_transcription_columns(connection)
    _ensure_error_columns(connection)


def _ensure_transcription_columns(connection: Any) -> None:
    """为转写进度功能添加必要字段（幂等操作）。"""
    cursor = connection.execute("PRAGMA table_info(tasks);")
    existing = {_table_info_name(row) for row in cursor.fetchall()}

    if "transcription_progress" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 transcription_progress")
        connection.execute("ALTER TABLE tasks ADD COLUMN transcription_progress TEXT;")
    if "transcription_total_chunks" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 transcription_total_chunks")
        connection.execute("ALTER TABLE tasks ADD COLUMN transcription_total_chunks INTEGER;")
    if "transcription_completed_chunks" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 transcription_completed_chunks")
        connection.execute("ALTER TABLE tasks ADD COLUMN transcription_completed_chunks INTEGER;")

    _commit_connection(connection, sync_remote=False)


def _ensure_error_columns(connection: Any) -> None:
    """为结构化错误信息添加必要字段（幂等操作）。"""
    cursor = connection.execute("PRAGMA table_info(tasks);")
    existing = {_table_info_name(row) for row in cursor.fetchall()}

    if "error_stage" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 error_stage")
        connection.execute("ALTER TABLE tasks ADD COLUMN error_stage TEXT;")
    if "error_code" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 error_code")
        connection.execute("ALTER TABLE tasks ADD COLUMN error_code TEXT;")
    if "error_message" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 error_message")
        connection.execute("ALTER TABLE tasks ADD COLUMN error_message TEXT;")
    if "error_updated_at" not in existing:
        LOGGER.info("数据库迁移：tasks 新增列 error_updated_at")
        connection.execute("ALTER TABLE tasks ADD COLUMN error_updated_at DATETIME;")

    _commit_connection(connection, sync_remote=False)


def update_transcription_progress(
    task_id: int,
    chunk_index: int,
    total_chunks: int,
    chunk_text: str,
    start_sec: float,
    end_sec: float,
    db_path: Path | str = DEFAULT_DB_PATH,
) -> None:
    """
    更新指定切片的转写进度。

    Args:
        task_id: 任务主键。
        chunk_index: 切片索引（从 0 开始）。
        total_chunks: 总切片数。
        chunk_text: 切片转写文本。
        start_sec: 切片起始时间（秒）。
        end_sec: 切片结束时间（秒）。
        db_path: 数据库文件路径。
    """
    with get_connection(db_path) as connection:
        # 读取现有进度
        cursor = connection.execute(
            "SELECT transcription_progress FROM tasks WHERE id = ?",
            (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            return

        existing_json = _single_column_value(row, "transcription_progress")
        if existing_json:
            progress = json.loads(existing_json)
        else:
            # 初始化进度结构
            progress = {
                "total_chunks": total_chunks,
                "completed_chunks": 0,
                "chunks": [
                    {
                        "index": i,
                        "start_sec": 0,
                        "end_sec": 0,
                        "text": None,
                        "completed": False
                    }
                    for i in range(total_chunks)
                ]
            }

        # 更新指定切片
        if chunk_index < len(progress["chunks"]):
            progress["chunks"][chunk_index] = {
                "index": chunk_index,
                "start_sec": start_sec,
                "end_sec": end_sec,
                "text": chunk_text,
                "completed": True
            }

        # 重新计算已完成数量
        completed_count = sum(1 for c in progress["chunks"] if c["completed"])
        progress["completed_chunks"] = completed_count

        # 写回数据库（异步同步到远程，不阻塞主流程）
        connection.execute(
            """
            UPDATE tasks
            SET transcription_progress = ?,
                transcription_total_chunks = ?,
                transcription_completed_chunks = ?
            WHERE id = ?
            """,
            (json.dumps(progress, ensure_ascii=False), total_chunks, completed_count, task_id)
        )
        _commit_connection(connection, sync_remote=False)


def get_transcription_progress(
    task_id: int, db_path: Path | str = DEFAULT_DB_PATH
) -> Optional[Dict[str, Any]]:
    """
    获取任务的转写进度信息。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。

    Returns:
        进度 JSON 字典，未找到或无进度时返回 None。
    """
    with get_connection(db_path) as connection:
        cursor = connection.execute(
            "SELECT transcription_progress FROM tasks WHERE id = ?",
            (task_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        progress_raw = _single_column_value(row, "transcription_progress")
        if not progress_raw:
            return None
        return json.loads(progress_raw)


def reset_transcription_data(
    task_id: int, db_path: Path | str = DEFAULT_DB_PATH
) -> None:
    """
    清空任务的转写进度与文本结果，用于从头重新转写。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。
    """
    with get_connection(db_path) as connection:
        try:
            connection.execute(
                """
                UPDATE tasks
                SET transcript_text = NULL,
                    transcript_raw_text = NULL,
                    summary_text = NULL,
                    transcription_progress = NULL,
                    transcription_total_chunks = NULL,
                    transcription_completed_chunks = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
        except Exception as exc:  # noqa: BLE001
            if "transcript_raw_text" not in str(exc):
                raise
            connection.execute(
                """
                UPDATE tasks
                SET transcript_text = NULL,
                    summary_text = NULL,
                    transcription_progress = NULL,
                    transcription_total_chunks = NULL,
                    transcription_completed_chunks = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
        _commit_connection(connection, sync_remote=True)


def assemble_partial_transcript(
    task_id: int, db_path: Path | str = DEFAULT_DB_PATH
) -> str:
    """
    从进度 JSON 中拼接已完成切片的文本（用于失败时保存部分结果）。

    Args:
        task_id: 任务主键。
        db_path: 数据库文件路径。

    Returns:
        已完成切片的拼接文本。
    """
    progress = get_transcription_progress(task_id, db_path)
    if not progress:
        return ""

    completed_texts = [
        chunk["text"]
        for chunk in progress.get("chunks", [])
        if chunk.get("completed") and chunk.get("text")
    ]
    return " ".join(completed_texts).strip()
