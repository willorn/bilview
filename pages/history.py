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

import streamlit as st

from db.database import Task, TaskStatus, count_tasks, delete_task, init_db, list_tasks_paginated

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
        if st.button("⬅️ 返回工作台", type="primary", use_container_width=True):
            st.switch_page("app.py")
    with refresh_col:
        st.button("🔄 刷新", use_container_width=True, key="refresh_history")

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
            st.info(f"未找到标题包含“{keyword}”的记录。")
        else:
            st.info("暂无历史任务。")
        return

    _render_history_table(tasks)
    _render_pagination(total_count=total_count, page_size=st.session_state.history_page_size)


def _ensure_pagination_state() -> None:
    if "history_page" not in st.session_state:
        st.session_state.history_page = 1
    if "history_page_size" not in st.session_state:
        st.session_state.history_page_size = DEFAULT_PAGE_SIZE
    if "history_search_keyword" not in st.session_state:
        st.session_state.history_search_keyword = ""
    if "history_search_input" not in st.session_state:
        st.session_state.history_search_input = st.session_state.history_search_keyword


def _render_search_bar() -> None:
    search_col, action_col, clear_col = st.columns([6, 1, 1], vertical_alignment="bottom")
    with search_col:
        st.text_input(
            "搜索标题",
            key="history_search_input",
            placeholder="输入关键词匹配视频标题，回车可直接搜索",
            on_change=_apply_search_keyword,
        )
    with action_col:
        if st.button("🔍 搜索", use_container_width=True, key="history_search_btn"):
            _apply_search_keyword()
    with clear_col:
        has_search = bool(st.session_state.history_search_keyword or st.session_state.history_search_input)
        if st.button("清空", use_container_width=True, key="history_clear_search_btn", disabled=not has_search):
            _clear_search_keyword()

    active_keyword = str(st.session_state.history_search_keyword).strip()
    if active_keyword:
        st.caption(f"当前筛选：标题包含“{active_keyword}”")


def _apply_search_keyword() -> None:
    keyword = str(st.session_state.get("history_search_input", "")).strip()
    st.session_state.history_search_keyword = keyword
    st.session_state.history_page = 1


def _clear_search_keyword() -> None:
    st.session_state.history_search_input = ""
    st.session_state.history_search_keyword = ""
    st.session_state.history_page = 1


def _load_tasks_page(
    page: int,
    page_size: int,
    title_keyword: Optional[str] = None,
) -> Optional[tuple[list[Task], int]]:
    try:
        total_count = count_tasks(title_keyword=title_keyword)
        if total_count <= 0:
            st.session_state.history_page = 1
            return [], 0

        total_pages = _calculate_total_pages(total_count=total_count, page_size=page_size)
        normalized_page = min(max(int(page), 1), total_pages)
        st.session_state.history_page = normalized_page

        tasks = list_tasks_paginated(
            page=normalized_page,
            page_size=page_size,
            include_content=False,
            title_keyword=title_keyword,
        )
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
        "<th>原链接/BV号</th>"
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

    total_pages = _calculate_total_pages(total_count=total_count, page_size=page_size)
    current_page = max(min(int(st.session_state.history_page), total_pages), 1)
    st.session_state.history_page = current_page

    start_idx = (current_page - 1) * page_size + 1
    end_idx = min(current_page * page_size, total_count)

    spacer_col, size_col, page_col, prev_col, next_col = st.columns([4, 1, 2, 1, 1], vertical_alignment="center")
    with spacer_col:
        st.empty()
    with size_col:
        selected_page_size = st.selectbox(
            "每页条数",
            options=PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(st.session_state.history_page_size),
            key="history_page_size_selector_bottom",
            label_visibility="collapsed",
        )
        if int(selected_page_size) != int(st.session_state.history_page_size):
            st.session_state.history_page_size = int(selected_page_size)
            st.session_state.history_page = 1
            st.rerun()
    with page_col:
        st.caption(f"第 {current_page}/{total_pages} 页 · {start_idx}-{end_idx}/{total_count}")
    with prev_col:
        if st.button("上一页", use_container_width=True, disabled=current_page <= 1, key="history_prev_page"):
            st.session_state.history_page = current_page - 1
            st.rerun()
    with next_col:
        if st.button("下一页", use_container_width=True, disabled=current_page >= total_pages, key="history_next_page"):
            st.session_state.history_page = current_page + 1
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
    safe_source_text = html.escape(_extract_link_label(source_url), quote=False)
    source_cell_html = (
        f'<a href="{safe_source_url}" target="_blank" rel="noopener noreferrer">{safe_source_text}</a>'
        if source_url
        else "<span>-</span>"
    )

    formatted_time = html.escape(_format_created_at(task.created_at), quote=False)
    status_text, status_class = _resolve_status_style(task.status)
    safe_status_text = html.escape(status_text, quote=False)

    detail_link = html.escape(f"/?task_id={task.id}", quote=True)
    delete_link = html.escape(f"?confirm_delete={task.id}", quote=True)
    cancel_link = html.escape("?", quote=True)

    row_html = dedent(
        f"""
        <tr>
          <td class="title-col"><span class="title-text" title="{safe_title}">{safe_title}</span></td>
          <td>{source_cell_html}</td>
          <td>{formatted_time}</td>
          <td><span class="status-tag {status_class}">{safe_status_text}</span></td>
          <td class="action-col">
            <div class="action-group">
              <a class="action-link" href="{detail_link}" target="_self">查看详情</a>
              <details class="delete-popconfirm">
                <summary class="delete-trigger">删除</summary>
                <div class="delete-bubble">
                  <div class="delete-text">确定要删除这条记录吗？</div>
                  <div class="delete-actions">
                    <a class="delete-confirm" href="{delete_link}" target="_self">确定</a>
                    <a class="delete-cancel" href="{cancel_link}" target="_self">取消</a>
                  </div>
                </div>
              </details>
            </div>
          </td>
        </tr>
        """
    ).strip()
    return row_html


