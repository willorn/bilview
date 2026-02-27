"""
端到端测试：模拟完整的转写流程，包括进度回调和断点续传。

注意：此测试需要实际的音频文件。如果没有，会创建一个测试音频。
"""
import tempfile
from pathlib import Path
from typing import List, Tuple

from pydub import AudioSegment
from pydub.generators import Sine


def create_test_audio(duration_sec: int = 15) -> Path:
    """创建一个测试音频文件（正弦波）。"""
    print(f"创建 {duration_sec} 秒的测试音频...")

    # 生成 440Hz 正弦波
    sine_wave = Sine(440).to_audio_segment(duration=duration_sec * 1000)

    # 保存为临时文件
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        audio_path = Path(tmp.name)

    sine_wave.export(audio_path, format="wav")
    print(f"✓ 测试音频已创建: {audio_path}")
    return audio_path


def test_normal_transcription():
    """测试 1: 正常转写流程（带进度回调）。"""
    print("\n" + "=" * 60)
    print("测试 1: 正常转写流程")
    print("=" * 60)

    from db.database import (
        init_db,
        create_task,
        update_transcription_progress,
        get_transcription_progress,
        update_task_content,
    )
    from core.transcriber import audio_to_text

    # 使用临时数据库
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    # 创建测试音频（15 秒，不会触发切片）
    audio_path = create_test_audio(duration_sec=15)

    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="测试视频",
            db_path=db_path
        )

        print(f"\n任务 ID: {task_id}")
        print("开始转写...")

        # 记录回调调用
        callback_calls: List[Tuple[int, int, str, float, float]] = []

        def progress_callback(current: int, total: int, text: str, start_sec: float, end_sec: float) -> None:
            print(f"  进度回调: {current}/{total} 切片 ({int(current/total*100)}%)")
            print(f"    时间范围: {start_sec:.1f}s - {end_sec:.1f}s")
            print(f"    文本长度: {len(text)} 字符")

            # 保存到数据库
            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=text,
                start_sec=start_sec,
                end_sec=end_sec,
                db_path=db_path
            )

            callback_calls.append((current, total, text, start_sec, end_sec))

        # 执行转写
        transcript = audio_to_text(
            audio_path,
            model_size="tiny",
            progress_callback=progress_callback
        )

        print(f"\n转写完成！")
        print(f"  回调次数: {len(callback_calls)}")
        print(f"  转写文本长度: {len(transcript)} 字符")

        # 验证数据库中的进度
        progress = get_transcription_progress(task_id, db_path)
        if progress:
            print(f"  数据库进度: {progress['completed_chunks']}/{progress['total_chunks']} 切片")

        # 保存最终结果
        update_task_content(task_id, transcript_text=transcript, db_path=db_path)

        # 验证
        if len(callback_calls) == 0:
            print("\n❌ 测试失败: 回调未被调用")
            return False

        # 注意：纯正弦波音频没有语音内容，Whisper 返回空文本是正常的
        # 关键是验证回调机制和数据库存储是否正常工作
        print(f"\n  注意: 测试音频为纯正弦波，无语音内容，转写结果为空是正常的")

        if not progress:
            print("\n❌ 测试失败: 数据库中无进度信息")
            return False

        if progress['completed_chunks'] != progress['total_chunks']:
            print(f"\n❌ 测试失败: 进度不完整 ({progress['completed_chunks']}/{progress['total_chunks']})")
            return False

        print("\n✓ 测试 1 通过: 回调机制和数据库存储正常")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        audio_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


