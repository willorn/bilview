"""
Streamlit 前端：负责输入、状态提示、历史记录与结果展示。

功能流程：
1. 输入 B 站链接，点击“开始处理”。
2. 按序调用下载 → 转写 → 总结，过程中实时更新状态与数据库。
3. 左侧历史记录，可查看此前任务的转录与总结，并可下载文本。
"""
from __future__ import annotations

# Python 3.13 移除了 audioop，pydub 依赖不存在的 pyaudioop。
# 在所有其他导入之前抢先注册本地兼容层。
import sys
from types import ModuleType
_pyaudioop = ModuleType("pyaudioop")

def _rms(audio_data: bytes, width: int) -> float:
    import struct
    if not audio_data:
        return 0.0
    if width == 1:
        samples = [s - 128 for s in struct.unpack(f"{len(audio_data)}B", audio_data)]
    elif width == 2:
        samples = struct.unpack(f"<{len(audio_data)//2}h", audio_data)
    elif width == 3:
        total = 0.0
        n = len(audio_data) // 3
        for i in range(n):
            b0, b1, b2 = audio_data[i*3], audio_data[i*3+1], audio_data[i*3+2]
            val = b0 | (b1 << 8) | ((b2 << 24) >> 8)
            total += val * val
        return (total / n) ** 0.5 if n else 0.0
    elif width == 4:
        samples = struct.unpack(f"<{len(audio_data)//4}i", audio_data)
    else:
        return 0.0
    total = sum(s * s for s in samples)
    return (total / len(samples)) ** 0.5 if samples else 0.0

_pyaudioop.rms = _rms
sys.modules["pyaudioop"] = _pyaudioop

import html
import logging
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

import streamlit as st
import streamlit.components.v1 as components
from yt_dlp import YoutubeDL

import config as app_config
from utils.network import get_lan_addresses
from utils.url_helper import process_user_input
from core.downloader import download_audio
from core.punctuator import punctuate_transcript
from core import summarizer as summarizer_module
from core.downloader import (
    has_bilibili_cookies,
    COOKIE_FILE,
    generate_bilibili_qr,
    check_bilibili_login_status,
    get_cookie_receive_url,
)
from core.transcriber import audio_to_text
from db.database import (
    DEFAULT_DB_PATH,
    Task,
    TaskStatus,
    assemble_partial_transcript,
    clear_task_cancel_request,
    clear_task_error,
    claim_next_waiting_task,
    create_task,
    delete_tasks_before,
    delete_tasks_by_status,
    get_task_error_info,
    get_task,
    get_task_raw_transcript,
    get_task_summary,
    get_task_transcript,
    get_transcription_progress,
    init_db,
    is_task_cancel_requested,
    list_tasks,
    request_task_cancel,
    recover_interrupted_tasks,
    reset_transcription_data,
    update_task_content,
    update_task_error,
    update_task_status,
    update_transcription_progress,
)
from utils.copy_button import create_copy_button_with_tooltip, create_task_copy_button
from utils.file_helper import ensure_dir

DB_AUTO_INIT_ON_STARTUP = bool(getattr(app_config, "DB_AUTO_INIT_ON_STARTUP", False))
DEFAULT_GROQ_ASR_MODEL = (
    str(getattr(app_config, "DEFAULT_GROQ_ASR_MODEL", "whisper-large-v3-turbo")).strip()
    or "whisper-large-v3-turbo"
)
DEFAULT_LLM_MODEL = (
    str(getattr(app_config, "DEFAULT_LLM_MODEL", "gemini-2.5-pro-1m")).strip()
    or "gemini-2.5-pro-1m"
)
_download_dir_value = getattr(app_config, "DOWNLOAD_DIR", Path("downloads")) or Path("downloads")
DOWNLOAD_DIR = Path(_download_dir_value).expanduser().resolve()
_config_ensure_api_key = getattr(app_config, "ensure_api_key_present", None)


def _noop_ensure_api_key_present() -> None:
    return None


ensure_api_key_present: Callable[[], None] = (
    _config_ensure_api_key if callable(_config_ensure_api_key) else _noop_ensure_api_key
)

