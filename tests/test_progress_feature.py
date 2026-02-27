"""
测试转写进度功能的脚本。

验证：
1. 数据库字段迁移
2. 进度更新和读取
3. 部分结果拼接
"""
from pathlib import Path
import tempfile

from db.database import (
    init_db,
    create_task,
    update_transcription_progress,
    get_transcription_progress,
    assemble_partial_transcript,
    get_connection,
)


def test_database_migration():
    """测试数据库迁移是否成功添加新字段。"""
    print("测试 1: 数据库迁移...")

    # 使用临时数据库
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        # 初始化数据库
        init_db(db_path)

        # 检查字段是否存在
        with get_connection(db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(tasks);")
            columns = {row["name"] for row in cursor.fetchall()}

            required_columns = {
                "transcription_progress",
                "transcription_total_chunks",
                "transcription_completed_chunks"
            }

            missing = required_columns - columns
            if missing:
                print(f"  ❌ 缺少字段: {missing}")
                return False

            print("  ✓ 所有必需字段已添加")
            return True
    finally:
        db_path.unlink(missing_ok=True)


def test_progress_update():
    """测试进度更新功能。"""
    print("\n测试 2: 进度更新...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        init_db(db_path)

        # 创建测试任务
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="测试视频",
            db_path=db_path
        )

        # 模拟 3 个切片的转写
        total_chunks = 3
        for i in range(total_chunks):
            update_transcription_progress(
                task_id=task_id,
                chunk_index=i,
                total_chunks=total_chunks,
                chunk_text=f"切片 {i + 1} 的文本内容",
                start_sec=i * 300,
                end_sec=(i + 1) * 300,
                db_path=db_path
            )

        # 读取进度
        progress = get_transcription_progress(task_id, db_path)

        if not progress:
            print("  ❌ 无法读取进度")
            return False

        if progress["total_chunks"] != total_chunks:
            print(f"  ❌ 总切片数不匹配: {progress['total_chunks']} != {total_chunks}")
            return False

        if progress["completed_chunks"] != total_chunks:
            print(f"  ❌ 已完成切片数不匹配: {progress['completed_chunks']} != {total_chunks}")
            return False

        print(f"  ✓ 进度更新正常: {progress['completed_chunks']}/{progress['total_chunks']} 切片")
        return True
    finally:
        db_path.unlink(missing_ok=True)


def test_partial_transcript():
    """测试部分结果拼接功能。"""
    print("\n测试 3: 部分结果拼接...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        init_db(db_path)

        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="测试视频",
            db_path=db_path
        )

        # 模拟部分完成（5 个切片中完成了 3 个）
        total_chunks = 5
        completed_chunks = 3

        for i in range(completed_chunks):
            update_transcription_progress(
                task_id=task_id,
                chunk_index=i,
                total_chunks=total_chunks,
                chunk_text=f"文本{i + 1}",
                start_sec=i * 300,
                end_sec=(i + 1) * 300,
                db_path=db_path
            )

        # 拼接部分结果
        partial = assemble_partial_transcript(task_id, db_path)
        expected = "文本1 文本2 文本3"

        if partial != expected:
            print(f"  ❌ 拼接结果不匹配")
            print(f"    期望: {expected}")
            print(f"    实际: {partial}")
            return False

        print(f"  ✓ 部分结果拼接正常: '{partial}'")
        return True
    finally:
        db_path.unlink(missing_ok=True)


def test_resume_from_chunks():
    """测试断点续传数据结构。"""
    print("\n测试 4: 断点续传数据结构...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        init_db(db_path)

        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="测试视频",
            db_path=db_path
        )

        # 模拟部分完成
        total_chunks = 4
        for i in range(2):  # 只完成前 2 个
            update_transcription_progress(
                task_id=task_id,
                chunk_index=i,
                total_chunks=total_chunks,
                chunk_text=f"已完成文本{i + 1}",
                start_sec=i * 300,
                end_sec=(i + 1) * 300,
                db_path=db_path
            )

        # 获取进度用于断点续传
        progress = get_transcription_progress(task_id, db_path)
        resume_chunks = progress["chunks"]

        # 验证数据结构
        if len(resume_chunks) != total_chunks:
            print(f"  ❌ 切片数量不匹配: {len(resume_chunks)} != {total_chunks}")
            return False

        completed_count = sum(1 for c in resume_chunks if c["completed"])
        if completed_count != 2:
            print(f"  ❌ 已完成数量不匹配: {completed_count} != 2")
            return False

        # 验证已完成切片有文本
        for i in range(2):
            if not resume_chunks[i]["text"]:
                print(f"  ❌ 切片 {i} 缺少文本")
                return False

        # 验证未完成切片没有文本
        for i in range(2, total_chunks):
            if resume_chunks[i]["completed"]:
                print(f"  ❌ 切片 {i} 不应标记为已完成")
                return False

        print(f"  ✓ 断点续传数据结构正确")
        return True
    finally:
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("转写进度功能测试")
    print("=" * 60)

    results = []
    results.append(test_database_migration())
    results.append(test_progress_update())
    results.append(test_partial_transcript())
    results.append(test_resume_from_chunks())

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"✓ 所有测试通过 ({passed}/{total})")
        exit(0)
    else:
        print(f"✗ 部分测试失败 ({passed}/{total})")
        exit(1)
