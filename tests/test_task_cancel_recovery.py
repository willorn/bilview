"""
任务取消与恢复语义测试。

验证点：
1. 进程重启恢复时，已请求取消的中间态任务应落到 cancelled。
2. 认领 waiting 队列时，应跳过 cancel_requested=1 的任务。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from db.database import (
    TaskStatus,
    claim_next_waiting_task,
    create_task,
    get_connection,
    get_task,
    init_db,
    is_task_cancel_requested,
    recover_interrupted_tasks,
    request_task_cancel,
    update_task_status,
)


def test_recover_interrupted_tasks_honors_cancel_request() -> None:
    """恢复中断任务时，已请求取消的运行中任务应被标记为 cancelled。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://example.com/recover-cancel",
            video_title="恢复取消语义测试",
            db_path=db_path,
        )
        update_task_status(task_id, TaskStatus.TRANSCRIBING.value, db_path=db_path)
        assert request_task_cancel(task_id, db_path=db_path) is True
        assert is_task_cancel_requested(task_id, db_path=db_path) is True

        recovered_count = recover_interrupted_tasks(db_path=db_path)
        assert recovered_count == 1

        task = get_task(task_id, db_path=db_path)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED.value
        assert is_task_cancel_requested(task_id, db_path=db_path) is False
    finally:
        db_path.unlink(missing_ok=True)


def test_claim_next_waiting_task_skips_cancel_requested_rows() -> None:
    """认领 waiting 队列时应跳过已标记 cancel_requested=1 的行。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        init_db(db_path)
        skipped_task_id = create_task(
            bilibili_url="https://example.com/skipped",
            video_title="应跳过",
            db_path=db_path,
        )
        expected_task_id = create_task(
            bilibili_url="https://example.com/claimed",
            video_title="应被认领",
            db_path=db_path,
        )

        with get_connection(db_path) as connection:
            connection.execute(
                "UPDATE tasks SET cancel_requested = 1 WHERE id = ?",
                (skipped_task_id,),
            )
            connection.commit()

        queue_item = claim_next_waiting_task(db_path=db_path)
        assert queue_item is not None
        assert queue_item.id == expected_task_id

        skipped_task = get_task(skipped_task_id, db_path=db_path)
        expected_task = get_task(expected_task_id, db_path=db_path)
        assert skipped_task is not None
        assert expected_task is not None
        assert skipped_task.status == TaskStatus.WAITING.value
        assert expected_task.status == TaskStatus.DOWNLOADING.value
    finally:
        db_path.unlink(missing_ok=True)


def test_timeout_status_is_persisted_and_not_recovered() -> None:
    """timeout 应作为独立终态持久化，恢复流程不应将其改回 waiting。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://example.com/timeout",
            video_title="超时状态测试",
            db_path=db_path,
        )
        update_task_status(task_id, TaskStatus.TIMEOUT.value, db_path=db_path)

        recovered_count = recover_interrupted_tasks(db_path=db_path)
        assert recovered_count == 0

        task = get_task(task_id, db_path=db_path)
        assert task is not None
        assert task.status == TaskStatus.TIMEOUT.value
    finally:
        db_path.unlink(missing_ok=True)
