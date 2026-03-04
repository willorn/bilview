"""
工具函数：生成带悬浮提示的复制按钮。

提供可复用的 HTML + JavaScript 代码生成器，避免代码重复。
"""
import html as html_lib
import json
import re


def _sanitize_button_id(raw_id: str) -> str:
    """
    将任意字符串转换为可用于 DOM id / JS 标识符的安全 ID。
    """
    safe = re.sub(r"\W", "_", raw_id)
    if not safe:
        return "copy_btn"
    if safe[0].isdigit():
        return f"copy_{safe}"
    return safe


def create_copy_button_with_tooltip(
    button_id: str,
    text_to_copy: str,
    button_text: str = "复制",
    button_color: str = "#ff4b4b",
    button_hover_color: str = "#ff3333",
    success_message: str = "✓ 已复制到剪贴板",
    error_message: str = "✗ 复制失败",
    success_duration: int = 2000,
    error_duration: int = 3000,
) -> str:
    """
    生成带悬浮提示的复制按钮 HTML。

    Args:
        button_id: 按钮唯一标识符
        text_to_copy: 要复制的文本内容
        button_text: 按钮显示文字
        button_color: 按钮背景色
        button_hover_color: 悬停时背景色
        success_message: 成功提示文字
        error_message: 失败提示文字
        success_duration: 成功提示持续时间（毫秒）
        error_duration: 失败提示持续时间（毫秒）

    Returns:
        完整的 HTML + JavaScript 代码字符串
    """
    safe_id = _sanitize_button_id(button_id)
    escaped_text = json.dumps(text_to_copy)
    escaped_button_text = html_lib.escape(button_text)
    escaped_success_message = json.dumps(success_message)
    escaped_error_message = json.dumps(error_message)
    escaped_button_color = json.dumps(button_color)
    escaped_button_hover_color = json.dumps(button_hover_color)

    # 判断是否使用超链接样式（透明背景 + 蓝色文字）
    is_link_style = button_color == "transparent"
    text_color = "#2563eb" if is_link_style else "white"
    bg_color = "transparent"
    border_style = "none"
    padding_y = "0.25rem" if is_link_style else "0.5rem"
    font_size = "0.875rem" if is_link_style else "1rem"
    text_decoration = "none"
    hover_decoration = "underline"

    markup = f"""
    <div style="width: 100%; position: relative; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <button
            onclick="copyToClipboard_{safe_id}()"
            id="copyBtn_{safe_id}"
            style="
                width: 100%;
                padding: {padding_y} 0.5rem;
                background-color: {bg_color};
                color: {text_color};
                border: {border_style};
                border-radius: 0.25rem;
                cursor: pointer;
                font-size: {font_size};
                font-weight: 500;
                text-decoration: {text_decoration};
                transition: all 0.2s;
            "
            onmouseover="this.style.textDecoration='{hover_decoration}'"
            onmouseout="this.style.textDecoration='{text_decoration}'">
            {escaped_button_text}
        </button>
        <div
            id="tooltip_{safe_id}"
            style="
                margin-top: 0.4rem;
                background-color: #262730;
                color: white;
                padding: 0.35rem 0.65rem;
                border-radius: 0.375rem;
                font-size: 0.8rem;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.3s;
                display: inline-block;
                z-index: 1000;
            "></div>
    </div>
    <script>
    async function copyToClipboard_{safe_id}() {{
        const text = {escaped_text};
        const tooltip = document.getElementById('tooltip_{safe_id}');
        const button = document.getElementById('copyBtn_{safe_id}');
        const originalText = button.textContent;

        function showTooltip(message, color, duration) {{
            tooltip.textContent = message;
            tooltip.style.backgroundColor = color;
            tooltip.style.opacity = '1';
            setTimeout(function() {{
                tooltip.style.opacity = '0';
            }}, duration);
        }}

        try {{
            if (navigator.clipboard && window.isSecureContext) {{
                await navigator.clipboard.writeText(text);
            }} else {{
                // 不满足 clipboard API 条件时，退回旧方案。
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.setAttribute('readonly', '');
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                document.body.appendChild(textarea);
                textarea.select();

                const copied = document.execCommand('copy');
                document.body.removeChild(textarea);
                if (!copied) {{
                    throw new Error('execCommand copy failed');
                }}
            }}

            button.textContent = '✓ 已复制';
            button.style.color = '#0e7c3a';
            button.style.backgroundColor = 'transparent';
            showTooltip({escaped_success_message}, '#0e7c3a', {success_duration});

            setTimeout(function() {{
                button.textContent = originalText;
                button.style.color = '{text_color}';
                button.style.backgroundColor = '{bg_color}';
            }}, {success_duration});
        }} catch (err) {{
            console.error('复制失败:', err);
            showTooltip({escaped_error_message}, '#dc2626', {error_duration});
        }}
    }}
    </script>
    """
    return markup


# 便捷函数：为 Streamlit 任务生成复制按钮
def create_task_copy_button(task_id: int, text_to_copy: str, button_text: str = "复制逐字稿") -> str:
    """
    为 Streamlit 任务生成复制按钮（预设样式）。

    Args:
        task_id: 任务 ID
        text_to_copy: 要复制的文本
        button_text: 按钮文字

    Returns:
        HTML 代码字符串
    """
    return create_copy_button_with_tooltip(
        button_id=str(task_id),
        text_to_copy=text_to_copy,
        button_text=button_text,
        button_color="#ff4b4b",  # Streamlit 主题色
        button_hover_color="#ff3333",
    )
