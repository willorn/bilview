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

from utils.network import get_lan_addresses
from config import DOWNLOAD_DIR
from core.downloader import download_audio
from core.summarizer import generate_summary
from core.transcriber import audio_to_text
from db.database import (
    Task,
    TaskStatus,
    create_task,
    delete_tasks_before,
    delete_tasks_by_status,
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

    _render_copy_address()

    col_input, col_action = st.columns([4, 1], vertical_alignment="bottom")
    with col_input:
        url = st.text_input(
            "B 站视频链接",
            placeholder="https://b23.tv/xxxx 或 https://www.bilibili.com/video/BV...",
        )
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

    settings_col, history_col = st.columns([1.2, 2])
    with settings_col:
        _render_settings()
    with history_col:
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
            audio_path, info = download_audio(url, download_dir=DOWNLOAD_DIR, return_info=True)
            update_task_content(
                task_id,
                audio_file_path=str(audio_path),
                video_title=info.get("title") if isinstance(info, dict) else None,
                video_duration_seconds=int(info.get("duration")) if isinstance(info, dict) and info.get("duration") else None,
            )

            update_task_status(task_id, TaskStatus.TRANSCRIBING.value)
            status_box.write("转写中（Whisper）...")
            transcript = audio_to_text(audio_path, model_size="tiny", language="zh")
            update_task_content(task_id, transcript_text=transcript)

            update_task_status(task_id, TaskStatus.SUMMARIZING.value)
            status_box.write("总结中（LLM）...")
            summary = generate_summary(transcript, system_prompt=_get_active_prompt())
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
        f"任务状态：{STATUS_MAP.get(task.status, task.status)}，"
        f"时长：{_format_duration(task.video_duration_seconds)}, "
        f"创建时间：{task.created_at}"
    )


def _render_settings() -> None:
    st.subheader("设置与清理")
    with st.expander("总结 Prompt", expanded=False):
        default_prompt = _DEFAULT_PROMPT
        user_prompt = st.text_area(
            "自定义 System Prompt（留空则使用默认）",
            value=st.session_state.get("custom_prompt", ""),
            height=200,
            placeholder=default_prompt[:120] + "...",
        )
        if st.button("保存 Prompt", use_container_width=True):
            st.session_state.custom_prompt = user_prompt.strip()
            st.success("已更新 Prompt（本次会话生效）")
        st.caption("提示：为空则自动使用内置默认提示。")

    with st.expander("历史记录清理", expanded=False):
        days = st.number_input("删除早于 N 天的任务", min_value=0, max_value=3650, value=0, step=1)
        status_choices = st.multiselect(
            "按状态删除", options=list(STATUS_MAP.keys()), format_func=lambda x: STATUS_MAP.get(x, x)
        )
        delete_files = st.checkbox("同时删除对应音频文件", value=True)
        confirm = st.checkbox("我已知晓删除不可恢复", value=False)
        if st.button("执行清理", type="primary", use_container_width=True, disabled=not confirm):
            removed_rows = 0
            removed_files = 0
            if days > 0:
                removed_rows += delete_tasks_before(days)
            if status_choices:
                removed_rows += delete_tasks_by_status(status_choices)
            if delete_files:
                removed_files = _cleanup_files()
            st.success(f"清理完成：删除记录 {removed_rows} 条，删除音频文件 {removed_files} 个。")
        st.caption("提示：days=0 表示不按时间删除；状态未选则跳过状态清理。")


def _cleanup_files() -> int:
    """删除 downloads 目录下的音频文件，返回删除数量。"""
    count = 0
    for path in Path(DOWNLOAD_DIR).glob("*"):
        if path.is_file():
            try:
                path.unlink()
                count += 1
            except Exception:
                continue
    return count


def _get_active_prompt() -> Optional[str]:
    prompt = st.session_state.get("custom_prompt")
    return prompt if prompt else None


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _render_copy_address() -> None:
    addrs = get_lan_addresses()
    if not addrs:
        return
    port = st.session_state.get("server_port", 8501)
    options = [f"http://{addr}:{port}" for addr in addrs]
    selected = options[0]
    if len(options) > 1:
        selected = st.selectbox("可用局域网地址", options, label_visibility="collapsed")
    st.code(selected, language="text")
    st.caption("提示：手机需与本机同一局域网；如无法访问，请检查防火墙/端口。")


_DEFAULT_PROMPT = """你是一个专业的长视频笔记助手，请将输入的完整转录文本，提炼为结构化笔记，需包含：
1) 内容摘要：3-5 条
2) 核心亮点/金句：2-4 条
3) 结论与行动建议：2-3 条
要求：用中文输出；保持事实准确，不臆测；必要时保留数字、公式或关键引用。"""


if __name__ == "__main__":
    main()
