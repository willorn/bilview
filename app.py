"""
Streamlit 前端：负责输入、状态提示、历史记录与结果展示。

功能流程：
1. 输入 B 站链接，点击“开始处理”。
2. 按序调用下载 → 转写 → 总结，过程中实时更新状态与数据库。
3. 左侧历史记录，可查看此前任务的转录与总结，并可下载文本。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import streamlit as st

from config import DOWNLOAD_DIR
from core.downloader import download_audio
from core.summarizer import generate_summary
from core.transcriber import audio_to_text
from db.database import (
    Task,
    TaskStatus,
    create_task,
    get_task,
    init_db,
    list_tasks,
    update_task_content,
    update_task_status,
)
from utils.file_helper import ensure_dir

STATUS_MAP = {
    TaskStatus.WAITING.value: "等待中",
    TaskStatus.DOWNLOADING.value: "下载中",
    TaskStatus.TRANSCRIBING.value: "转录中",
    TaskStatus.SUMMARIZING.value: "总结中",
    TaskStatus.COMPLETED.value: "已完成",
    TaskStatus.FAILED.value: "失败",
}


def main() -> None:
    st.set_page_config(page_title="B站音频转写助手", layout="wide")
    init_db()
    ensure_dir(DOWNLOAD_DIR)

    if "running_task_id" not in st.session_state:
        st.session_state.running_task_id = None

    st.title("Bilibili 视频转录与总结")
    st.caption("输入 B 站链接，一键完成下载、转写、总结。")

    col_input, col_action = st.columns([4, 1])
    with col_input:
        url = st.text_input("B 站视频链接", placeholder="https://b23.tv/xxxx 或 https://www.bilibili.com/video/BV...")
    with col_action:
        run_btn = st.button(
            "开始处理",
            type="primary",
            use_container_width=True,
            disabled=not url or st.session_state.running_task_id is not None,
        )

    if run_btn and url:
        st.session_state.running_task_id = _start_task(url)

    if st.session_state.running_task_id is not None:
        _render_running_task(st.session_state.running_task_id)

    st.divider()
    _render_history()


def _start_task(url: str) -> int:
    """创建任务并启动处理。"""
    task_id = create_task(bilibili_url=url, video_title="pending")
    _process_task(task_id, url)
    return task_id


def _process_task(task_id: int, url: str) -> None:
    """顺序执行下载→转写→总结，异常自动标记失败。"""
    with st.status("处理中...", expanded=True) as status_box:
        try:
            update_task_status(task_id, TaskStatus.DOWNLOADING.value)
            status_box.write("下载音频中...")
            audio_path = download_audio(url, download_dir=DOWNLOAD_DIR)
            update_task_content(task_id, audio_file_path=str(audio_path))

            update_task_status(task_id, TaskStatus.TRANSCRIBING.value)
            status_box.write("转写中（Whisper）...")
            transcript = audio_to_text(audio_path, model_size="tiny", language="zh")
            update_task_content(task_id, transcript_text=transcript)

            update_task_status(task_id, TaskStatus.SUMMARIZING.value)
            status_box.write("总结中（LLM）...")
            summary = generate_summary(transcript)
            update_task_content(task_id, summary_text=summary)

            update_task_status(task_id, TaskStatus.COMPLETED.value)
            status_box.update(label="处理完成", state="complete")
        except Exception as exc:  # noqa: BLE001
            update_task_status(task_id, TaskStatus.FAILED.value)
            status_box.update(label="处理失败", state="error")
            status_box.write(f"错误：{exc}")
        finally:
            st.session_state.running_task_id = None


def _render_running_task(task_id: int) -> None:
    st.info(f"正在处理任务 #{task_id}，请稍候...")


def _render_history() -> None:
    st.subheader("历史记录")
    tasks = list_tasks(limit=50)
    if not tasks:
        st.write("暂无记录")
        return

    options = {f"#{t.id} | {STATUS_MAP.get(t.status, t.status)} | {t.bilibili_url}": t.id for t in tasks}
    selected_label = st.selectbox("选择任务查看详情", list(options.keys()))
    selected_id = options[selected_label]
    task = get_task(selected_id)
    if not task:
        st.warning("任务不存在")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("**转录文本**")
        st.text_area("transcript", value=task.transcript_text or "", height=400, label_visibility="collapsed")
        if task.transcript_text:
            st.download_button(
                "下载逐字稿 (.txt)",
                data=(task.transcript_text or "").encode("utf-8"),
                file_name=f"task_{task.id}_transcript.txt",
                mime="text/plain",
            )
    with right:
        st.markdown("**总结结果**")
        st.text_area("summary", value=task.summary_text or "", height=400, label_visibility="collapsed")
        if task.summary_text:
            st.download_button(
                "下载总结 (.md)",
                data=(task.summary_text or "").encode("utf-8"),
                file_name=f"task_{task.id}_summary.md",
                mime="text/markdown",
            )

    st.caption(
        f"任务状态：{STATUS_MAP.get(task.status, task.status)}，创建时间：{task.created_at}"
    )


if __name__ == "__main__":
    main()