STATUS_MAP = {
    TaskStatus.WAITING.value: "等待中",
    TaskStatus.DOWNLOADING.value: "下载中",
    TaskStatus.TRANSCRIBING.value: "转录中",
    TaskStatus.SUMMARIZING.value: "总结中",
    TaskStatus.TIMEOUT.value: "已超时",
    TaskStatus.CANCELLED.value: "已取消",
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
REGEN_RUNNING_TASK_SESSION_KEY = "regen_running_task_id"
TASK_TEXT_CACHE_SESSION_KEY = "task_text_cache"
DB_SCHEMA_READY_SESSION_KEY = "db_schema_ready"
DB_SCHEMA_ERROR_SESSION_KEY = "db_schema_error"
TRANSCRIBE_API_MODEL = DEFAULT_GROQ_ASR_MODEL
TRANSCRIBE_TEXT_PROMPT = (
    "请输出简体中文逐字稿，并尽量补全自然中文标点符号；"
    "不要添加任何解释或额外内容。"
)
LOGGER = logging.getLogger(__name__)
_TASK_PROMPT_SNAPSHOTS: dict[int, Optional[str]] = {}
_TASK_PROMPT_SNAPSHOTS_LOCK = threading.Lock()
_TASK_TIMEOUT_REQUESTS: set[int] = set()
_TASK_TIMEOUT_REQUESTS_LOCK = threading.Lock()


def _read_positive_int_config(name: str, default: int) -> int:
    raw_value = getattr(app_config, name, default)
    try:
        return max(int(raw_value), 1)
    except (TypeError, ValueError):
        return max(int(default), 1)


def _read_non_negative_int_config(name: str, default: int) -> int:
    raw_value = getattr(app_config, name, default)
    try:
        return max(int(raw_value), 0)
    except (TypeError, ValueError):
        return max(int(default), 0)


def _read_positive_float_config(name: str, default: float) -> float:
    raw_value = getattr(app_config, name, default)
    try:
        return max(float(raw_value), 0.2)
    except (TypeError, ValueError):
        return max(float(default), 0.2)


TASK_EXECUTOR_MAX_WORKERS = _read_positive_int_config("TASK_EXECUTOR_MAX_WORKERS", 1)
TASK_EXECUTOR_POLL_INTERVAL_SECONDS = _read_positive_float_config("TASK_EXECUTOR_POLL_INTERVAL_SECONDS", 1.0)
TASK_EXECUTOR_TASK_TIMEOUT_SECONDS = _read_positive_float_config("TASK_EXECUTOR_TASK_TIMEOUT_SECONDS", 5400.0)
TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS = _read_non_negative_int_config(
    "TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS",
    1,
)


def _remember_task_prompt(task_id: int, prompt: Optional[str]) -> None:
    with _TASK_PROMPT_SNAPSHOTS_LOCK:
        _TASK_PROMPT_SNAPSHOTS[int(task_id)] = prompt


def _take_task_prompt(task_id: int) -> Optional[str]:
    with _TASK_PROMPT_SNAPSHOTS_LOCK:
        return _TASK_PROMPT_SNAPSHOTS.pop(int(task_id), None)


def _extract_error_code(error_text: str) -> str:
    """从异常文本中提取机器可读错误码。"""
    normalized = (error_text or "").strip()
    if not normalized:
        return "UNKNOWN"
    lowered = normalized.lower()
    if "超时" in normalized or "timeout" in lowered:
        return "TASK_TIMEOUT"

    http_match = re.search(r"\bHTTP\s+(\d{3})\b", normalized, flags=re.IGNORECASE)
    if http_match:
        return f"HTTP_{http_match.group(1)}"

    status_match = re.search(r"\b(\d{3})\b", normalized)
    if status_match and status_match.group(1) in {"401", "403", "404", "408", "409", "413", "429", "500", "502", "503", "504"}:
        return f"HTTP_{status_match.group(1)}"

    return "RUNTIME_ERROR"


def _record_task_error(task_id: int, stage: str, error_text: str) -> None:
    """将失败信息写入结构化错误字段。"""
    try:
        update_task_error(
            task_id,
            error_stage=stage,
            error_code=_extract_error_code(error_text),
            error_message=error_text,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("写入任务结构化错误失败(task=%s)：%s", task_id, exc)


def _clear_task_error(task_id: int) -> None:
    """清理任务错误信息，避免旧错误误导。"""
    try:
        clear_task_error(task_id)
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("清理任务结构化错误失败(task=%s)：%s", task_id, exc)


class TaskCancelledError(RuntimeError):
    """任务被用户取消。"""

    def __init__(self, message: str, reason: str = "user") -> None:
        super().__init__(message)
        self.reason = reason


def _mark_task_timeout_requested(task_id: int) -> None:
    with _TASK_TIMEOUT_REQUESTS_LOCK:
        _TASK_TIMEOUT_REQUESTS.add(int(task_id))


def _is_task_timeout_requested(task_id: int) -> bool:
    with _TASK_TIMEOUT_REQUESTS_LOCK:
        return int(task_id) in _TASK_TIMEOUT_REQUESTS


def _consume_task_timeout_requested(task_id: int) -> bool:
    with _TASK_TIMEOUT_REQUESTS_LOCK:
        key = int(task_id)
        if key in _TASK_TIMEOUT_REQUESTS:
            _TASK_TIMEOUT_REQUESTS.remove(key)
            return True
        return False


def _clear_task_timeout_requested(task_id: int) -> None:
    with _TASK_TIMEOUT_REQUESTS_LOCK:
        _TASK_TIMEOUT_REQUESTS.discard(int(task_id))


def _record_task_cancelled(task_id: int) -> None:
    """记录任务取消信息。"""
    try:
        update_task_error(
            task_id,
            error_stage="cancelled",
            error_code="USER_CANCELLED",
            error_message="用户主动取消任务",
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("写入任务取消信息失败(task=%s)：%s", task_id, exc)


def _raise_if_task_cancel_requested(task_id: int) -> None:
    if is_task_cancel_requested(task_id):
        if _is_task_timeout_requested(task_id):
            raise TaskCancelledError("任务执行超时，已停止", reason="timeout")
        raise TaskCancelledError("任务已被用户取消", reason="user")


class _PersistentTaskExecutor:
    """基于数据库 waiting 状态的持久化任务执行器。"""

    def __init__(
        self,
        max_workers: int,
        poll_interval_seconds: float,
        task_timeout_seconds: float,
        timeout_overflow_workers: int,
    ) -> None:
        self._max_workers = max(1, int(max_workers))
        self._poll_interval_seconds = max(float(poll_interval_seconds), 0.2)
        self._task_timeout_seconds = max(float(task_timeout_seconds), 30.0)
        self._timeout_overflow_workers = max(int(timeout_overflow_workers), 0)
        self._pool_generation = 1
        self._pool = self._build_thread_pool()
        self._futures: dict[Future[Any], tuple[int, float]] = {}
        self._detached_futures: dict[Future[Any], int] = {}
        self._futures_lock = threading.Lock()
        self._dispatch_thread: Optional[threading.Thread] = None
        self._dispatch_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._recovered_once = False

    def _build_thread_pool(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix=f"task-worker-g{self._pool_generation}",
        )

    def start(self) -> None:
        with self._dispatch_lock:
            if self._dispatch_thread and self._dispatch_thread.is_alive():
                return

            if not self._recovered_once:
                self._recover_interrupted_tasks_once()
                self._recovered_once = True

            self._stop_event.clear()
            self._dispatch_thread = threading.Thread(
                target=self._dispatch_loop,
                name="task-dispatcher",
                daemon=True,
            )
            self._dispatch_thread.start()

    def notify_new_task(self) -> None:
        self.start()
        self._wake_event.set()

    def _recover_interrupted_tasks_once(self) -> None:
        try:
            recovered_count = recover_interrupted_tasks()
            if recovered_count > 0:
                LOGGER.info("任务执行器已接管 %s 个中断任务。", recovered_count)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("任务执行器恢复中断任务失败：%s", exc)

    def _dispatch_loop(self) -> None:
        while not self._stop_event.is_set():
            self._prune_done_futures()
            self._enforce_task_timeouts()
            scheduled = self._schedule_waiting_tasks()
            if scheduled:
                continue
            self._wake_event.wait(timeout=self._poll_interval_seconds)
            self._wake_event.clear()

    def _schedule_waiting_tasks(self) -> bool:
        has_scheduled = False
        while self._has_available_worker_slot():
            try:
                queue_item = claim_next_waiting_task()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("认领 waiting 任务失败：%s", exc)
                return has_scheduled

            if not queue_item:
                return has_scheduled

            has_scheduled = True
            prompt_snapshot = _take_task_prompt(queue_item.id)
            future = self._pool.submit(
                _process_task,
                queue_item.id,
                queue_item.bilibili_url,
                prompt_snapshot,
            )
            with self._futures_lock:
                self._futures[future] = (queue_item.id, time.monotonic())
        return has_scheduled

    def _prune_done_futures(self) -> None:
        with self._futures_lock:
            done_futures = [future for future in self._futures if future.done()]
            for future in done_futures:
                task_id, _ = self._futures.pop(future)
                _consume_task_timeout_requested(task_id)

            done_detached_futures = [
                future for future in self._detached_futures if future.done()
            ]
            for future in done_detached_futures:
                task_id = self._detached_futures.pop(future)
                _consume_task_timeout_requested(task_id)

    def _has_available_worker_slot(self) -> bool:
        with self._futures_lock:
            detached_count = len(self._detached_futures)
            active_limit = max(
                self._max_workers + self._timeout_overflow_workers - detached_count,
                0,
            )
            return len(self._futures) < active_limit

    def _enforce_task_timeouts(self) -> None:
        timed_out_futures: list[tuple[Future[Any], int, float]] = []
        with self._futures_lock:
            for future, (task_id, start_ts) in self._futures.items():
                elapsed = time.monotonic() - start_ts
                if elapsed > self._task_timeout_seconds and not _is_task_timeout_requested(task_id):
                    timed_out_futures.append((future, task_id, elapsed))

        detached_task_ids: list[int] = []
        for future, task_id, elapsed in timed_out_futures:
            try:
                should_stop = request_task_cancel(task_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("触发超时停止失败(task=%s): %s", task_id, exc)
                continue
            if not should_stop:
                continue

            _mark_task_timeout_requested(task_id)
            try:
                update_task_status(task_id, TaskStatus.TIMEOUT.value)
            except Exception:  # noqa: BLE001
                pass
            timeout_msg = (
                f"任务执行超过 {int(self._task_timeout_seconds)} 秒，"
                f"已触发超时停止（已运行 {int(elapsed)} 秒）"
            )
            _record_task_error(task_id, stage="watchdog", error_text=timeout_msg)
            LOGGER.warning("任务超时已触发停止(task=%s, elapsed=%ss)", task_id, int(elapsed))
            if self._detach_timed_out_future(future, task_id):
                detached_task_ids.append(task_id)

        if detached_task_ids:
            self._rotate_pool_for_timeouts(detached_task_ids)

    def _detach_timed_out_future(self, future: Future[Any], task_id: int) -> bool:
        """将超时任务从调度计数中摘除，避免阻塞后续排队任务。"""
        with self._futures_lock:
            if future not in self._futures:
                return False
            self._futures.pop(future, None)
            self._detached_futures[future] = int(task_id)
            return True

    def _rotate_pool_for_timeouts(self, task_ids: list[int]) -> None:
        """重建线程池，避免被已超时的阻塞线程长期占满。"""
        old_pool = self._pool
        self._pool_generation += 1
        self._pool = self._build_thread_pool()
        LOGGER.warning(
            "检测到超时任务 %s，执行器已切换到线程池代际 g%s。",
            task_ids,
            self._pool_generation,
        )
        old_pool.shutdown(wait=False, cancel_futures=True)


@st.cache_resource(show_spinner=False)
def _get_task_executor() -> _PersistentTaskExecutor:
    executor = _PersistentTaskExecutor(
        max_workers=TASK_EXECUTOR_MAX_WORKERS,
        poll_interval_seconds=TASK_EXECUTOR_POLL_INTERVAL_SECONDS,
        task_timeout_seconds=TASK_EXECUTOR_TASK_TIMEOUT_SECONDS,
        timeout_overflow_workers=TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS,
    )
    executor.start()
    return executor


def _load_default_prompt() -> str:
    getter = getattr(summarizer_module, "get_default_system_prompt", None)
    if callable(getter):
        return getter()
    try:
        prompt_path = Path(__file__).resolve().parent / "docs" / "default_prompt.md"
        return prompt_path.read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""


_DEFAULT_PROMPT = _load_default_prompt()


def _build_readable_transcript(raw_transcript: str) -> str:
    """基于原始转录生成阅读版文本（自动补标点，不改字词）。"""
    if not raw_transcript:
        return ""
    return punctuate_transcript(raw_transcript)


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
    if DB_SCHEMA_READY_SESSION_KEY not in st.session_state:
        st.session_state[DB_SCHEMA_READY_SESSION_KEY] = False
    if DB_SCHEMA_ERROR_SESSION_KEY not in st.session_state:
        st.session_state[DB_SCHEMA_ERROR_SESSION_KEY] = ""
    if not bool(st.session_state.get(DB_SCHEMA_READY_SESSION_KEY)):
        _ensure_database_schema_ready(show_feedback=False)
    ensure_dir(DOWNLOAD_DIR)
    _get_task_executor().start()

    if "running_task_id" not in st.session_state:
        st.session_state.running_task_id = None

    _auto_refresh_fragment()

    title_col, tools_col = st.columns([6, 2], vertical_alignment="top")
    with title_col:
        st.title("Bilibili Video Transcription and Summary")
        st.caption("输入 B 站链接，一键完成下载、转写、总结。")
    with tools_col:
        _render_top_actions()

    is_processing = st.session_state.running_task_id is not None
    schema_ready = bool(st.session_state.get(DB_SCHEMA_READY_SESSION_KEY))
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
            disabled=not user_input or is_processing or not schema_ready,
        )

    if not schema_ready:
        schema_error = str(st.session_state.get(DB_SCHEMA_ERROR_SESSION_KEY, "")).strip()
        detail_text = f"详情：{schema_error}" if schema_error else "请稍后重试。"
        st.error(f"数据库 Schema 校验失败，已禁用任务提交。{detail_text}")
        if st.button("重试 Schema 校验", type="secondary", key="retry_schema_check_main"):
            _ensure_database_schema_ready(show_feedback=True)
            st.rerun()

    if run_btn and user_input:
        if not bool(st.session_state.get(DB_SCHEMA_READY_SESSION_KEY)):
            st.error("数据库 Schema 未就绪，请先完成校验。")
            st.toast("❌ 数据库 Schema 未就绪")
            return
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
    """创建任务并交给持久化执行器排队处理。"""
    task_id = create_task(bilibili_url=url, video_title="pending")
    _remember_task_prompt(task_id, system_prompt)
    try:
        _get_task_executor().notify_new_task()
    except Exception as exc:  # noqa: BLE001
        _take_task_prompt(task_id)
        _mark_task_failed_safely(task_id, f"提交任务到执行队列失败：{exc}", stage="queue")
        st.error(f"任务提交失败：{exc}")
    return task_id


def _process_task(task_id: int, url: str, system_prompt: Optional[str]) -> None:
    """
    后台顺序执行下载→转写→总结。

    注意：这里不依赖 Streamlit UI 状态，避免浏览器断开导致任务中断。
    """
    current_stage = "downloading"
    try:
        _clear_task_timeout_requested(task_id)
        _raise_if_task_cancel_requested(task_id)

        existing_task = get_task(task_id, include_content=False)
        audio_path: Optional[Path] = None
        if existing_task and existing_task.audio_file_path:
            candidate_path = Path(existing_task.audio_file_path).expanduser().resolve()
            if candidate_path.exists():
                audio_path = candidate_path

        if audio_path is None:
            current_stage = "downloading"
            update_task_status(task_id, TaskStatus.DOWNLOADING.value)
            _raise_if_task_cancel_requested(task_id)
            audio_path, info = download_audio(url, download_dir=DOWNLOAD_DIR, return_info=True)
            _raise_if_task_cancel_requested(task_id)
            update_task_content(
                task_id,
                audio_file_path=str(audio_path),
                video_title=info.get("title") if isinstance(info, dict) else None,
                video_duration_seconds=int(info.get("duration")) if isinstance(info, dict) and info.get("duration") else None,
            )

        existing_progress = get_transcription_progress(task_id)
        total_chunks = int(existing_progress.get("total_chunks", 0)) if existing_progress else 0
        completed_chunks = int(existing_progress.get("completed_chunks", 0)) if existing_progress else 0
        has_completed_transcription = total_chunks > 0 and completed_chunks >= total_chunks
        if has_completed_transcription:
            existing_transcript = get_task_transcript(task_id) or ""
            existing_raw_transcript = get_task_raw_transcript(task_id) or ""
            summary_source = existing_transcript or existing_raw_transcript
            if summary_source:
                current_stage = "summarizing"
                update_task_status(task_id, TaskStatus.SUMMARIZING.value)
                _raise_if_task_cancel_requested(task_id)
                summary = summarizer_module.generate_summary(
                    summary_source,
                    system_prompt=system_prompt,
                )
                _raise_if_task_cancel_requested(task_id)
                update_task_content(task_id, summary_text=summary)
                update_task_status(task_id, TaskStatus.COMPLETED.value)
                _clear_task_error(task_id)
                clear_task_cancel_request(task_id)
                return

        current_stage = "transcribing"
        update_task_status(task_id, TaskStatus.TRANSCRIBING.value)
        _raise_if_task_cancel_requested(task_id)

        resume_chunks = None
        if existing_progress and existing_progress.get("completed_chunks", 0) > 0:
            resume_chunks = existing_progress["chunks"]

        def on_chunk_completed(current: int, total: int, chunk_text: str, start_sec: float, end_sec: float) -> None:
            _raise_if_task_cancel_requested(task_id)
            update_transcription_progress(
                task_id=task_id,
                chunk_index=current - 1,
                total_chunks=total,
                chunk_text=chunk_text,
                start_sec=start_sec,
                end_sec=end_sec,
            )

        raw_transcript = audio_to_text(
            audio_path,
            asr_model=TRANSCRIBE_API_MODEL,
            language="zh",
            transcription_prompt=TRANSCRIBE_TEXT_PROMPT,
            progress_callback=on_chunk_completed,
            resume_from_chunks=resume_chunks,
        )
        _raise_if_task_cancel_requested(task_id)
        transcript = _build_readable_transcript(raw_transcript)
        update_task_content(
            task_id,
            transcript_text=transcript,
            transcript_raw_text=raw_transcript,
        )

        current_stage = "summarizing"
        update_task_status(task_id, TaskStatus.SUMMARIZING.value)
        _raise_if_task_cancel_requested(task_id)
        summary = summarizer_module.generate_summary(
            transcript or raw_transcript,
            system_prompt=system_prompt,
        )
        _raise_if_task_cancel_requested(task_id)
        update_task_content(task_id, summary_text=summary)

        update_task_status(task_id, TaskStatus.COMPLETED.value)
        _clear_task_error(task_id)
        clear_task_cancel_request(task_id)
    except TaskCancelledError as exc:
        if exc.reason == "timeout":
            _mark_task_timeout_safely(task_id, str(exc), stage="watchdog")
        else:
            try:
                update_task_status(task_id, TaskStatus.CANCELLED.value)
            except Exception:  # noqa: BLE001
                pass
            _record_task_cancelled(task_id)
        clear_task_cancel_request(task_id)
    except Exception as exc:  # noqa: BLE001
        try:
            partial_raw_transcript = assemble_partial_transcript(task_id)
            if partial_raw_transcript:
                partial_transcript = _build_readable_transcript(partial_raw_transcript)
                update_task_content(
                    task_id,
                    transcript_text=partial_transcript,
                    transcript_raw_text=partial_raw_transcript,
                )
        except Exception:  # noqa: BLE001
            pass
        _mark_task_failed_safely(task_id, str(exc), stage=current_stage)


def _render_running_task(task_id: int) -> Optional[str]:
    try:
        task = get_task(task_id, include_content=False)
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


def _has_active_tasks() -> bool:
    """检查是否存在尚未完成的任务。"""
    try:
        tasks = list_tasks(limit=100, include_content=False)
        active_statuses = {
            TaskStatus.WAITING.value,
            TaskStatus.DOWNLOADING.value,
            TaskStatus.TRANSCRIBING.value,
            TaskStatus.SUMMARIZING.value,
        }
        return any(t.status in active_statuses for t in tasks)
    except Exception:  # noqa: BLE001
        return False


@st.fragment
def _auto_refresh_fragment() -> None:
    """
    页面自动刷新组件。
    - 检测到有活跃任务时，注入 JS 每 3 秒自动刷新页面（保证状态实时）。
    - 监听 Page Visibility API，手机切回前台时立即刷新。
    - 任务全部完成后自动停止刷新。
    """
    has_active = _has_active_tasks()

    if has_active:
        st.query_params["__ar"] = "1"
    elif "__ar" in st.query_params:
        del st.query_params["__ar"]

    poll_script = """
    <script>
    (function() {
        // 页面可见性监听：切回前台立即刷新（手机切后台再切回来时触发）
        document.addEventListener("visibilitychange", function() {
            if (document.visibilityState === "visible") {
                var url = new URL(window.location.href);
                if (!url.searchParams.has("__ar") || url.searchParams.get("__ar") === "1") {
                    var ts = new Date().getTime();
                    var cleanUrl = window.location.pathname + "?__ar=1&t=" + ts + window.location.hash;
                    window.location.replace(cleanUrl);
                }
            }
        });

        // 定期轮询：活跃任务存在时每 3 秒刷新一次
        function scheduleReload() {
            var url = new URL(window.location.href);
            if (!url.searchParams.has("__ar") || url.searchParams.get("__ar") !== "1") return;

            setTimeout(function() {
                var ts = new Date().getTime();
                var cleanUrl = window.location.pathname + "?__ar=1&t=" + ts + window.location.hash;
                window.location.replace(cleanUrl);
            }, 3000);
        }

        // 页面加载时立即开始轮询（如果 query param 标记了活跃状态）
        scheduleReload();
    })();
    </script>
    """
    components.html(poll_script, height=0, scrolling=False)


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
    task_map = {t.id: t for t in tasks}
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
    task = task_map.get(selected_id)
    if task is None:
        try:
            task = get_task(selected_id, include_content=False)
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
    transcript_text = _get_cached_task_text(task.id, "transcript")

    # 使用 Tabs 选项卡样式替代 radio
    summary_tab, transcript_tab = st.tabs(["📋 核心总结", "📝 完整转录"])

    with summary_tab:
        summary_text = _get_cached_task_text(task.id, "summary")
        if summary_text is None:
            with st.spinner("正在加载总结内容..."):
                _load_summary_to_cache(task.id)
            summary_text = _get_cached_task_text(task.id, "summary") or ""

        # 标题栏和操作按钮：标题左侧，按钮靠右
        header_col, action_col = st.columns([1, 0.18], vertical_alignment="center")
        with header_col:
            st.markdown("#### 核心总结")
        with action_col:
            if summary_text:
                _render_action_buttons(
                    task_id=task.id,
                    text_content=summary_text,
                    download_filename=f"task_{task.id}_summary.md",
                    download_label="下载",
                    copy_label="复制",
                    mime="text/markdown",
                    key_prefix="summary",
                )

        if summary_text:
            st.markdown(summary_text)
        else:
            st.info("暂无总结内容。")

        # 显示生成/重新生成总结按钮（只要有转录内容就可以生成）
        transcript_for_summary = transcript_text or _get_cached_task_text(task.id, "raw_transcript")
        if transcript_for_summary:
            regen_running = _is_regen_running(task.id)
            if regen_running:
                st.info("⏳ 正在生成总结，请勿重复点击。")

            if task.status in {
                TaskStatus.SUMMARIZING.value,
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.TIMEOUT.value,
            }:
                regen_btn_label = "生成中..." if regen_running else ("重新生成总结" if summary_text else "生成总结")
                if st.button(
                    regen_btn_label,
                    use_container_width=False,
                    type="secondary",
                    key=f"regen_{task.id}",
                    disabled=regen_running,
                ):
                    if _allow_action(f"open_regen_dialog_{task.id}"):
                        st.session_state["show_regen_dialog"] = True
                        st.session_state["regen_task_id"] = task.id
                        st.session_state.setdefault("regen_model_choice", SUMMARY_MODEL_OPTIONS[0][1])
                    else:
                        st.toast("点击过快，请稍后再试")

            if st.session_state.get("show_regen_dialog") and st.session_state.get("regen_task_id") == task.id:
                _render_regen_dialog(task)

    with transcript_tab:
        if transcript_text is None:
            with st.spinner("正在加载转录文本..."):
                _load_transcript_to_cache(task.id)
            transcript_text = _get_cached_task_text(task.id, "transcript") or ""
        raw_transcript_text = _get_cached_task_text(task.id, "raw_transcript")
        if raw_transcript_text is None:
            with st.spinner("正在加载原始转录..."):
                _load_raw_transcript_to_cache(task.id)
            raw_transcript_text = _get_cached_task_text(task.id, "raw_transcript") or ""

        # 标题栏和操作按钮：标题左侧，按钮靠右
        header_col, action_col = st.columns([1, 0.18], vertical_alignment="center")
        with header_col:
            st.markdown("#### 完整转录（阅读版）")
        with action_col:
            if transcript_text:
                _render_action_buttons(
                    task_id=task.id,
                    text_content=transcript_text,
                    download_filename=f"task_{task.id}_transcript.txt",
                    download_label="下载",
                    copy_label="复制",
                    mime="text/plain",
                    key_prefix="transcript",
                )

        if transcript_text:
            _render_transcript_reader(transcript_text)
            if raw_transcript_text and raw_transcript_text != transcript_text:
                with st.expander("查看原始转录（未补标点）", expanded=False):
                    _render_action_buttons(
                        task_id=task.id,
                        text_content=raw_transcript_text,
                        download_filename=f"task_{task.id}_transcript_raw.txt",
                        download_label="下载原始",
                        copy_label="复制原始",
                        mime="text/plain",
                        key_prefix="transcript_raw",
                    )
                    _render_transcript_reader(raw_transcript_text)
        else:
            st.info("暂无转录文本。")

    st.caption(
        f"任务状态：{STATUS_MAP.get(task.status, task.status)}，"
        f"时长：{_format_duration(task.video_duration_seconds)}, "
        f"创建时间：{task.created_at}"
    )
    cancellable_statuses = {
        TaskStatus.WAITING.value,
        TaskStatus.DOWNLOADING.value,
        TaskStatus.TRANSCRIBING.value,
        TaskStatus.SUMMARIZING.value,
    }
    if task.status in cancellable_statuses:
        if st.button("停止任务", key=f"cancel_task_{task.id}", type="secondary"):
            if request_task_cancel(task.id):
                st.toast("已发送停止请求，当前阶段完成后将停止")
                st.rerun()
            else:
                st.info("任务状态已变化，无需停止。")
    failed_like_statuses = {TaskStatus.FAILED.value, TaskStatus.TIMEOUT.value}
    if task.status in failed_like_statuses:
        if task.status == TaskStatus.TIMEOUT.value:
            st.warning("任务因超时被中止。可直接重试；若频繁超时，建议提高超时阈值。")
        else:
            st.warning("最近一次处理失败。若下方仍显示旧总结，说明本次重新生成未成功覆盖。")
        error_info = get_task_error_info(task.id)
        if error_info:
            st.caption("结构化错误信息")
            st.json(error_info, expanded=False)
    elif task.status == TaskStatus.CANCELLED.value:
        st.info("任务已取消。可重新提交同一链接发起新任务。")
        error_info = get_task_error_info(task.id)
        if error_info:
            st.caption("结构化错误信息")
            st.json(error_info, expanded=False)

    # 显示转写进度信息
    if task.status in failed_like_statuses:
        progress = get_transcription_progress(task.id)
        has_progress = bool(progress and progress.get("completed_chunks", 0) > 0)
        if has_progress:
            st.info(
                f"转写进度：已完成 {progress['completed_chunks']}/{progress['total_chunks']} 个切片"
            )
        retry_col, restart_col = st.columns(2)
        with retry_col:
            if st.button(
                "重试任务（自动）",
                use_container_width=True,
                type="primary",
                key=f"retry_task_{task.id}",
            ):
                _retry_task_in_queue(task, restart_from_scratch=False)
        with restart_col:
            if st.button(
                "从头重跑任务",
                use_container_width=True,
                type="secondary",
                key=f"restart_task_{task.id}",
            ):
                _retry_task_in_queue(task, restart_from_scratch=True)


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
    elif task.status == TaskStatus.TIMEOUT.value:
        st.toast("⏱️ 任务超时，请重试")
    elif task.status == TaskStatus.CANCELLED.value:
        st.toast("⏹️ 任务已取消")
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


def _is_regen_running(task_id: int) -> bool:
    """判断当前任务是否处于重新生成中。"""
    try:
        return int(st.session_state.get(REGEN_RUNNING_TASK_SESSION_KEY, 0)) == int(task_id)
    except (TypeError, ValueError):
        return False


def _render_action_buttons(
    task_id: int,
    text_content: str,
    download_filename: str,
    download_label: str = "下载",
    copy_label: str = "复制",
    mime: str = "text/plain",
    key_prefix: str = "action",
) -> None:
    """渲染并排的复制和下载超链接样式按钮，靠右对齐，中间精确 5px 间隔。"""
    copy_button_html = create_copy_button_with_tooltip(
        button_id=f"{key_prefix}_{task_id}",
        text_to_copy=text_content,
        button_text=copy_label,
        button_color="transparent",
        button_hover_color="#f0f0f0",
        success_message="✓ 已复制",
        error_message="✗ 复制失败",
    )

    from utils.download_button import create_download_button

    download_button_html = create_download_button(
        button_id=f"{key_prefix}_{task_id}",
        content=text_content,
        filename=download_filename,
        label=download_label,
        mime=mime,
        button_color="transparent",
        button_hover_color="#f0f0f0",
    )

    action_row_html = f"""
    <style>
      html, body {{
        margin: 0;
        padding: 0;
      }}
      .action-row {{
        width: 100%;
        display: flex;
        justify-content: flex-end;
        align-items: flex-start;
        gap: 5px;
        flex-wrap: wrap;
      }}
      .action-item {{
        flex: 0 0 auto;
      }}
    </style>
    <div class="action-row">
      <div class="action-item">{copy_button_html}</div>
      <div class="action-item">{download_button_html}</div>
    </div>
    """
    components.html(action_row_html, height=40, scrolling=False)


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


def _ensure_task_text_cache() -> dict[int, dict[str, str]]:
    """确保会话内任务文本缓存结构可用。"""
    cache: Any = st.session_state.get(TASK_TEXT_CACHE_SESSION_KEY)
    if isinstance(cache, dict):
        return cache
    new_cache: dict[int, dict[str, str]] = {}
    st.session_state[TASK_TEXT_CACHE_SESSION_KEY] = new_cache
    return new_cache


def _get_cached_task_text(task_id: int, key: str) -> Optional[str]:
    """读取指定任务的缓存文本，未命中返回 None。"""
    cache = _ensure_task_text_cache()
    task_cache = cache.get(task_id, {})
    if not isinstance(task_cache, dict):
        return None
    value = task_cache.get(key)
    return value if isinstance(value, str) else None


def _set_cached_task_text(task_id: int, key: str, content: str) -> None:
    """写入指定任务的缓存文本。"""
    cache = _ensure_task_text_cache()
    task_cache = cache.get(task_id)
    if not isinstance(task_cache, dict):
        task_cache = {}
    task_cache[key] = content
    cache[task_id] = task_cache
    st.session_state[TASK_TEXT_CACHE_SESSION_KEY] = cache


def _load_summary_to_cache(task_id: int) -> None:
    """从数据库按需读取总结并写入缓存。"""
    summary_text = get_task_summary(task_id) or ""
    _set_cached_task_text(task_id, "summary", summary_text)


def _load_transcript_to_cache(task_id: int) -> None:
    """从数据库按需读取转录并写入缓存。"""
    transcript_text = get_task_transcript(task_id) or ""
    _set_cached_task_text(task_id, "transcript", transcript_text)


def _load_raw_transcript_to_cache(task_id: int) -> None:
    """从数据库按需读取原始转录并写入缓存。"""
    raw_transcript_text = get_task_raw_transcript(task_id) or ""
    _set_cached_task_text(task_id, "raw_transcript", raw_transcript_text)


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
        /* 移动端适配 */
        @media (max-width: 768px) {
            .transcript-reader {
                max-height: 400px;
                padding: 0.75rem;
            }
            .transcript-reader pre {
                font-size: 0.9375rem;
                line-height: 1.5;
            }
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


def _render_bilibili_login() -> None:
    """B站扫码登录组件。"""
    with st.expander("🔑 B站登录", expanded=False):
        if has_bilibili_cookies():
            st.success("✅ Cookies 已配置，可以下载大多数视频")
            st.caption(f"保存路径：{COOKIE_FILE}")
            st.caption("💡 会员视频需要有效的 Cookies，普通视频一般无需登录")
            if st.button("清除 Cookies", use_container_width=True):
                if COOKIE_FILE.is_file():
                    COOKIE_FILE.unlink()
                st.rerun()
            return

        st.warning("未配置 Cookies，部分视频可能下载失败（HTTP 412/403）")

        if st.button("📱 扫码登录B站", use_container_width=True, key="bili_qr_start"):
            try:
                qr_data = generate_bilibili_qr()
                oauth_key = qr_data["oauth_key"]
                qr_url = qr_data["url"]
                callback_url = get_cookie_receive_url()
            except Exception as exc:
                st.error(f"生成二维码失败：{exc}")
                return

            # 生成二维码图片
            try:
                import qrcode, io
                img = qrcode.make(qr_url)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_bytes = buf.getvalue()
            except Exception as exc:
                st.error(f"生成二维码图片失败：{exc}")
                return

            st.session_state.bili_qr_oauth_key = oauth_key
            st.session_state.bili_callback_url = callback_url
            st.session_state.bili_qr_polling = True

        if st.session_state.get("bili_qr_polling"):
            oauth_key = st.session_state.get("bili_qr_oauth_key", "")
            callback_url = st.session_state.get("bili_callback_url", "")

            st.info("📱 请用 B站App 扫码登录（点击二维码 → 相册选图）")
            try:
                import qrcode, io
                img = qrcode.make(
                    "https://passport.bilibili.com/qrcode/h5/login?oauthKey=" + oauth_key
                )
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                qr_bytes = buf.getvalue()
                st.image(qr_bytes, width=220, caption="请用 B站App 扫码")
            except Exception:
                st.text("oauthKey: " + oauth_key)

            # 浏览器端轮询 + 自动刷新
            poll_script = f"""
            <div id="bili-login-status" style="padding:8px 0;font-family:sans-serif">
                ⏳ 等待扫码确认...
            </div>
            <div id="bili-error" style="color:#ff6b6b;padding:4px 0;display:none"></div>
            <script>
            (function() {{
                var oauthKey = "{oauth_key}";
                var callbackUrl = "{callback_url}";
                var POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll";
                var TIMEOUT = 90;
                var start = Date.now();

                function poll() {{
                    if (Date.now() - start > TIMEOUT * 1000) {{
                        document.getElementById("bili-login-status").textContent = "⏰ 扫码超时，请重新点击「扫码登录」";
                        return;
                    }}

                    var xhr = new XMLHttpRequest();
                    xhr.open("POST", POLL_URL, true);
                    xhr.setRequestHeader("Content-Type", "application/json");
                    xhr.setRequestHeader("Referer", "https://www.bilibili.com/");
                    xhr.onload = function() {{
                        try {{
                            var d = JSON.parse(xhr.responseText);
                            var code = (d.data && d.data.code) || d.data;
                            var statusEl = document.getElementById("bili-login-status");
                            if (code == 86100) {{
                                statusEl.textContent = "📱 请用 B站App 扫码...";
                                setTimeout(poll, 1000);
                            }} else if (code == 86038) {{
                                statusEl.textContent = "✅ 已扫码，请在手机端确认";
                                setTimeout(poll, 1000);
                            }} else if (code == 86090) {{
                                var url = d.data && d.data.url || "";
                                var cookies = parseCookies(url);
                                statusEl.textContent = "🎉 登录成功！正在保存 Cookies...";
                                // 发送 cookies 到我们的服务器
                                if (cookies) {{
                                    var postXhr = new XMLHttpRequest();
                                    postXhr.open("POST", callbackUrl, true);
                                    postXhr.setRequestHeader("Content-Type", "application/json");
                                    postXhr.onload = function() {{
                                        setTimeout(function() {{ window.location.reload(); }}, 1500);
                                    }};
                                    postXhr.onerror = function() {{
                                        // 即使回调失败，cookies 可能已保存
                                        setTimeout(function() {{ window.location.reload(); }}, 1500);
                                    }};
                                    postXhr.send(JSON.stringify({{cookies: cookies}}));
                                }} else {{
                                    setTimeout(function() {{ window.location.reload(); }}, 1500);
                                }}
                            }} else if (code == -2) {{
                                statusEl.textContent = "⏰ 二维码已过期，请重新扫码";
                            }} else {{
                                statusEl.textContent = "登录状态: " + code + "，请重新扫码";
                            }}
                        }} catch(e) {{
                            document.getElementById("bili-login-status").textContent = "轮询异常，重试中...";
                            setTimeout(poll, 2000);
                        }}
                    }};
                    xhr.onerror = function() {{
                        document.getElementById("bili-login-status").textContent = "网络异常，2秒后重试...";
                        setTimeout(poll, 2000);
                    }};
                    xhr.send(JSON.stringify({{oauthKey: oauthKey, source: "main", scopes: "login"}}));
                }}

                function parseCookies(url) {{
                    try {{
                        var cookies = {{}};
                        // 从 URL hash 中提取
                        var hash = url.split("#")[1] || "";
                        hash.split("&").forEach(function(p) {{
                            var kv = p.split("=");
                            if (kv[0] && kv[1]) cookies[kv[0]] = decodeURIComponent(kv[1]);
                        }});
                        if (Object.keys(cookies).length > 0) {{
                            var lines = ["# Netscape HTTP Cookie File", "# Generated by BilView"];
                            var expire = Math.floor(Date.now()/1000) + 25*24*3600;
                            for (var k in cookies) {{
                                if (["SESSDATA","bili_jct","DedeUserID","DedeUserID__ckMd5","sid"].indexOf(k) >= 0) {{
                                    lines.push(".bilibili.com\\tTRUE\\t/\\tTRUE\\t" + expire + "\\t" + k + "\\t" + cookies[k]);
                                }}
                            }}
                            return lines.join("\\n");
                        }}
                    }} catch(e) {{}}
                    return "";
                }}

                poll();
            }})();
            </script>
            """
            st.components.v1.html(poll_script, height=80, scrolling=False)

            if st.button("取消扫码", key="bili_qr_cancel"):
                st.session_state.bili_qr_polling = False
                st.rerun()
        else:
            st.caption(
                "💡 点击后用 B站App 扫码，确认后自动保存，无需手动上传文件。"
            )


def _render_settings(show_title: bool = True) -> None:
    if show_title:
        st.subheader("设置与清理")

    _render_bilibili_login()

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
        st.caption("提示：为空则自动使用 docs/default_prompt.md 的默认提示。")

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
        regen_running = _is_regen_running(task.id)
        selected_model = st.radio(
            "选择模型",
            options=model_values,
            index=default_index,
            format_func=lambda val: f"{label_map.get(val, val)}",
            key=f"regen_model_radio_{task.id}",
            disabled=regen_running,
        )
        st.session_state["regen_model_choice"] = selected_model
        if regen_running:
            st.info("⏳ 正在生成总结，请耐心等待当前任务完成。")
        else:
            st.caption("提示：当前模型高峰期可切换备用模型再试。")

        col_confirm, col_cancel = st.columns(2)
        with col_confirm:
            if st.button(
                "开始生成",
                type="primary",
                use_container_width=True,
                key=f"confirm_regen_{task.id}",
                disabled=regen_running,
            ):
                if _allow_action(f"confirm_regen_{task.id}"):
                    _regenerate_summary(task, model=selected_model)
                else:
                    st.info("点击过快，请稍后再试。")
        with col_cancel:
            if st.button(
                "取消",
                use_container_width=True,
                key=f"cancel_regen_{task.id}",
                disabled=regen_running,
            ):
                st.session_state["show_regen_dialog"] = False
                st.session_state["regen_task_id"] = None

    _dialog()


def _initialize_database(show_feedback: bool) -> bool:
    """执行数据库初始化，并更新会话内状态。"""
    return _ensure_database_schema_ready(show_feedback=show_feedback)


def _ensure_database_schema_ready(show_feedback: bool) -> bool:
    """校验并补齐数据库 Schema。失败时更新会话状态并返回 False。"""
    try:
        init_db()
        st.session_state.db_initialized = True
        st.session_state[DB_SCHEMA_READY_SESSION_KEY] = True
        st.session_state[DB_SCHEMA_ERROR_SESSION_KEY] = ""
        if show_feedback:
            st.success("数据库 Schema 校验完成")
        return True
    except Exception as exc:  # noqa: BLE001
        error_text = str(exc)
        st.session_state.db_initialized = False
        st.session_state[DB_SCHEMA_READY_SESSION_KEY] = False
        st.session_state[DB_SCHEMA_ERROR_SESSION_KEY] = error_text
        LOGGER.error("数据库 Schema 校验失败：%s", error_text)
        if show_feedback:
            st.error(f"数据库 Schema 校验失败：{error_text}")
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


def _mark_task_failed_safely(task_id: int, error_text: str, stage: str = "system") -> None:
    """尽力将任务标记为失败，避免卡在 waiting/transcribing。"""
    try:
        update_task_status(task_id, TaskStatus.FAILED.value)
    except Exception:  # noqa: BLE001
        return
    _record_task_error(task_id, stage=stage, error_text=error_text)

    # 可选补充一条可见错误信息，避免空白失败记录。
    try:
        task = get_task(task_id)
        has_any_content = bool(task and ((task.transcript_text and task.transcript_text.strip()) or (task.summary_text and task.summary_text.strip())))
        if not has_any_content:
            update_task_content(task_id, summary_text=f"[系统] 任务异常终止：{error_text}")
    except Exception:  # noqa: BLE001
        pass


def _mark_task_timeout_safely(task_id: int, error_text: str, stage: str = "watchdog") -> None:
    """尽力将任务标记为超时，并保留结构化错误信息。"""
    try:
        update_task_status(task_id, TaskStatus.TIMEOUT.value)
    except Exception:  # noqa: BLE001
        return
    _record_task_error(task_id, stage=stage, error_text=error_text)


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
    transcript_text = task.transcript_text or get_task_transcript(task.id)
    raw_transcript_text = get_task_raw_transcript(task.id)
    summary_source_text = transcript_text or raw_transcript_text
    if not summary_source_text:
        _set_regen_feedback(task.id, "error", "暂无转录文本，无法生成总结")
        st.session_state["show_regen_dialog"] = False
        st.session_state["regen_task_id"] = None
        st.rerun()
        return
    if _is_regen_running(task.id):
        st.info("总结正在生成中，请勿重复提交。")
        return

    chosen_model = model or st.session_state.get("regen_model_choice") or DEFAULT_LLM_MODEL
    st.session_state[REGEN_RUNNING_TASK_SESSION_KEY] = task.id
    try:
        with st.status("重新生成总结中...", expanded=True) as status_box:
            update_task_status(task.id, TaskStatus.SUMMARIZING.value)
            status_box.write(f"已提交模型：{chosen_model}")
            summary = summarizer_module.generate_summary(
                summary_source_text,
                system_prompt=_get_active_prompt(),
                model=chosen_model,
            )
            if not summary.strip():
                raise RuntimeError("模型未返回有效总结内容，请切换模型后重试。")
            update_task_content(task.id, summary_text=summary)
            _set_cached_task_text(task.id, "summary", summary)
            update_task_status(task.id, TaskStatus.COMPLETED.value)
            _clear_task_error(task.id)
            status_box.update(label="总结重新生成完成", state="complete")
            _set_regen_feedback(task.id, "success", "总结已重新生成")
    except Exception as exc:  # noqa: BLE001
        _mark_task_failed_safely(task.id, str(exc), stage="summarizing")
        if not (task.summary_text and task.summary_text.strip()):
            try:
                update_task_content(task.id, summary_text=f"[系统] 重新生成失败：{exc}")
            except Exception:  # noqa: BLE001
                pass
        _set_regen_feedback(task.id, "error", f"重新生成失败：{exc}")
    finally:
        st.session_state.pop(REGEN_RUNNING_TASK_SESSION_KEY, None)
        st.session_state["show_regen_dialog"] = False
        st.session_state["regen_task_id"] = None
    st.rerun()


def _retry_transcription(task: Task) -> None:
    """从断点继续转写失败任务。"""
    _run_transcription_flow(task, restart_from_scratch=False)


def _restart_transcription(task: Task) -> None:
    """清空历史转写结果后，从第一个切片重新转写。"""
    _run_transcription_flow(task, restart_from_scratch=True)


def _retry_task_in_queue(task: Task, restart_from_scratch: bool = False) -> None:
    """将失败/超时任务重新入队，支持保留断点或从头重跑。"""
    try:
        clear_task_cancel_request(task.id)
        _clear_task_timeout_requested(task.id)
        if restart_from_scratch:
            reset_transcription_data(task.id)
            _set_cached_task_text(task.id, "transcript", "")
            _set_cached_task_text(task.id, "raw_transcript", "")
            _set_cached_task_text(task.id, "summary", "")
        update_task_status(task.id, TaskStatus.WAITING.value)
        _clear_task_error(task.id)
        _remember_task_prompt(task.id, _get_active_prompt())
        _get_task_executor().notify_new_task()
        st.session_state.running_task_id = task.id
        st.toast("已加入重试队列")
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"提交重试失败：{exc}")


def _run_transcription_flow(task: Task, restart_from_scratch: bool) -> None:
    """执行转写+总结流程，支持断点续传和从头重跑两种模式。"""
    clear_task_cancel_request(task.id)
    if not task.audio_file_path:
        missing_path_error = "音频文件路径缺失，无法继续转写"
        _record_task_error(task.id, stage="transcribing", error_text=missing_path_error)
        st.error(missing_path_error)
        return

    audio_path = Path(task.audio_file_path)
    if not audio_path.exists():
        missing_file_error = f"音频文件不存在：{audio_path}"
        _record_task_error(task.id, stage="transcribing", error_text=missing_file_error)
        st.error(missing_file_error)
        return

    status_label = "从头重新转写中..." if restart_from_scratch else "继续转写中..."
    success_message = "已从头完成转写" if restart_from_scratch else "转写已完成"
    with st.status(status_label, expanded=True) as status_box:
        current_stage = "transcribing"
        try:
            update_task_status(task.id, TaskStatus.TRANSCRIBING.value)

            resume_chunks = None
            if restart_from_scratch:
                reset_transcription_data(task.id)
                _set_cached_task_text(task.id, "transcript", "")
                _set_cached_task_text(task.id, "raw_transcript", "")
                _set_cached_task_text(task.id, "summary", "")
                status_box.info("已清空历史进度，将从第 1 个切片开始。")
            else:
                # 获取断点续传数据
                existing_progress = get_transcription_progress(task.id)
                if existing_progress:
                    resume_chunks = existing_progress["chunks"]
                    status_box.info(
                        f"从第 {existing_progress['completed_chunks'] + 1} 个切片继续..."
                    )
                else:
                    status_box.info("未检测到断点进度，将从第 1 个切片开始。")

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
            raw_transcript = audio_to_text(
                audio_path,
                asr_model=TRANSCRIBE_API_MODEL,
                language="zh",
                transcription_prompt=TRANSCRIBE_TEXT_PROMPT,
                progress_callback=on_chunk_completed,
                resume_from_chunks=resume_chunks,
            )
            transcript = _build_readable_transcript(raw_transcript)
            update_task_content(
                task.id,
                transcript_text=transcript,
                transcript_raw_text=raw_transcript,
            )
            _set_cached_task_text(task.id, "transcript", transcript)
            _set_cached_task_text(task.id, "raw_transcript", raw_transcript)

            # 继续总结
            current_stage = "summarizing"
            update_task_status(task.id, TaskStatus.SUMMARIZING.value)
            status_box.write("总结中（LLM）...")
            summary = summarizer_module.generate_summary(
                transcript or raw_transcript,
                system_prompt=_get_active_prompt(),
            )
            update_task_content(task.id, summary_text=summary)
            _set_cached_task_text(task.id, "summary", summary)

            update_task_status(task.id, TaskStatus.COMPLETED.value)
            _clear_task_error(task.id)
            status_box.update(label="处理完成", state="complete")
            st.success(success_message)
        except Exception as exc:  # noqa: BLE001
            # 保存部分结果
            partial_raw_transcript = assemble_partial_transcript(task.id)
            if partial_raw_transcript:
                partial_transcript = _build_readable_transcript(partial_raw_transcript)
                update_task_content(
                    task.id,
                    transcript_text=partial_transcript,
                    transcript_raw_text=partial_raw_transcript,
                )
                _set_cached_task_text(task.id, "transcript", partial_transcript)
                _set_cached_task_text(task.id, "raw_transcript", partial_raw_transcript)
                status_box.warning(f"转写部分完成（{len(partial_transcript)} 字符），但遇到错误")

            _mark_task_failed_safely(task.id, str(exc), stage=current_stage)
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


if __name__ == "__main__":
    main()
