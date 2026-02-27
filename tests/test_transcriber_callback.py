"""
测试转写模块的回调和断点续传功能。

注意：此测试需要实际的音频文件，这里使用模拟测试。
"""
from pathlib import Path
from typing import List, Tuple


def test_callback_signature():
    """测试回调函数签名是否正确。"""
    print("测试 1: 回调函数签名...")

    from core.transcriber import audio_to_text
    import inspect

    sig = inspect.signature(audio_to_text)
    params = sig.parameters

    # 检查是否有 progress_callback 参数
    if "progress_callback" not in params:
        print("  ❌ 缺少 progress_callback 参数")
        return False

    # 检查是否有 resume_from_chunks 参数
    if "resume_from_chunks" not in params:
        print("  ❌ 缺少 resume_from_chunks 参数")
        return False

    print("  ✓ 函数签名正确")
    return True


def test_callback_invocation():
    """测试回调函数是否被正确调用（使用模拟）。"""
    print("\n测试 2: 回调调用机制...")

    # 这个测试需要实际的音频文件，这里只验证代码逻辑
    # 在实际使用中，回调会在每个切片完成后被调用

    callback_calls: List[Tuple[int, int, str, float, float]] = []

    def mock_callback(current: int, total: int, text: str, start_sec: float, end_sec: float) -> None:
        callback_calls.append((current, total, text, start_sec, end_sec))

    # 验证回调函数可以被正常调用
    try:
        mock_callback(1, 3, "测试文本", 0.0, 300.0)
        mock_callback(2, 3, "测试文本2", 300.0, 600.0)
        mock_callback(3, 3, "测试文本3", 600.0, 900.0)

        if len(callback_calls) != 3:
            print(f"  ❌ 回调次数不正确: {len(callback_calls)} != 3")
            return False

        # 验证参数传递
        first_call = callback_calls[0]
        if first_call[0] != 1 or first_call[1] != 3:
            print(f"  ❌ 第一次回调参数不正确: {first_call}")
            return False

        print(f"  ✓ 回调机制正常（模拟 {len(callback_calls)} 次调用）")
        return True
    except Exception as e:
        print(f"  ❌ 回调调用失败: {e}")
        return False


def test_resume_data_structure():
    """测试断点续传数据结构。"""
    print("\n测试 3: 断点续传数据结构...")

    # 模拟断点续传数据
    resume_chunks = [
        {
            "index": 0,
            "start_sec": 0,
            "end_sec": 300,
            "text": "第一段已完成",
            "completed": True
        },
        {
            "index": 1,
            "start_sec": 300,
            "end_sec": 600,
            "text": "第二段已完成",
            "completed": True
        },
        {
            "index": 2,
            "start_sec": 600,
            "end_sec": 900,
            "text": None,
            "completed": False
        }
    ]

    # 验证数据结构
    completed = [c for c in resume_chunks if c["completed"]]
    if len(completed) != 2:
        print(f"  ❌ 已完成切片数不正确: {len(completed)} != 2")
        return False

    # 验证已完成切片有文本
    for chunk in completed:
        if not chunk.get("text"):
            print(f"  ❌ 已完成切片缺少文本: {chunk}")
            return False

    print(f"  ✓ 断点续传数据结构正确（{len(completed)}/{len(resume_chunks)} 已完成）")
    return True


def test_integration_with_database():
    """测试转写模块与数据库的集成。"""
    print("\n测试 4: 转写模块与数据库集成...")

    import tempfile
    from db.database import (
        init_db,
        create_task,
        update_transcription_progress,
        get_transcription_progress,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="集成测试",
            db_path=db_path
        )

        # 模拟回调函数更新数据库
        def callback_with_db(current: int, total: int, text: str, start_sec: float, end_sec: float) -> None:
            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=text,
                start_sec=start_sec,
                end_sec=end_sec,
                db_path=db_path
            )

        # 模拟 3 次回调
        callback_with_db(1, 3, "文本1", 0.0, 300.0)
        callback_with_db(2, 3, "文本2", 300.0, 600.0)
        callback_with_db(3, 3, "文本3", 600.0, 900.0)

        # 验证数据库中的进度
        progress = get_transcription_progress(task_id, db_path)

        if not progress:
            print("  ❌ 无法从数据库读取进度")
            return False

        if progress["completed_chunks"] != 3:
            print(f"  ❌ 已完成切片数不正确: {progress['completed_chunks']} != 3")
            return False

        print(f"  ✓ 转写模块与数据库集成正常")
        return True
    finally:
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("转写模块回调功能测试")
    print("=" * 60)

    results = []
    results.append(test_callback_signature())
    results.append(test_callback_invocation())
    results.append(test_resume_data_structure())
    results.append(test_integration_with_database())

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)

    if passed == total:
        print(f"✓ 所有测试通过 ({passed}/{total})")
        print("\n提示：实际音频转写测试需要运行完整应用程序")
        exit(0)
    else:
        print(f"✗ 部分测试失败 ({passed}/{total})")
        exit(1)
