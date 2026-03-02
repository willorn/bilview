"""
Streamlit 前端：负责输入、状态提示、历史记录与结果展示。

功能流程：
1. 输入 B 站链接，点击“开始处理”。
2. 按序调用下载 → 转写 → 总结，过程中实时更新状态与数据库。
3. 左侧历史记录，可查看此前任务的转录与总结，并可下载文本。
"""
from __future__ import annotations

import html
import threading
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st
import streamlit.components.v1 as components
from yt_dlp import YoutubeDL

from utils.network import get_lan_addresses
from utils.url_helper import process_user_input
from config import DB_AUTO_INIT_ON_STARTUP, DEFAULT_LLM_MODEL, DOWNLOAD_DIR, ensure_api_key_present
from core.downloader import download_audio
from core.summarizer import generate_summary
from core.transcriber import audio_to_text
from db.database import (
    DEFAULT_DB_PATH,
    Task,
    TaskStatus,
    assemble_partial_transcript,
    create_task,
    delete_tasks_before,
    delete_tasks_by_status,
    get_task,
    get_transcription_progress,
    init_db,
    list_tasks,
    update_task_content,
    update_task_status,
    update_transcription_progress,
)
from utils.copy_button import create_copy_button_with_tooltip, create_task_copy_button
from utils.file_helper import ensure_dir

STATUS_MAP = {
    TaskStatus.WAITING.value: "等待中",
    TaskStatus.DOWNLOADING.value: "下载中",
    TaskStatus.TRANSCRIBING.value: "转录中",
    TaskStatus.SUMMARIZING.value: "总结中",
    TaskStatus.COMPLETED.value: "已完成",
    TaskStatus.FAILED.value: "失败",
}

SUMMARY_MODEL_OPTIONS = [
    ("默认（config.py）", DEFAULT_LLM_MODEL),
    ("gpt-5.2-high", "gpt-5.2-high"),
    ("gemini-3-flash-preview", "gemini-3-flash-preview"),
]
REGEN_FEEDBACK_SESSION_KEY = "regen_feedback"
REGEN_ACTION_DEBOUNCE_SECONDS = 1.2