def _extract_link_label(source_url: str) -> str:
    if not source_url:
        return "-"
    matched = BV_PATTERN.search(source_url)
    if matched:
        return matched.group(1).upper()
    return "打开原视频"


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


def _handle_pending_delete() -> None:
    pending_delete_id = _consume_int_query_param("confirm_delete")
    if pending_delete_id is None:
        return

    try:
        delete_task(pending_delete_id)
        st.toast(f"🗑️ 已删除记录 #{pending_delete_id}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"删除失败：{exc}")
    st.rerun()


def _consume_int_query_param(param_name: str) -> Optional[int]:
    raw_value: Any = st.query_params.get(param_name)
    if raw_value is None:
        return None
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None

    try:
        value = int(str(raw_value))
    except (TypeError, ValueError):
        value = None

    try:
        del st.query_params[param_name]
    except Exception:  # noqa: BLE001
        pass
    return value if value and value > 0 else None


def _inject_table_styles() -> None:
    st.markdown(
        """
        <style>
        .history-table-wrap {
            border: 1px solid rgba(15, 23, 42, 0.12);
            border-radius: 12px;
            overflow: auto;
            max-height: 72vh;
            background: #ffffff;
        }
        .history-table {
            width: 100%;
            min-width: 900px;
            border-collapse: separate;
            border-spacing: 0;
            table-layout: fixed;
        }
        .history-table th {
            position: sticky;
            top: 0;
            z-index: 4;
            padding: 12px 14px;
            border-bottom: 1px solid #e5e7eb;
            background: #f8fafc;
            text-align: left;
            font-size: 0.88rem;
            font-weight: 600;
            color: #334155;
            white-space: nowrap;
        }
        .history-table td {
            padding: 12px 14px;
            border-bottom: 1px solid #eef2f7;
            background: #ffffff;
            vertical-align: middle;
            color: #111827;
            font-size: 0.92rem;
        }
        .history-table tr:nth-child(even) td {
            background: #fcfdff;
        }
        .history-table .title-col {
            width: 42%;
            max-width: 480px;
        }
        .history-table .title-text {
            display: inline-block;
            max-width: 100%;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            vertical-align: bottom;
        }
        .history-table .status-tag {
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 2px 10px;
            border: 1px solid transparent;
            font-size: 0.80rem;
            font-weight: 600;
            line-height: 1.4;
        }
        .history-table .status-success {
            color: #047857;
            background: #d1fae5;
            border-color: #a7f3d0;
        }
        .history-table .status-failed {
            color: #b91c1c;
            background: #fee2e2;
            border-color: #fecaca;
        }
        .history-table .status-running {
            color: #1d4ed8;
            background: #dbeafe;
            border-color: #bfdbfe;
        }
        .history-table .status-waiting {
            color: #92400e;
            background: #fef3c7;
            border-color: #fde68a;
        }
        .history-table .status-default {
            color: #374151;
            background: #f3f4f6;
            border-color: #e5e7eb;
        }
        .history-table .action-col {
            position: sticky;
            right: 0;
            z-index: 3;
            min-width: 140px;
            width: 140px;
            text-align: right;
            box-shadow: -1px 0 0 rgba(15, 23, 42, 0.08);
            background: #ffffff;
        }
        .history-table tr:nth-child(even) .action-col {
            background: #fcfdff;
        }
        .history-table th.action-col {
            z-index: 5;
            background: #f8fafc;
        }
        .history-table .action-link {
            color: #2563eb;
            text-decoration: none;
            font-weight: 600;
        }
        .history-table .action-link:hover {
            text-decoration: underline;
        }
        .history-table .action-group {
            display: inline-flex;
            align-items: center;
            gap: 10px;
            justify-content: flex-end;
            width: 100%;
        }
        .history-table .delete-popconfirm {
            position: relative;
            display: inline-flex;
        }
        .history-table .delete-popconfirm > summary {
            list-style: none;
        }
        .history-table .delete-popconfirm > summary::-webkit-details-marker {
            display: none;
        }
        .history-table .delete-trigger {
            color: #dc2626;
            cursor: pointer;
            font-weight: 600;
            user-select: none;
        }
        .history-table .delete-trigger:hover {
            text-decoration: underline;
        }
        .history-table .delete-bubble {
            position: absolute;
            right: 0;
            top: calc(100% + 8px);
            min-width: 220px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid #fed7d7;
            background: #fff5f5;
            box-shadow: 0 8px 20px rgba(15, 23, 42, 0.14);
            z-index: 30;
        }
        .history-table .delete-text {
            font-size: 0.82rem;
            color: #7f1d1d;
            margin-bottom: 8px;
            text-align: left;
        }
        .history-table .delete-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            justify-content: flex-end;
        }
        .history-table .delete-confirm,
        .history-table .delete-cancel {
            text-decoration: none;
            font-size: 0.80rem;
            border-radius: 999px;
            padding: 2px 10px;
            border: 1px solid transparent;
            font-weight: 600;
        }
        .history-table .delete-confirm {
            color: #ffffff;
            background: #dc2626;
            border-color: #dc2626;
        }
        .history-table .delete-cancel {
            color: #b91c1c;
            background: #fee2e2;
            border-color: #fecaca;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