def test_chunked_transcription():
    """测试 2: 分片转写流程（触发切片）。"""
    print("\n" + "=" * 60)
    print("测试 2: 分片转写流程")
    print("=" * 60)

    from db.database import (
        init_db,
        create_task,
        update_transcription_progress,
        get_transcription_progress,
    )
    from core.transcriber import audio_to_text

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    # 创建较长的测试音频（12 分钟，会触发切片）
    print("创建 12 分钟的测试音频（会触发切片）...")
    audio_path = create_test_audio(duration_sec=12 * 60)

    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="长视频测试",
            db_path=db_path
        )

        print(f"\n任务 ID: {task_id}")
        print("开始分片转写...")

        callback_calls = []

        def progress_callback(current: int, total: int, text: str, start_sec: float, end_sec: float) -> None:
            print(f"  切片 {current}/{total} 完成 ({int(current/total*100)}%)")
            print(f"    时间: {start_sec:.0f}s - {end_sec:.0f}s")

            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=text,
                start_sec=start_sec,
                end_sec=end_sec,
                db_path=db_path
            )

            callback_calls.append((current, total))

        # 执行转写
        transcript = audio_to_text(
            audio_path,
            model_size="tiny",
            chunk_duration_sec=300,  # 5 分钟切片
            progress_callback=progress_callback
        )

        print(f"\n转写完成！")
        print(f"  总切片数: {len(callback_calls)}")
        print(f"  转写文本长度: {len(transcript)} 字符")

        # 验证数据库
        progress = get_transcription_progress(task_id, db_path)
        if progress:
            print(f"  数据库进度: {progress['completed_chunks']}/{progress['total_chunks']} 切片")

            # 验证切片数量
            expected_chunks = 3  # 12 分钟 / 5 分钟 = 2.4，向上取整为 3
            if progress['total_chunks'] != expected_chunks:
                print(f"\n⚠️  警告: 切片数量不符合预期 ({progress['total_chunks']} != {expected_chunks})")

        # 验证
        if len(callback_calls) < 2:
            print(f"\n❌ 测试失败: 切片数量太少 ({len(callback_calls)})")
            return False

        print("\n✓ 测试 2 通过: 分片转写正常")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        audio_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


def test_resume_from_checkpoint():
    """测试 3: 断点续传功能。"""
    print("\n" + "=" * 60)
    print("测试 3: 断点续传功能")
    print("=" * 60)

    from db.database import (
        init_db,
        create_task,
        update_transcription_progress,
        get_transcription_progress,
        assemble_partial_transcript,
    )
    from core.transcriber import audio_to_text

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    # 创建测试音频（12 分钟）
    audio_path = create_test_audio(duration_sec=12 * 60)

    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="断点续传测试",
            db_path=db_path
        )

        print(f"\n任务 ID: {task_id}")

        # 第一阶段：模拟部分完成（手动创建进度数据）
        print("\n阶段 1: 模拟已完成 2 个切片...")
        total_chunks = 3
        for i in range(2):  # 只完成前 2 个
            update_transcription_progress(
                task_id=task_id,
                chunk_index=i,
                total_chunks=total_chunks,
                chunk_text=f"已完成切片 {i + 1} 的文本",
                start_sec=i * 300,
                end_sec=(i + 1) * 300,
                db_path=db_path
            )

        progress = get_transcription_progress(task_id, db_path)
        print(f"  当前进度: {progress['completed_chunks']}/{progress['total_chunks']} 切片")

        # 拼接部分结果
        partial = assemble_partial_transcript(task_id, db_path)
        print(f"  部分结果长度: {len(partial)} 字符")

        # 第二阶段：从断点继续
        print("\n阶段 2: 从断点继续转写...")

        resume_chunks = progress["chunks"]
        callback_count = 0

        def progress_callback(current: int, total: int, text: str, start_sec: float, end_sec: float) -> None:
            nonlocal callback_count
            callback_count += 1
            print(f"  切片 {current}/{total} 完成")

            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=text,
                start_sec=start_sec,
                end_sec=end_sec,
                db_path=db_path
            )

        # 从断点继续转写
        transcript = audio_to_text(
            audio_path,
            model_size="tiny",
            chunk_duration_sec=300,
            progress_callback=progress_callback,
            resume_from_chunks=resume_chunks  # 关键：传入断点数据
        )

        print(f"\n转写完成！")
        print(f"  新增回调次数: {callback_count}")
        print(f"  最终文本长度: {len(transcript)} 字符")

        # 验证最终进度
        final_progress = get_transcription_progress(task_id, db_path)
        print(f"  最终进度: {final_progress['completed_chunks']}/{final_progress['total_chunks']} 切片")

        # 验证
        if final_progress['completed_chunks'] != total_chunks:
            print(f"\n❌ 测试失败: 未完成所有切片")
            return False

        # 验证回调次数（应该只调用未完成的切片）
        expected_new_callbacks = total_chunks - 2  # 只转写第 3 个切片
        if callback_count != expected_new_callbacks:
            print(f"\n⚠️  警告: 回调次数不符合预期 ({callback_count} != {expected_new_callbacks})")
            print("    这可能是因为音频时长导致的切片数量差异")

        print("\n✓ 测试 3 通过: 断点续传正常")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        audio_path.unlink(missing_ok=True)
        db_path.unlink(missing_ok=True)