def main() -> None:
    st.set_page_config(
        page_title="B站音频转写助手",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    try:
        ensure_api_key_present()
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        return
    if "db_initialized" not in st.session_state:
        st.session_state.db_initialized = False
    if not st.session_state.db_initialized and DB_AUTO_INIT_ON_STARTUP:
        _initialize_database(show_feedback=False)
    ensure_dir(DOWNLOAD_DIR)

    if "running_task_id" not in st.session_state:
        st.session_state.running_task_id = None

    title_col, tools_col = st.columns([6, 2], vertical_alignment="top")
    with title_col:
        st.title("Bilibili Video Transcription and Summary")
        st.caption("输入 B 站链接，一键完成下载、转写、总结。")
    with tools_col:
        _render_top_actions()

    is_processing = st.session_state.running_task_id is not None
    _inject_start_button_loading_style(is_processing)

    col_input, col_action = st.columns([4, 1], vertical_alignment="bottom")
    with col_input:
        user_input = st.text_input(
            "B 站视频链接",
            placeholder="支持：https://b23.tv/xxxx 或【标题】https://b23.tv/xxxx",
        )
    with col_action:
        run_btn = st.button(
            "处理中..." if is_processing else "开始处理",
            type="primary",
            use_container_width=True,
            key="start_process_btn",
            disabled=not user_input or is_processing,
        )

    if run_btn and user_input:
        if not st.session_state.db_initialized and not _probe_database_ready():
            st.error("数据库尚未初始化。请在右上角“⚙️ -> 数据库维护”中手动初始化。")
            st.toast("❌ 数据库尚未初始化")
        else:
            # 提取并清洗 URL
            url = process_user_input(user_input)
            if not url:
                st.error("无法识别有效的 B 站链接，请检查输入格式")
                st.toast("❌ 链接解析失败，请检查输入")
            else:
                st.session_state.running_task_id = _start_task(url, _get_active_prompt())

    if st.session_state.running_task_id is not None:
        processing_hint = _render_running_task(st.session_state.running_task_id)
        if processing_hint:
            st.caption(f"⏳ {processing_hint}")

    st.divider()
    requested_task_id = _consume_task_id_query_param()
    _render_history(default_task_id=requested_task_id)


def _start_task(url: str, system_prompt: Optional[str]) -> int:
    """创建任务并启动处理。"""
    task_id = create_task(bilibili_url=url, video_title="pending")
    try:
        worker = threading.Thread(
            target=_process_task,
            args=(task_id, url, system_prompt),
            name=f"task-worker-{task_id}",
            daemon=True,
        )
        worker.start()
    except Exception as exc:  # noqa: BLE001
        _mark_task_failed_safely(task_id, f"启动任务失败：{exc}")
        st.error(f"任务启动失败：{exc}")
    return task_id


def _process_task(task_id: int, url: str, system_prompt: Optional[str]) -> None:
    """
    后台顺序执行下载→转写→总结。

    注意：这里不依赖 Streamlit UI 状态，避免浏览器断开导致任务中断。
    """
    try:
        update_task_status(task_id, TaskStatus.DOWNLOADING.value)
        audio_path, info = download_audio(url, download_dir=DOWNLOAD_DIR, return_info=True)
        update_task_content(
            task_id,
            audio_file_path=str(audio_path),
            video_title=info.get("title") if isinstance(info, dict) else None,
            video_duration_seconds=int(info.get("duration")) if isinstance(info, dict) and info.get("duration") else None,
        )

        update_task_status(task_id, TaskStatus.TRANSCRIBING.value)

        existing_progress = get_transcription_progress(task_id)
        resume_chunks = None
        if existing_progress and existing_progress.get("completed_chunks", 0) > 0:
            resume_chunks = existing_progress["chunks"]

        def on_chunk_completed(current: int, total: int, chunk_text: str, start_sec: float, end_sec: float) -> None:
            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=chunk_text,
                start_sec=start_sec,
                end_sec=end_sec,
            )

        transcript = audio_to_text(
            audio_path,
            model_size="tiny",
            language="zh",
            progress_callback=on_chunk_completed,
            resume_from_chunks=resume_chunks,
        )
        update_task_content(task_id, transcript_text=transcript)

        update_task_status(task_id, TaskStatus.SUMMARIZING.value)
        summary = generate_summary(transcript, system_prompt=system_prompt)
        update_task_content(task_id, summary_text=summary)

        update_task_status(task_id, TaskStatus.COMPLETED.value)
    except Exception as exc:  # noqa: BLE001
        try:
            partial_transcript = assemble_partial_transcript(task_id)
            if partial_transcript:
                update_task_content(task_id, transcript_text=partial_transcript)
        except Exception:  # noqa: BLE001
            pass
        _mark_task_failed_safely(task_id, str(exc))


def _render_running_task(task_id: int) -> Optional[str]:
    try:
        task = get_task(task_id)
    except Exception:  # noqa: BLE001
        return f"正在处理任务 #{task_id}，请稍候..."

    if not task:
        st.session_state.running_task_id = None
        return None

    active_statuses = {
        TaskStatus.WAITING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.TRANSCRIBING.value,
        TaskStatus.SUMMARIZING.value,
    }
    step_hint_map = {
        TaskStatus.WAITING.value: "任务已提交，等待处理队列...",
        TaskStatus.DOWNLOADING.value: "正在提取音频...",
        TaskStatus.TRANSCRIBING.value: "正在进行语音转录...",
        TaskStatus.SUMMARIZING.value: "正在调用大模型总结...",
    }
    if task.status in active_statuses:
        return step_hint_map.get(task.status, "任务处理中...")

    st.session_state.running_task_id = None
    _notify_task_result(task)
    return None


