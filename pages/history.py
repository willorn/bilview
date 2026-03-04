"""
历史记录页：以表格结构展示任务数据。
"""
from __future__ import annotations

import html
import math
import re
from datetime import datetime
from textwrap import dedent
from typing import Any, Optional
from urllib.parse import urlencode

import streamlit as st

from db.database import (
    Task,
    TaskStatus,
    delete_task,
    init_db,
    list_tasks_paginated_with_total,
)

STATUS_STYLE_MAP = {
    TaskStatus.COMPLETED.value: ("成功", "status-success"),
    TaskStatus.FAILED.value: ("失败", "status-failed"),
    TaskStatus.WAITING.value: ("等待中", "status-waiting"),
    TaskStatus.DOWNLOADING.value: ("下载中", "status-running"),
    TaskStatus.TRANSCRIBING.value: ("转录中", "status-running"),
    TaskStatus.SUMMARIZING.value: ("总结中", "status-running"),
}
BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)", re.IGNORECASE)
PAGE_SIZE_OPTIONS = (10, 20)
DEFAULT_PAGE_SIZE = 10
QUERY_PAGE_KEY = "page"
QUERY_PAGE_SIZE_KEY = "page_size"
QUERY_SEARCH_KEY = "q"
QUERY_DELETE_KEY = "delete_task"


