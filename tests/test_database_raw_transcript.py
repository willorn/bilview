"""原始转录字段读写测试。"""
from __future__ import annotations

from pathlib import Path

from db.database import (
    create_task,
    get_task_raw_transcript,
    get_task_summary,
    get_task_transcript,
    init_db,
    reset_transcription_data,
    update_task_content,
)


def test_raw_transcript_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    init_db(db_path)
    task_id = create_task(
        bilibili_url="https://example.com/video",
        video_title="测试任务",
        db_path=db_path,
    )

    update_task_content(
        task_id,
        transcript_text="这是带标点的阅读版。",
        transcript_raw_text="这是带标点的阅读版",
        summary_text="总结内容",
        db_path=db_path,
    )

    assert get_task_transcript(task_id, db_path) == "这是带标点的阅读版。"
    assert get_task_raw_transcript(task_id, db_path) == "这是带标点的阅读版"
    assert get_task_summary(task_id, db_path) == "总结内容"

    reset_transcription_data(task_id, db_path)

    assert get_task_transcript(task_id, db_path) is None
    assert get_task_raw_transcript(task_id, db_path) is None
    assert get_task_summary(task_id, db_path) is None