def _render_history(default_task_id: Optional[int] = None) -> None:
    st.subheader("历史记录")
    try:
        tasks = list_tasks(limit=50, include_content=False)
        st.session_state.db_initialized = True
    except Exception as exc:  # noqa: BLE001
        _render_db_not_ready_hint(exc, button_key="init_db_from_history")
        return

    if not tasks:
        st.write("暂无记录")
        return

    options = {t.id: f"#{t.id} | {STATUS_MAP.get(t.status, t.status)} | {t.video_title or '未命名'}" for t in tasks}
    task_ids = list(options.keys())
    default_index = 0
    if default_task_id in options:
        default_index = task_ids.index(default_task_id)
    selected_id = st.selectbox(
        "选择任务查看详情",
        options=task_ids,
        index=default_index,
        format_func=lambda tid: options.get(tid, str(tid)),
    )
    try:
        task = get_task(selected_id)
    except Exception as exc:  # noqa: BLE001
        _render_db_not_ready_hint(exc, button_key="init_db_from_history_get")
        return

    if not task:
        st.warning("任务不存在")
        return

    if not task.video_title:
        if st.button("重新获取标题", use_container_width=True, type="secondary"):
            _refresh_title(task.id, task.bilibili_url)

    _render_regen_feedback(task.id)

    _inject_reading_experience_styles()
    summary_tab, transcript_tab = st.tabs(["核心总结", "完整转录"])

    with summary_tab:
        summary_header_col, summary_action_col = st.columns([5, 1], vertical_alignment="bottom")
        with summary_header_col:
            st.markdown("#### 核心总结")
        with summary_action_col:
            if task.summary_text:
                st.download_button(
                    label="下载 MD",
                    data=task.summary_text,
                    file_name=f"task_{task.id}_summary.md",
                    mime="text/markdown",
                    type="primary",
                    use_container_width=True,
                    key=f"download_summary_{task.id}",
                )

        if task.summary_text:
            st.markdown(task.summary_text)
            summary_copy_button_html = create_task_copy_button(
                task_id=task.id,
                text_to_copy=task.summary_text,
                button_text="复制总结",
            )
            components.html(summary_copy_button_html, height=90, scrolling=False)
        else:
            st.info("暂无总结内容。")

    with transcript_tab:
        transcript_header_col, transcript_action_col = st.columns([5, 1], vertical_alignment="bottom")
        with transcript_header_col:
            st.markdown("#### 完整转录")
        with transcript_action_col:
            if task.transcript_text:
                st.download_button(
                    label="下载 TXT",
                    data=task.transcript_text,
                    file_name=f"task_{task.id}_transcript.txt",
                    mime="text/plain",
                    type="primary",
                    use_container_width=True,
                    key=f"download_transcript_{task.id}",
                )

        if task.transcript_text:
            _render_transcript_reader(task.transcript_text)
            transcript_copy_button_html = create_task_copy_button(task.id, task.transcript_text)
            components.html(transcript_copy_button_html, height=90, scrolling=False)
        else:
            st.info("暂无转录文本。")

    st.caption(
        f"任务状态：{STATUS_MAP.get(task.status, task.status)}，"
        f"时长：{_format_duration(task.video_duration_seconds)}, "
        f"创建时间：{task.created_at}"
    )
    if task.status == TaskStatus.FAILED.value:
        st.warning("最近一次处理失败。若下方仍显示旧总结，说明本次重新生成未成功覆盖。")

    # 显示转写进度信息
    if task.status == TaskStatus.FAILED.value:
        progress = get_transcription_progress(task.id)
        if progress and progress.get("completed_chunks", 0) > 0:
            st.info(
                f"转写进度：已完成 {progress['completed_chunks']}/{progress['total_chunks']} 个切片"
            )
            if st.button("从断点继续转写", use_container_width=True, type="primary", key=f"retry_{task.id}"):
                _retry_transcription(task)

    if task.status in {TaskStatus.SUMMARIZING.value, TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}:
        if st.button("重新生成总结", use_container_width=True, type="primary", key=f"regen_{task.id}"):
            if _allow_action(f"open_regen_dialog_{task.id}"):
                st.session_state["show_regen_dialog"] = True
                st.session_state["regen_task_id"] = task.id
                st.session_state.setdefault("regen_model_choice", SUMMARY_MODEL_OPTIONS[0][1])
            else:
                st.toast("点击过快，请稍后再试")

    if st.session_state.get("show_regen_dialog") and st.session_state.get("regen_task_id") == task.id:
        _render_regen_dialog(task)


def _render_top_actions() -> None:
    nav_col, help_col, settings_col = st.columns([3, 1, 1], gap="small")
    with nav_col:
        if st.button("🗂️ 历史记录", use_container_width=True, key="go_history_page"):
            st.switch_page("pages/history.py")
    with help_col:
        with st.popover("?", use_container_width=True):
            st.markdown("**局域网访问地址**")
            _render_copy_address()
    with settings_col:
        with st.popover("⚙️", use_container_width=True):
            _render_settings(show_title=False)


