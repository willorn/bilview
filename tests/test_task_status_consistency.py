"""
任务状态一致性测试。

验证点：
1. 原子完成接口会同时写 summary 与 completed 状态。
2. 历史修复接口仅修复“failed 但内容完整”的任务。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from db.database import (
    TaskStatus,
    create_task,
    get_task,
    init_db,
    repair_inconsistent_task_statuses,
    update_task_content,
    update_task_status,
    update_task_summary_and_complete,
)


def test_update_task_summary_and_complete() -> None:
    """验证 summary+completed 的原子更新。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://example.com/video",
            video_title="原子完成测试",
            db_path=db_path,
        )
        update_task_summary_and_complete(task_id, "最终总结内容", db_path=db_path)

        task = get_task(task_id, db_path=db_path)
        assert task is not None
        assert task.status == TaskStatus.COMPLETED.value
        assert task.summary_text == "最终总结内容"
    finally:
        db_path.unlink(missing_ok=True)


def test_repair_inconsistent_task_statuses() -> None:
    """验证只修复内容完整但状态异常的 failed 任务。"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)
    try:
        init_db(db_path)

        should_fix_id = create_task(
            bilibili_url="https://example.com/fix",
            video_title="应修复",
            db_path=db_path,
        )
        update_task_content(
            should_fix_id,
            transcript_text="完整转录",
            summary_text="完整总结",
            db_path=db_path,
        )
        update_task_status(should_fix_id, TaskStatus.FAILED.value, db_path=db_path)

        should_keep_failed_id = create_task(
            bilibili_url="https://example.com/keep",
            video_title="应保持失败",
            db_path=db_path,
        )
        update_task_content(
            should_keep_failed_id,
            transcript_text="只有转录，无总结",
            db_path=db_path,
        )
        update_task_status(should_keep_failed_id, TaskStatus.FAILED.value, db_path=db_path)

        repaired_count = repair_inconsistent_task_statuses(db_path=db_path)
        assert repaired_count == 1

        fixed_task = get_task(should_fix_id, db_path=db_path)
        kept_task = get_task(should_keep_failed_id, db_path=db_path)
        assert fixed_task is not None
        assert kept_task is not None
        assert fixed_task.status == TaskStatus.COMPLETED.value
        assert kept_task.status == TaskStatus.FAILED.value
    finally:
        db_path.unlink(missing_ok=True)
