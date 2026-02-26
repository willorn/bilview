"""
工具函数：生成带悬浮提示的复制按钮。

提供可复用的 HTML + JavaScript 代码生成器，避免代码重复。
"""
from typing import Optional


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
    # 安全转义文本
    escaped_text = repr(text_to_copy)

    html = f"""
    <div style="position: relative;">
        <button
            onclick="copyToClipboard_{button_id}()"
            id="copyBtn_{button_id}"
            style="
                width: 100%;
                padding: 0.5rem 1rem;
                background-color: {button_color};
                color: white;
                border: none;
                border-radius: 0.5rem;
                cursor: pointer;
                font-size: 1rem;
                font-weight: 500;
                transition: background-color 0.2s;
            "
            onmouseover="this.style.backgroundColor='{button_hover_color}'"
            onmouseout="this.style.backgroundColor='{button_color}'">
            {button_text}
        </button>
        <div
            id="tooltip_{button_id}"
            style="
                position: absolute;
                bottom: 110%;
                left: 50%;
                transform: translateX(-50%);
                background-color: #262730;
                color: white;
                padding: 0.5rem 1rem;
                border-radius: 0.375rem;
                font-size: 0.875rem;
                white-space: nowrap;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.3s;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
                z-index: 1000;
            "></div>
    </div>
    <script>
    function copyToClipboard_{button_id}() {{
        const text = {escaped_text};
        const tooltip = document.getElementById('tooltip_{button_id}');
        const button = document.getElementById('copyBtn_{button_id}');

        navigator.clipboard.writeText(text).then(
            function() {{
                // 成功提示
                tooltip.textContent = '{success_message}';
                tooltip.style.backgroundColor = '#0e7c3a';
                tooltip.style.opacity = '1';

                // 按钮反馈
                const originalText = button.textContent;
                button.textContent = '✓ 已复制';
                button.style.backgroundColor = '#0e7c3a';

                // 自动恢复
                setTimeout(function() {{
                    tooltip.style.opacity = '0';
                    button.textContent = originalText;
                    button.style.backgroundColor = '{button_color}';
                }}, {success_duration});
            }},
            function(err) {{
                // 失败提示
                tooltip.textContent = '{error_message}';
                tooltip.style.backgroundColor = '#dc2626';
                tooltip.style.opacity = '1';

                setTimeout(function() {{
                    tooltip.style.opacity = '0';
                }}, {error_duration});
            }}
        );
    }}
    </script>
    """
    return html


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