def _inject_start_button_loading_style(is_loading: bool) -> None:
    loading_style = ""
    if is_loading:
        loading_style = """
        .st-key-start_process_btn button[kind="primary"]::after {
            content: "";
            display: inline-block;
            width: 0.85rem;
            height: 0.85rem;
            margin-left: 0.5rem;
            border: 2px solid rgba(255, 255, 255, 0.45);
            border-top-color: #ffffff;
            border-radius: 50%;
            vertical-align: middle;
            animation: start-btn-spin 0.75s linear infinite;
        }
        """

    st.markdown(
        f"""
        <style>
        @keyframes start-btn-spin {{
            to {{
                transform: rotate(360deg);
            }}
        }}
        {loading_style}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _notify_task_result(task: Task) -> None:
    toast_key = f"task_result_toast_{task.id}_{task.status}"
    if st.session_state.get(toast_key):
        return

    if task.status == TaskStatus.COMPLETED.value:
        st.toast("✅ 总结完成")
    elif task.status == TaskStatus.FAILED.value:
        st.toast("❌ 任务失败，请查看详情")
    else:
        return
    st.session_state[toast_key] = True


def _set_regen_feedback(task_id: int, level: str, message: str) -> None:
    """记录重新生成总结后的提示信息，在下次渲染详情时展示。"""
    st.session_state[REGEN_FEEDBACK_SESSION_KEY] = {
        "task_id": task_id,
        "level": level,
        "message": message,
    }


def _allow_action(action_name: str, cooldown_seconds: float = REGEN_ACTION_DEBOUNCE_SECONDS) -> bool:
    """简单防抖：限制同一动作在短时间内重复触发。"""
    state_key = f"action_debounce::{action_name}"
    now = time.monotonic()
    previous = st.session_state.get(state_key, 0.0)
    try:
        last_ts = float(previous)
    except (TypeError, ValueError):
        last_ts = 0.0

    if now - last_ts < cooldown_seconds:
        return False
    st.session_state[state_key] = now
    return True


def _render_regen_feedback(task_id: int) -> None:
    """展示并消费一次性反馈，避免用户错过失败/成功提示。"""
    payload: Any = st.session_state.get(REGEN_FEEDBACK_SESSION_KEY)
    if not isinstance(payload, dict):
        return
    if payload.get("task_id") != task_id:
        return

    message = str(payload.get("message", "")).strip()
    level = str(payload.get("level", "info"))
    if not message:
        st.session_state.pop(REGEN_FEEDBACK_SESSION_KEY, None)
        return

    if level == "success":
        st.success(message)
    elif level == "error":
        st.error(message)
    else:
        st.info(message)

    st.session_state.pop(REGEN_FEEDBACK_SESSION_KEY, None)


def _inject_reading_experience_styles() -> None:
    st.markdown(
        """
        <style>
        .transcript-reader {
            max-height: 560px;
            overflow-y: auto;
            border: 1px solid rgba(128, 128, 128, 0.35);
            border-radius: 12px;
            padding: 1rem 1.1rem;
            background: rgba(250, 250, 250, 0.45);
            scrollbar-width: thin;
            scrollbar-color: #b7bdcc transparent;
        }
        .transcript-reader pre {
            margin: 0;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 1rem;
            line-height: 1.6;
            font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
        }
        .transcript-reader::-webkit-scrollbar {
            width: 10px;
        }
        .transcript-reader::-webkit-scrollbar-thumb {
            background: #b7bdcc;
            border-radius: 999px;
        }
        .transcript-reader::-webkit-scrollbar-track {
            background: transparent;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_transcript_reader(transcript_text: str) -> None:
    safe_text = html.escape(transcript_text)
    st.markdown(
        f'<div class="transcript-reader"><pre>{safe_text}</pre></div>',
        unsafe_allow_html=True,
    )


def _render_settings(show_title: bool = True) -> None:
    if show_title:
        st.subheader("设置与清理")
    with st.expander("数据库维护", expanded=False):
        auto_init_text = "开启" if DB_AUTO_INIT_ON_STARTUP else "关闭"
        st.caption(f"启动时自动初始化：{auto_init_text}（环境变量：DB_AUTO_INIT_ON_STARTUP）")
        if st.button("手动初始化/校验数据库", use_container_width=True, key="init_db_from_settings"):
            if _initialize_database(show_feedback=True):
                st.rerun()

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


def _render_regen_dialog(task: Task) -> None:
    """弹窗选择模型后重新生成总结。"""
    model_values = [item[1] for item in SUMMARY_MODEL_OPTIONS]
    label_map = {val: label for label, val in SUMMARY_MODEL_OPTIONS}
    current = st.session_state.get("regen_model_choice", model_values[0])
    try:
        default_index = model_values.index(current)
    except ValueError:
        default_index = 0

    @st.dialog("选择总结模型")
    def _dialog() -> None:
        selected_model = st.radio(
            "选择模型",
            options=model_values,
            index=default_index,
            format_func=lambda val: f"{label_map.get(val, val)}",
            key=f"regen_model_radio_{task.id}",
        )
        st.session_state["regen_model_choice"] = selected_model
        st.caption("提示：当前模型高峰期可切换备用模型再试。")

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button("开始生成", type="primary", use_container_width=True, key=f"confirm_regen_{task.id}"):
                if _allow_action(f"confirm_regen_{task.id}"):
                    _regenerate_summary(task, model=selected_model)
                else:
                    st.info("点击过快，请稍后再试。")
        with col_cancel:
            if st.button("取消", use_container_width=True, key=f"cancel_regen_{task.id}"):
                st.session_state["show_regen_dialog"] = False
                st.session_state["regen_task_id"] = None

    _dialog()


def _initialize_database(show_feedback: bool) -> bool:
    """执行数据库初始化，并更新会话内状态。"""
    try:
        init_db()
        st.session_state.db_initialized = True
        if show_feedback:
            st.success("数据库初始化完成")
        return True
    except Exception as exc:  # noqa: BLE001
        st.session_state.db_initialized = False
        if show_feedback:
            st.error(f"数据库初始化失败：{exc}")
        return False


def _probe_database_ready() -> bool:
    """轻量探测数据库可用性，不做初始化。"""
    try:
        list_tasks(limit=1, include_content=False)
        st.session_state.db_initialized = True
        return True
    except Exception:  # noqa: BLE001
        st.session_state.db_initialized = False
        return False


def _render_db_not_ready_hint(exc: Exception, button_key: str) -> None:
    st.warning(f"数据库暂不可用：{exc}")
    if st.button("立即初始化数据库", key=button_key, type="primary", use_container_width=True):
        if _initialize_database(show_feedback=True):
            st.rerun()


def _mark_task_failed_safely(task_id: int, error_text: str) -> None:
    """尽力将任务标记为失败，避免卡在 waiting/transcribing。"""
    try:
        update_task_status(task_id, TaskStatus.FAILED.value)
    except Exception:  # noqa: BLE001
        return

    # 可选补充一条可见错误信息，避免空白失败记录。
    try:
        task = get_task(task_id)
        has_any_content = bool(task and ((task.transcript_text and task.transcript_text.strip()) or (task.summary_text and task.summary_text.strip())))
        if not has_any_content:
            update_task_content(task_id, summary_text=f"[系统] 任务异常终止：{error_text}")
    except Exception:  # noqa: BLE001
        pass


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


def _consume_task_id_query_param() -> Optional[int]:
    """读取 URL 中的 task_id 参数并转换为整数，读取后清理参数避免重复生效。"""
    raw_value: Any = st.query_params.get("task_id")
    if raw_value is None:
        return None

    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    if raw_value is None:
        return None

    try:
        task_id = int(str(raw_value))
    except (TypeError, ValueError):
        task_id = None

    try:
        del st.query_params["task_id"]
    except Exception:  # noqa: BLE001
        pass

    if task_id is None or task_id <= 0:
        return None
    return task_id


def _format_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _refresh_title(task_id: int, url: str) -> None:
    """使用 yt-dlp metadata 重新获取标题并更新任务、下拉显示。"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        title = info.get("title") if isinstance(info, dict) else None
        duration = int(info.get("duration")) if isinstance(info, dict) and info.get("duration") else None
        update_task_content(task_id, video_title=title, video_duration_seconds=duration)
        st.success("标题已刷新")
    except Exception as exc:  # noqa: BLE001
        st.error(f"获取标题失败：{exc}")


def _regenerate_summary(task: Task, model: Optional[str] = None) -> None:
    """使用已存转录重新生成总结，可指定模型。"""
    if not task.transcript_text:
        _set_regen_feedback(task.id, "error", "暂无转录文本，无法生成总结")
        st.session_state["show_regen_dialog"] = False
        st.session_state["regen_task_id"] = None
        st.rerun()
        return
    chosen_model = model or st.session_state.get("regen_model_choice") or DEFAULT_LLM_MODEL
    try:
        update_task_status(task.id, TaskStatus.SUMMARIZING.value)
        summary = generate_summary(
            task.transcript_text,
            system_prompt=_get_active_prompt(),
            model=chosen_model,
        )
        if not summary.strip():
            raise RuntimeError("模型未返回有效总结内容，请切换模型后重试。")
        update_task_content(task.id, summary_text=summary)
        update_task_status(task.id, TaskStatus.COMPLETED.value)
        _set_regen_feedback(task.id, "success", "总结已重新生成")
    except Exception as exc:  # noqa: BLE001
        try:
            update_task_status(task.id, TaskStatus.FAILED.value)
        except Exception:  # noqa: BLE001
            pass
        if not (task.summary_text and task.summary_text.strip()):
            try:
                update_task_content(task.id, summary_text=f"[系统] 重新生成失败：{exc}")
            except Exception:  # noqa: BLE001
                pass
        _set_regen_feedback(task.id, "error", f"重新生成失败：{exc}")
    finally:
        st.session_state["show_regen_dialog"] = False
        st.session_state["regen_task_id"] = None
    st.rerun()


def _retry_transcription(task: Task) -> None:
    """从断点继续转写失败的任务。"""
    if not task.audio_file_path:
        st.error("音频文件路径缺失，无法继续转写")
        return

    audio_path = Path(task.audio_file_path)
    if not audio_path.exists():
        st.error(f"音频文件不存在：{audio_path}")
        return

    with st.status("继续转写中...", expanded=True) as status_box:
        try:
            update_task_status(task.id, TaskStatus.TRANSCRIBING.value)

            # 获取断点续传数据
            existing_progress = get_transcription_progress(task.id)
            resume_chunks = None
            if existing_progress:
                resume_chunks = existing_progress["chunks"]
                status_box.info(f"从第 {existing_progress['completed_chunks'] + 1} 个切片继续...")

            # 创建进度条
            progress_container = status_box.container()
            progress_bar = progress_container.progress(0, text="准备转写...")

            # 定义进度回调
            def on_chunk_completed(current: int, total: int, chunk_text: str, start_sec: float, end_sec: float) -> None:
                update_transcription_progress(
                    task_id=task.id,
                    chunk_index=current - 1,
                    total_chunks=total,
                    chunk_text=chunk_text,
                    start_sec=start_sec,
                    end_sec=end_sec,
                )
                progress_percent = current / total
                progress_bar.progress(
                    progress_percent,
                    text=f"转写进度：{current}/{total} 切片 ({int(progress_percent * 100)}%)"
                )

            # 调用转写
            transcript = audio_to_text(
                audio_path,
                model_size="tiny",
                language="zh",
                progress_callback=on_chunk_completed,
                resume_from_chunks=resume_chunks,
            )
            update_task_content(task.id, transcript_text=transcript)

            # 继续总结
            update_task_status(task.id, TaskStatus.SUMMARIZING.value)
            status_box.write("总结中（LLM）...")
            summary = generate_summary(transcript, system_prompt=_get_active_prompt())
            update_task_content(task.id, summary_text=summary)

            update_task_status(task.id, TaskStatus.COMPLETED.value)
            status_box.update(label="处理完成", state="complete")
            st.success("转写已完成")
        except Exception as exc:  # noqa: BLE001
            # 保存部分结果
            partial_transcript = assemble_partial_transcript(task.id)
            if partial_transcript:
                update_task_content(task.id, transcript_text=partial_transcript)
                status_box.warning(f"转写部分完成（{len(partial_transcript)} 字符），但遇到错误")

            update_task_status(task.id, TaskStatus.FAILED.value)
            status_box.update(label="处理失败", state="error")
            status_box.write(f"错误：{exc}")


def _render_copy_address() -> None:
    addrs = get_lan_addresses()
    if not addrs:
        st.caption("未检测到可用局域网地址。")
        return
    port = st.session_state.get("server_port", 8501)
    options = [f"http://{addr}:{port}" for addr in addrs]
    selected = options[0]
    if len(options) > 1:
        selected = st.selectbox("可用局域网地址", options, label_visibility="collapsed")
    st.code(selected, language="text")
    copy_button_html = create_copy_button_with_tooltip(
        button_id=f"lan_address_{selected}",
        text_to_copy=selected,
        button_text="复制地址",
        button_color="#2563eb",
        button_hover_color="#1d4ed8",
        success_message="✓ 局域网地址已复制",
        error_message="✗ 复制地址失败",
    )
    components.html(copy_button_html, height=86, scrolling=False)
    st.caption("手机需与本机同一局域网；如无法访问，请检查防火墙/端口。")


_DEFAULT_PROMPT = """你是一个专业的长视频笔记助手，请将输入的完整转录文本，提炼为结构化笔记，需包含：
# Role: 认知科学教学设计师 & 温情深度学习教练


## 🎯 核心目标

你现在的任务不是简单的“总结”，而是将一份**口语化的课程语音转录稿**，转化为一份**逻辑严密、易于理解的深度学习教材**，并辅助用户完成**主动式学习（Active Learning）**。



* **用户画像**：偏好阅读文字，习惯通过“复述”和“教授他人”来检验学习成果。

* **最终标准**：用户不需要看原始视频，仅通过你的输出就能彻底学懂，并能应用。

* **交互风格**：专业严谨的整理者 + 温暖、包容、令人有安全感的学习伙伴。



---



## 📝 任务流程（请严格按步骤执行）



### 🟢 第一阶段：内容重构与深度加工（Textbook Quality）

请处理附后的输入文本，输出一份**教科书级别的学习文稿**。



**处理要求：**

1.  **清洗与修复**：去除口语废话、重复、纠正语音识别错误。补全因口语跳跃而缺失的逻辑链条。

2.  **动态结构化（关键）**：不要使用固定的总结模板。请分析内容特点，**自动选择最适合该知识点的讲解逻辑**：

    * *如果是技术原理*：采用“场景/问题 -> 核心概念 -> 运作机制 -> 优缺点”的结构。

    * *如果是操作流程*：采用“前置条件 -> 步骤分解 -> 关键注意事项”的结构。

    * *如果是概念辨析*：采用“定义对比 -> 核心差异 -> 误区澄清”的结构。

3.  **保留精华**：**严禁**删减老师举的**具体例子、比喻和应用场景**（这些是理解的关键），必须完整保留并优化表达。

4.  **可视化辅助**：在关键逻辑处，使用 Mermaid 伪代码或 ASCII 流程图/思维导图（文本形式）来展示结构。



### 🟡 第二阶段：认知支架搭建（Cognitive Scaffolding）

在正文之后，提供以下辅助模块以降低认知负荷：

1.  **ELI5 (Explain Like I'm 5)**：用最通俗的语言，一句话概括这节课解决了什么核心问题。

2.  **易混淆点/陷阱预警**：指出初学者最容易误解的地方，并给出正确视角。

3.  **核心概念关系图**：用列表或缩进结构，展示核心概念之间的层级或因果关系。



### 🔴 第三阶段：主动式学习挑战（Interaction Loop）

**这是最重要的部分。请不要直接给出答案，而是生成 3 个深度思考任务。**



**任务设计原则（必须包含）：**

1.  **费曼复述题**：“请用你自己的话，向一个完全不懂[某核心概念]的人解释它。”

2.  **迁移应用题**：设定一个新的具体场景，询问用户如何利用本课知识解决问题。

3.  **批判性思考/对比题**：询问“为什么选择 A 方案而不是 B 方案？”或“这个知识点在什么情况下会失效？”



---



## 🛑 结束语策略（关键：情感连接与鼓励）



**在列出题目后，请停止输出，不要提供答案。**

**最后，请放弃机械的指令（如“请回答”），改用“温和、支持性的导师”语调，随机选择或组合以下一种风格作为结束语，鼓励用户开口：**



* **风格 A（降低门槛型）**：强调“草稿思维”。

    * *话术示例*：“不用担心措辞严谨，哪怕只是几个关键词，或者大白话试着说一下，对理解都非常有帮助。试试看？”

* **风格 B（好奇伙伴型）**：表现出对用户观点的真实兴趣。

    * *话术示例*：“关于这一点，我很好奇你会怎么理解？我很想听听你的看法。”

* **风格 C（成长心态型）**：强调输出的价值。

    * *话术示例*：“看懂是第一步，讲出来才是真正属于你的时刻。挑一个你最有感觉的问题，或者随便聊聊你的启发？”

* **风格 D（角色扮演型）**：

    * *话术示例*：“现在我是你的学生，请苏格拉底老师教教我，这个概念到底该怎么懂？”



**✅ 目标：让人感到放松、被支持，觉得“说错也没关系”，从而愿意尝试输入。**



---



## 👇 请输入语音转录文本："""


if __name__ == "__main__":
    main()
