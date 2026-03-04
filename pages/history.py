"""
历史记录页：以表格结构展示任务数据。
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlencode

import streamlit as st
import pandas as pd

from db.database import (
    Task,
    TaskStatus,
    delete_task,
    init_db,
    list_tasks_paginated_with_total,
)

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
    _ensure_pagination_state()

    if st.button("← 返回工作台"):
        st.switch_page("app.py")

    st.title("历史记录")
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
    col1, col2 = st.columns([2, 4])
    with col1:
        st.text_input(
            "搜索",
            key="history_search_input",
            placeholder="搜索视频标题...",
            on_change=_apply_search_keyword,
            label_visibility="collapsed",
        )

    active_keyword = str(st.session_state.history_search_keyword).strip()
    if active_keyword:
        st.caption(f'当前筛选："{active_keyword}"')


def _apply_search_keyword() -> None:
    keyword = str(st.session_state.get("history_search_input", "")).strip()
    st.session_state.history_search_keyword = keyword
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
    except Exception as exc:
        st.warning(f"数据库暂不可用：{exc}")
        if st.button("初始化数据库"):
            try:
                init_db()
                st.success("数据库初始化完成")
                st.rerun()
            except Exception as init_exc:
                st.error(f"初始化失败：{init_exc}")
        return None


def _render_history_table(tasks: list[Task]) -> None:
    """使用原生Streamlit组件渲染表格，每行数据配操作链接"""
    # 表头
    cols = st.columns([4, 2, 1.5, 1, 1, 1])
    headers = ["视频标题", "原链接", "处理时间", "状态", "查看", "删除"]
    for col, header in zip(cols, headers):
        with col:
            st.text(header)

    st.divider()

    # 数据行
    for task in tasks:
        cols = st.columns([4, 2, 1.5, 1, 1, 1])

        # 视频标题
        with cols[0]:
            title = task.video_title.strip() if task.video_title else "未命名视频"
            st.text(title[:40] + "..." if len(title) > 40 else title)

        # 原链接
        with cols[1]:
            bv_id = _extract_bv_id(task.bilibili_url)
            if task.bilibili_url:
                st.markdown(f"<a href='{task.bilibili_url}' target='_blank' style='color: #0066cc; text-decoration: none;'>{bv_id}</a>", unsafe_allow_html=True)
            else:
                st.text("-")

        # 处理时间
        with cols[2]:
            st.text(_format_created_at(task.created_at))

        # 状态
        with cols[3]:
            st.text(_format_status(task.status))

        # 查看链接
        with cols[4]:
            st.markdown(f"<a href='/?task_id={task.id}' target='_self' style='color: #0066cc; text-decoration: none;'>查看</a>", unsafe_allow_html=True)

        # 删除链接
        with cols[5]:
            st.markdown(f"<a href='?delete_task={task.id}' target='_self' style='color: #0066cc; text-decoration: none;'>删除</a>", unsafe_allow_html=True)


def _render_pagination(total_count: int, page_size: int) -> None:
    if total_count <= 0:
        return

    normalized_page_size = _normalize_page_size(page_size)
    total_pages = _calculate_total_pages(total_count=total_count, page_size=normalized_page_size)
    current_page = max(min(int(st.session_state.history_page), total_pages), 1)
    st.session_state.history_page = current_page

    start_idx = (current_page - 1) * normalized_page_size + 1
    end_idx = min(current_page * normalized_page_size, total_count)

    st.divider()
    
    # 分页控制区域
    col1, col2, col3, col4, col5 = st.columns([1.5, 2, 3, 1.5, 1.5])
    
    with col1:
        st.selectbox(
            "每页条数",
            options=PAGE_SIZE_OPTIONS,
            index=PAGE_SIZE_OPTIONS.index(normalized_page_size),
            key="page_size",
            label_visibility="collapsed",
            on_change=lambda: _change_page_size(),
        )
    
    with col2:
        st.caption(f"显示 {start_idx}-{end_idx} 条，共 {total_count} 条")
    
    with col3:
        # 页码输入和跳转
        page_cols = st.columns([2, 1, 2])
        with page_cols[0]:
            if st.button("◀", disabled=current_page <= 1):
                st.session_state.history_page = current_page - 1
                _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
                st.rerun()
        with page_cols[1]:
            st.caption(f"{current_page}/{total_pages}")
        with page_cols[2]:
            if st.button("▶", disabled=current_page >= total_pages):
                st.session_state.history_page = current_page + 1
                _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
                st.rerun()
    
    with col4:
        # 直接跳转到指定页
        goto_page = st.number_input(
            "跳转",
            min_value=1,
            max_value=total_pages,
            value=current_page,
            key="goto_page",
            label_visibility="collapsed",
        )
    
    with col5:
        if st.button("跳转"):
            if int(goto_page) != current_page:
                st.session_state.history_page = int(goto_page)
                _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
                st.rerun()


def _change_page_size():
    st.session_state.history_page_size = int(st.session_state.page_size)
    st.session_state.history_page = 1
    _sync_history_query_params(pending_delete_id=_peek_delete_task_id())
    st.rerun()


def _calculate_total_pages(total_count: int, page_size: int) -> int:
    if total_count <= 0:
        return 1
    return max(math.ceil(total_count / max(int(page_size), 1)), 1)


def _extract_bv_id(source_url: str) -> str:
    if not source_url:
        return "-"
    matched = BV_PATTERN.search(source_url)
    if matched:
        bv = matched.group(1).upper()
        return bv[:12] if len(bv) > 12 else bv
    return "链接"


def _format_status(status: str) -> str:
    status_map = {
        TaskStatus.COMPLETED.value: "成功",
        TaskStatus.FAILED.value: "失败",
        TaskStatus.WAITING.value: "等待",
        TaskStatus.DOWNLOADING.value: "下载",
        TaskStatus.TRANSCRIBING.value: "转录",
        TaskStatus.SUMMARIZING.value: "总结",
    }
    return status_map.get(status, status)


def _format_created_at(raw_time: str) -> str:
    if not raw_time:
        return "-"
    normalized = raw_time.strip().replace("T", " ").replace("Z", "")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.strftime("%m-%d %H:%M")
    except ValueError:
        return normalized[:16]


def _handle_pending_delete() -> None:
    pending_delete_id = _peek_delete_task_id()
    if pending_delete_id is None:
        return

    st.warning(f"将删除记录 #{pending_delete_id}，此操作不可恢复。")
    col1, col2 = st.columns([1, 5])
    with col1:
        if st.button("确认删除", key=f"confirm_{pending_delete_id}"):
            try:
                delete_task(pending_delete_id)
                st.success(f"已删除记录 #{pending_delete_id}")
                _sync_history_query_params(pending_delete_id=None)
                st.rerun()
            except Exception as exc:
                st.error(f"删除失败：{exc}")
    with col2:
        if st.button("取消", key=f"cancel_{pending_delete_id}"):
            _sync_history_query_params(pending_delete_id=None)
            st.rerun()


def _peek_delete_task_id() -> Optional[int]:
    return _read_positive_int_query_param(QUERY_DELETE_KEY)


def _read_positive_int_query_param(param_name: str) -> Optional[int]:
    raw_value = st.query_params.get(param_name)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else None
    if raw_value is None:
        return None
    try:
        value = int(str(raw_value))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _read_text_query_param(param_name: str) -> str:
    raw_value = st.query_params.get(param_name)
    if isinstance(raw_value, list):
        raw_value = raw_value[0] if raw_value else ""
    return str(raw_value or "").strip()


def _normalize_page_size(page_size: Any) -> int:
    try:
        value = int(page_size)
        return value if value in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE


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

    current_params = {k: str(v) for k, v in st.query_params.items()}
    reserved_keys = {QUERY_PAGE_KEY, QUERY_PAGE_SIZE_KEY, QUERY_SEARCH_KEY, QUERY_DELETE_KEY}
    passthrough_params = {k: v for k, v in current_params.items() if k not in reserved_keys}
    merged_params = {**passthrough_params, **target_params}

    if current_params != merged_params:
        for key in list(st.query_params.keys()):
            del st.query_params[key]
        for key, value in merged_params.items():
            st.query_params[key] = value


if __name__ == "__main__":
    main()