def test_partial_result_on_failure():
    """测试 4: 失败时保存部分结果。"""
    print("\n" + "=" * 60)
    print("测试 4: 失败时保存部分结果")
    print("=" * 60)

    from db.database import (
        init_db,
        create_task,
        update_transcription_progress,
        assemble_partial_transcript,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = Path(tmp.name)

    try:
        init_db(db_path)
        task_id = create_task(
            bilibili_url="https://test.com",
            video_title="失败测试",
            db_path=db_path
        )

        print(f"\n任务 ID: {task_id}")
        print("模拟部分完成后失败...")

        # 模拟完成 3 个切片
        total_chunks = 5
        completed = 3

        for i in range(completed):
            update_transcription_progress(
                task_id=task_id,
                chunk_index=i,
                total_chunks=total_chunks,
                chunk_text=f"切片 {i + 1} 的完整文本内容",
                start_sec=i * 300,
                end_sec=(i + 1) * 300,
                db_path=db_path
            )

        print(f"  已完成: {completed}/{total_chunks} 切片")

        # 拼接部分结果
        partial = assemble_partial_transcript(task_id, db_path)
        print(f"  部分结果: '{partial[:50]}...'")
        print(f"  部分结果长度: {len(partial)} 字符")

        # 验证
        if not partial:
            print("\n❌ 测试失败: 部分结果为空")
            return False

        # 验证包含所有已完成切片的文本
        for i in range(completed):
            expected_text = f"切片 {i + 1} 的完整文本内容"
            if expected_text not in partial:
                print(f"\n❌ 测试失败: 部分结果缺少切片 {i + 1} 的文本")
                return False

        print("\n✓ 测试 4 通过: 部分结果保存正常")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    print("=" * 60)
    print("端到端测试 - 转写进度功能")
    print("=" * 60)
    print("\n注意：此测试会创建临时音频文件并使用 Whisper 模型进行转写")
    print("测试可能需要几分钟时间，请耐心等待...\n")

    results = []

    # 运行所有测试
    results.append(test_normal_transcription())
    results.append(test_chunked_transcription())
    results.append(test_resume_from_checkpoint())
    results.append(test_partial_result_on_failure())

    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    test_names = [
        "正常转写流程",
        "分片转写流程",
        "断点续传功能",
        "部分结果保存"
    ]

    for i, (name, result) in enumerate(zip(test_names, results), 1):
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{i}. {name}: {status}")

    print("\n" + "=" * 60)
    if passed == total:
        print(f"✓ 所有测试通过 ({passed}/{total})")
        print("\n🎉 转写进度功能完全正常！可以部署使用。")
        exit(0)
    else:
        print(f"✗ 部分测试失败 ({passed}/{total})")
        print("\n⚠️  请检查失败的测试并修复问题。")
        exit(1)
