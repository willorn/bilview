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
from yt_dlp import YoutubeDL

from utils.network import get_lan_addresses
from config import DOWNLOAD_DIR, ensure_api_key_present
from core.downloader import download_audio
from core.summarizer import generate_summary
from core.transcriber import audio_to_text
from db.database import (
    DEFAULT_DB_PATH,
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
    try:
        ensure_api_key_present()
    except Exception as exc:  # noqa: BLE001
        st.error(str(exc))
        return
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

    options = {t.id: f"#{t.id} | {STATUS_MAP.get(t.status, t.status)} | {t.video_title or '未命名'}" for t in tasks}
    selected_id = st.selectbox(
        "选择任务查看详情",
        options=list(options.keys()),
        format_func=lambda tid: options.get(tid, str(tid)),
    )
    task = get_task(selected_id)
    if not task:
        st.warning("任务不存在")
        return

    if not task.video_title:
        if st.button("重新获取标题", use_container_width=True, type="secondary"):
            _refresh_title(task.id, task.bilibili_url)

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
            st.button("复制逐字稿", on_click=_copy_to_clipboard, args=(task.transcript_text,), use_container_width=True, key=f"copy_transcript_{task.id}")
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

    if not task.video_title:
        if st.button("重新获取标题", use_container_width=True, type="secondary"):
            _refresh_title(task.id, task.bilibili_url)
    if task.status in {TaskStatus.SUMMARIZING.value, TaskStatus.COMPLETED.value, TaskStatus.FAILED.value}:
        if st.button("重新生成总结", use_container_width=True, type="primary"):
            _regenerate_summary(task)


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


def _regenerate_summary(task: Task) -> None:
    """使用已存转录重新生成总结。"""
    if not task.transcript_text:
        st.error("暂无转录文本，无法生成总结")
        return
    try:
        update_task_status(task.id, TaskStatus.SUMMARIZING.value)
        summary = generate_summary(task.transcript_text, system_prompt=_get_active_prompt())
        update_task_content(task.id, summary_text=summary)
        update_task_status(task.id, TaskStatus.COMPLETED.value)
        st.success("总结已重新生成")
    except Exception as exc:  # noqa: BLE001
        update_task_status(task.id, TaskStatus.FAILED.value)
        st.error(f"重新生成失败：{exc}")


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


def _copy_to_clipboard(text: str) -> None:
    # Streamlit 无法直接写客户端剪贴板，这里将内容放入 session_state，便于前端自定义 JS 读取。
    st.session_state["clipboard_text"] = text


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