def main() -> None:
    st.set_page_config(
        page_title="历史记录",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_table_styles()
    _ensure_pagination_state()

    back_col, refresh_col, _ = st.columns([1, 1, 6], vertical_alignment="top")
    with back_col:
        if st.button("← 返回工作台", use_container_width=True):
            st.switch_page("app.py")
    with refresh_col:
        if st.button("🔄 刷新", use_container_width=True, key="refresh_history"):
            _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
            st.rerun()

    st.title("历史记录")
    st.caption("按时间倒序展示任务。可点击链接打开原视频，或从“操作”列进入工作台详情。")
    _handle_pending_delete()
    _render_search_bar()

    with st.spinner("正在加载历史记录..."):
        paged_data = _load_tasks_page(
            page=st.session_state.history_page,
            page_size=st.session_state.history_page_size,
            title_keyword=st.session_state.history_search_keyword,
        )

    if paged_data is None:
        return
    tasks, total_count = paged_data
    if not tasks:
        keyword = str(st.session_state.history_search_keyword).strip()
        if keyword:
            st.info(f'未找到标题包含"{keyword}"的记录。')
        else:
            st.info("暂无历史任务。")
        return

    _render_history_table(tasks)
    _render_pagination(total_count=total_count, page_size=st.session_state.history_page_size)


def _ensure_pagination_state() -> None:
    if "history_page" not in st.session_state:
        st.session_state.history_page = _read_positive_int_query_param(QUERY_PAGE_KEY) or 1
    if "history_page_size" not in st.session_state:
        query_page_size = _read_positive_int_query_param(QUERY_PAGE_SIZE_KEY)
        st.session_state.history_page_size = _normalize_page_size(query_page_size)
    if "history_search_keyword" not in st.session_state:
        st.session_state.history_search_keyword = _read_text_query_param(QUERY_SEARCH_KEY)
    if "history_search_input" not in st.session_state:
        st.session_state.history_search_input = st.session_state.history_search_keyword
    _sync_history_query_params(pending_delete_id=_peek_delete_task_id())


def _render_search_bar() -> None:
    search_col, _ = st.columns([3, 5], vertical_alignment="bottom")
    with search_col:
        st.text_input(
            "搜索标题",
            key="history_search_input",
            placeholder="搜索视频标题...",
            on_change=_apply_search_keyword,
        )

    active_keyword = str(st.session_state.history_search_keyword).strip()
    if active_keyword:
        st.caption(f'当前筛选：标题包含"{active_keyword}"')


def _apply_search_keyword() -> None:
    keyword = str(st.session_state.get("history_search_input", "")).strip()
    st.session_state.history_search_keyword = keyword
    st.session_state.history_page = 1
    _sync_history_query_params(pending_delete_id=_peek_delete_task_id())


def _clear_search_keyword() -> None:
    st.session_state.history_search_input = ""
    st.session_state.history_search_keyword = ""
    st.session_state.history_page = 1
    _sync_history_query_params(pending_delete_id=_peek_delete_task_id())


def _load_tasks_page(
    page: int,
    page_size: int,
    title_keyword: Optional[str] = None,
) -> Optional[tuple[list[Task], int]]:
    try:
        tasks, total_count = list_tasks_paginated_with_total(
            page=page,
            page_size=page_size,
            include_content=False,
            title_keyword=title_keyword,
        )
        if total_count <= 0:
            st.session_state.history_page = 1
            _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
            return [], 0

        total_pages = _calculate_total_pages(total_count=total_count, page_size=page_size)
        st.session_state.history_page = min(max(int(page), 1), total_pages)
        _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
        return tasks, total_count
    except Exception as exc:  # noqa: BLE001
        st.warning(f"数据库暂不可用：{exc}")
        if st.button("初始化数据库", type="primary", use_container_width=False):
            try:
                init_db()
                st.success("数据库初始化完成")
                st.rerun()
            except Exception as init_exc:  # noqa: BLE001
                st.error(f"初始化失败：{init_exc}")
        return None


def _render_history_table(tasks: list[Task]) -> None:
    rows_html = "".join(_build_task_row_html(task) for task in tasks)
    table_html = (
        '<div class="history-table-wrap">'
        '<table class="history-table">'
        "<thead>"
        "<tr>"
        '<th class="title-col">视频标题</th>'
        "<th>原链接</th>"
        "<th>处理时间</th>"
        "<th>任务状态</th>"
        '<th class="action-col">操作</th>'
        "</tr>"
        "</thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)


def _render_pagination(total_count: int, page_size: int) -> None:
    if total_count <= 0:
        return

    normalized_page_size = _normalize_page_size(page_size)
    st.session_state.history_page_size = normalized_page_size
    total_pages = _calculate_total_pages(total_count=total_count, page_size=normalized_page_size)
    current_page = max(min(int(st.session_state.history_page), total_pages), 1)
    st.session_state.history_page = current_page

    start_idx = (current_page - 1) * normalized_page_size + 1
    end_idx = min(current_page * normalized_page_size, total_count)

    spacer_col, size_col, page_col, prev_col, next_col = st.columns([4, 1, 2, 1, 1], vertical_alignment="center")
    with spacer_col:
        st.empty()
    with size_col:
        selected_page_size = st.selectbox(
            "每页条数",
            options=PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(normalized_page_size),
            key="history_page_size_selector_bottom",
            label_visibility="collapsed",
        )
        if int(selected_page_size) != int(st.session_state.history_page_size):
            st.session_state.history_page_size = int(selected_page_size)
            st.session_state.history_page = 1
            _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
            st.rerun()
    with page_col:
        st.caption(f"第 {current_page}/{total_pages} 页 · {start_idx}-{end_idx}/{total_count}")
    with prev_col:
        if st.button("上一页", use_container_width=True, disabled=current_page <= 1, key="history_prev_page"):
            st.session_state.history_page = current_page - 1
            _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
            st.rerun()
    with next_col:
        if st.button("下一页", use_container_width=True, disabled=current_page >= total_pages, key="history_next_page"):
            st.session_state.history_page = current_page + 1
            _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
            st.rerun()


def _calculate_total_pages(total_count: int, page_size: int) -> int:
    if total_count <= 0:
        return 1
    return max(math.ceil(total_count / max(int(page_size), 1)), 1)


def _build_task_row_html(task: Task) -> str:
    title = task.video_title.strip() if task.video_title and task.video_title.strip() else "未命名视频"
    safe_title = html.escape(title, quote=True)

    source_url = task.bilibili_url or ""
    safe_source_url = html.escape(source_url, quote=True)
    bv_id = _extract_bv_id(source_url)
    source_cell_html = (
        f'<a href="{safe_source_url}" target="_blank" rel="noopener noreferrer" class="source-link" title="{bv_id}">'
        f'<svg class="icon-link" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        f'<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>'
        f'<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>'
        f'</svg><span class="bv-text">{html.escape(bv_id, quote=False)}</span></a>'
        if source_url and bv_id != "-"
        else "<span>-</span>"
    )

    formatted_time = html.escape(_format_created_at(task.created_at), quote=False)
    status_text, status_class = _resolve_status_style(task.status)
    safe_status_text = html.escape(status_text, quote=False)

    detail_link = html.escape(f"/?task_id={task.id}", quote=True)
    delete_link = html.escape(_build_delete_link(task.id), quote=True)

    row_html = dedent(
        f"""
        <tr>
          <td class="title-col"><span class="title-text" title="{safe_title}">{safe_title}</span></td>
          <td>{source_cell_html}</td>
          <td>{formatted_time}</td>
          <td><span class="status-tag {status_class}">{safe_status_text}</span></td>
          <td class="action-col">
            <div class="action-group">
              <a class="action-icon view-icon" href="{detail_link}" target="_self" title="查看详情">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                  <circle cx="12" cy="12" r="3"></circle>
                </svg>
              </a>
              <a class="action-icon delete-icon" href="{delete_link}" target="_self" title="删除">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="3 6 5 6 21 6"></polyline>
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                </svg>
              </a>
            </div>
          </td>
        </tr>
        """
    ).strip()
    return row_html


def _extract_bv_id(source_url: str) -> str:
    if not source_url:
        return "-"
    matched = BV_PATTERN.search(source_url)
    if matched:
        bv = matched.group(1).upper()
        return bv[:10] + "..." if len(bv) > 10 else bv
    return "链接"


def _format_created_at(raw_time: str) -> str:
    if not raw_time:
        return "-"

    normalized = raw_time.strip().replace("T", " ").replace("Z", "")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(normalized, pattern)
                return parsed.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
    return normalized[:16] if len(normalized) >= 16 else normalized


def _resolve_status_style(status: str) -> tuple[str, str]:
    if status in STATUS_STYLE_MAP:
        return STATUS_STYLE_MAP[status]
    return status, "status-default"


def _build_delete_link(task_id: int) -> str:
    params = {
        QUERY_PAGE_KEY: str(max(int(st.session_state.get("history_page", 1)), 1)),
        QUERY_PAGE_SIZE_KEY: str(_normalize_page_size(st.session_state.get("history_page_size"))),
        QUERY_DELETE_KEY: str(task_id),
    }
    keyword = str(st.session_state.get("history_search_keyword", "")).strip()
    if keyword:
        params[QUERY_SEARCH_KEY] = keyword
    return f"?{urlencode(params)}"


def _handle_pending_delete() -> None:
    pending_delete_id = _peek_delete_task_id()
    if pending_delete_id is None:
        return

    st.warning(f"将删除记录 #{pending_delete_id}，此操作不可恢复。")
    confirm_col, cancel_col, _ = st.columns([1, 1, 6], vertical_alignment="center")
    with confirm_col:
        if st.button("确认删除", type="primary", use_container_width=True, key=f"confirm_delete_{pending_delete_id}"):
            try:
                delete_task(pending_delete_id)
                st.toast(f"🗑️ 已删除记录 #{pending_delete_id}")
                _sync_history_query_params(pending_delete_id=None)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"删除失败：{exc}")
    with cancel_col:
        if st.button("取消删除", use_container_width=True, key=f"cancel_delete_{pending_delete_id}"):
            _sync_history_query_params(pending_delete_id=None)
            st.rerun()


def _peek_delete_task_id() -> Optional[int]:
    return _read_positive_int_query_param(QUERY_DELETE_KEY)


def _read_positive_int_query_param(param_name: str) -> Optional[int]:
    raw_value: Any = st.query_params.get(param_name)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    if raw_value is None:
        return None

    try:
        value = int(str(raw_value))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _read_text_query_param(param_name: str) -> str:
    raw_value: Any = st.query_params.get(param_name)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    if raw_value is None:
        return ""
    return str(raw_value).strip()


def _normalize_page_size(page_size: Any) -> int:
    try:
        value = int(page_size)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    if value not in PAGE_SIZE_OPTIONS:
        return DEFAULT_PAGE_SIZE
    return value


def _sync_history_query_params(pending_delete_id: Optional[int]) -> None:
    target_params = {
        QUERY_PAGE_KEY: str(max(int(st.session_state.get("history_page", 1)), 1)),
        QUERY_PAGE_SIZE_KEY: str(
            _normalize_page_size(st.session_state.get("history_page_size", DEFAULT_PAGE_SIZE))
        ),
    }
    keyword = str(st.session_state.get("history_search_keyword", "")).strip()
    if keyword:
        target_params[QUERY_SEARCH_KEY] = keyword
    if pending_delete_id and pending_delete_id > 0:
        target_params[QUERY_DELETE_KEY] = str(pending_delete_id)

    current_params: dict[str, str] = {}
    for key in st.query_params.keys():
        raw_value: Any = st.query_params.get(key)
        if isinstance(raw_value, list):
            raw_value = raw_value[0] if raw_value else ""
        if raw_value is None:
            continue
        current_params[str(key)] = str(raw_value)

    reserved_keys = {QUERY_PAGE_KEY, QUERY_PAGE_SIZE_KEY, QUERY_SEARCH_KEY, QUERY_DELETE_KEY}
    passthrough_params = {k: v for k, v in current_params.items() if k not in reserved_keys}
    merged_params = {**passthrough_params, **target_params}
    if current_params == merged_params:
        return

    for key in list(st.query_params.keys()):
        del st.query_params[key]
    for key, value in merged_params.items():
        st.query_params[key] = value


def _inject_table_styles() -> None:
    st.markdown(
        """
        <style>
        .history-table-wrap {
            border: 1px solid rgba(15, 23, 42, 0.08);
            overflow: auto;
            max-height: 72vh;
            background: #ffffff;
        }
        .history-table {
            width: 100%;
            min-width: 800px;
            border-collapse: separate;
            border-spacing: 0;
            table-layout: fixed;
        }
        .history-table th {
            position: sticky;
            top: 0;
            z-index: 4;
            padding: 10px 12px;
            border-bottom: 1px solid #e5e7eb;
            background: #f8fafc;
            text-align: left;
            font-size: 0.85rem;
            font-weight: 500;
            color: #64748b;
            white-space: nowrap;
        }
        .history-table td {
            padding: 10px 12px;
            border-bottom: 1px solid #f1f5f9;
            background: #ffffff;
            vertical-align: middle;
            color: #334155;
            font-size: 0.9rem;
        }
        .history-table tr:hover td {
            background: #f8fafc;
        }
        .history-table .title-col {
            width: 45%;
            max-width: 480px;
        }
        .history-table .title-text {
            display: block;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .history-table .status-tag {
            display: inline-flex;
            align-items: center;
            font-size: 0.85rem;
            font-weight: 500;
        }
        /* 成功状态：仅文字，无背景 */
        .history-table .status-success {
            color: #10b981;
        }
        .history-table .status-failed {
            color: #ef4444;
            background: #fef2f2;
            padding: 2px 8px;
            border-radius: 4px;
        }
        .history-table .status-running {
            color: #3b82f6;
            background: #eff6ff;
            padding: 2px 8px;
            border-radius: 4px;
        }
        .history-table .status-waiting {
            color: #f59e0b;
            background: #fffbeb;
            padding: 2px 8px;
            border-radius: 4px;
        }
        .history-table .status-default {
            color: #6b7280;
        }
        .history-table .action-col {
            position: sticky;
            right: 0;
            z-index: 3;
            min-width: 80px;
            width: 80px;
            text-align: center;
            background: #ffffff;
        }
        .history-table tr:hover .action-col {
            background: #f8fafc;
        }
        .history-table th.action-col {
            z-index: 5;
            background: #f8fafc;
        }
        /* 图标按钮样式 */
        .history-table .action-group {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            justify-content: center;
        }
        .history-table .action-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 4px;
            color: #64748b;
            text-decoration: none;
            transition: all 0.15s ease;
        }
        .history-table .action-icon:hover {
            background: #e2e8f0;
        }
        .history-table .action-icon svg {
            width: 16px;
            height: 16px;
        }
        .history-table .view-icon:hover {
            color: #3b82f6;
            background: #eff6ff;
        }
        .history-table .delete-icon:hover {
            color: #ef4444;
            background: #fef2f2;
        }
        /* 链接样式 */
        .history-table .source-link {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            color: #3b82f6;
            text-decoration: none;
            font-size: 0.85rem;
        }
        .history-table .source-link:hover {
            text-decoration: underline;
        }
        .history-table .icon-link {
            width: 14px;
            height: 14px;
        }
        .history-table .bv-text {
            font-family: monospace;
            font-size: 0.8rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
