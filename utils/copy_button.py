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
    manual_message: str = "⚠️ 自动复制受限，请长按后手动复制",
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
        manual_message: 自动复制受限时的手动复制提示
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
    escaped_manual_message = json.dumps(manual_message)
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
    <style>
    html, body {{
        margin: 0;
        padding: 0;
    }}
    </style>
    <div style="display: inline-block; width: fit-content; position: relative; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
        <button
            onclick="copyToClipboard_{safe_id}(event)"
            ontouchend="copyToClipboard_{safe_id}(event)"
            id="copyBtn_{safe_id}"
            type="button"
            style="
                width: auto;
                padding: {padding_y} 0.5rem;
                background-color: {bg_color};
                color: {text_color};
                border: {border_style};
                border-radius: 0.25rem;
                cursor: pointer;
                font-size: {font_size};
                font-weight: 500;
                white-space: nowrap;
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
                position: absolute;
                bottom: calc(100% + 0.35rem);
                left: 50%;
                transform: translateX(-50%);
                background-color: #262730;
                color: white;
                padding: 0.35rem 0.65rem;
                border-radius: 0.375rem;
                font-size: 0.8rem;
                white-space: nowrap;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.3s;
                display: block;
                z-index: 1000;
            "></div>
    </div>
    <script>
    async function copyToClipboard_{safe_id}(event) {{
        const dedupeKey = '__copy_btn_lock_{safe_id}';
        const lastInvoke = Number(window[dedupeKey] || 0);
        const now = Date.now();
        if (now - lastInvoke < 650) {{
            return;
        }}
        window[dedupeKey] = now;

        if (event) {{
            event.preventDefault();
            event.stopPropagation();
        }}

        const text = {escaped_text};
        const tooltip = document.getElementById('tooltip_{safe_id}');
        const button = document.getElementById('copyBtn_{safe_id}');

        function getCopyHostContext() {{
            try {{
                if (window.parent && window.parent.document && window.parent.navigator) {{
                    return {{
                        doc: window.parent.document,
                        win: window.parent,
                        nav: window.parent.navigator,
                    }};
                }}
            }} catch (e) {{
                // 访问父窗口受限时降级到当前 iframe 上下文
            }}
            return {{ doc: document, win: window, nav: navigator }};
        }}

        function getToastHostDocument() {{
            try {{
                if (window.parent && window.parent.document) {{
                    return window.parent.document;
                }}
            }} catch (e) {{
                // 访问父窗口受限时降级到当前 iframe 文档
            }}
            return document;
        }}

        function showGlobalToast(message, color, duration) {{
            const hostDoc = getToastHostDocument();
            const styleId = 'global_copy_toast_style';
            const containerId = 'global_copy_toast_container';

            if (!hostDoc.getElementById(styleId)) {{
                const styleEl = hostDoc.createElement('style');
                styleEl.id = styleId;
                styleEl.textContent = `
                    #${{containerId}} {{
                        position: fixed;
                        top: 18px;
                        left: 50%;
                        transform: translateX(-50%);
                        z-index: 2147483000;
                        pointer-events: none;
                        display: flex;
                        flex-direction: column;
                        align-items: center;
                        gap: 8px;
                    }}
                    #${{containerId}} .global-copy-toast-item {{
                        color: #fff;
                        padding: 0.5rem 0.85rem;
                        border-radius: 999px;
                        font-size: 0.85rem;
                        font-weight: 600;
                        line-height: 1.1;
                        white-space: nowrap;
                        box-shadow: 0 8px 22px rgba(0, 0, 0, 0.18);
                        opacity: 0;
                        transform: translateY(-6px);
                        transition: opacity 0.18s ease, transform 0.18s ease;
                    }}
                    #${{containerId}} .global-copy-toast-item.show {{
                        opacity: 1;
                        transform: translateY(0);
                    }}
                `;
                hostDoc.head.appendChild(styleEl);
            }}

            let container = hostDoc.getElementById(containerId);
            if (!container) {{
                container = hostDoc.createElement('div');
                container.id = containerId;
                hostDoc.body.appendChild(container);
            }}

            const toast = hostDoc.createElement('div');
            toast.className = 'global-copy-toast-item';
            toast.textContent = message;
            toast.style.backgroundColor = color;
            container.appendChild(toast);

            requestAnimationFrame(function() {{
                toast.classList.add('show');
            }});

            setTimeout(function() {{
                toast.classList.remove('show');
                setTimeout(function() {{
                    if (toast.parentNode) {{
                        toast.parentNode.removeChild(toast);
                    }}
                }}, 220);
            }}, duration);
        }}

        function showTooltip(message, color, duration) {{
            tooltip.textContent = message;
            tooltip.style.backgroundColor = color;
            tooltip.style.opacity = '1';
            setTimeout(function() {{
                tooltip.style.opacity = '0';
            }}, duration);
        }}

        async function copyByClipboardApi(targetNav, targetWin) {{
            if (!targetNav || !targetNav.clipboard) {{
                return false;
            }}
            if (targetWin && targetWin.isSecureContext === false) {{
                return false;
            }}
            try {{
                await targetNav.clipboard.writeText(text);
                return true;
            }} catch (e) {{
                return false;
            }}
        }}

        function copyByExecCommandTextarea(targetDoc) {{
            if (!targetDoc || !targetDoc.body) {{
                return false;
            }}

            const textarea = targetDoc.createElement('textarea');
            textarea.value = text;
            textarea.setAttribute('readonly', '');
            textarea.style.position = 'fixed';
            textarea.style.top = '0';
            textarea.style.left = '0';
            textarea.style.width = '1px';
            textarea.style.height = '1px';
            textarea.style.opacity = '0';
            textarea.style.pointerEvents = 'none';
            targetDoc.body.appendChild(textarea);

            const selection = targetDoc.getSelection ? targetDoc.getSelection() : null;
            let selectedRange = null;
            if (selection && selection.rangeCount > 0) {{
                selectedRange = selection.getRangeAt(0);
            }}

            textarea.focus({{ preventScroll: true }});
            textarea.select();
            textarea.setSelectionRange(0, textarea.value.length);

            let copied = false;
            try {{
                copied = targetDoc.execCommand('copy');
            }} catch (e) {{
                copied = false;
            }}

            targetDoc.body.removeChild(textarea);
            if (selection) {{
                selection.removeAllRanges();
                if (selectedRange) {{
                    selection.addRange(selectedRange);
                }}
            }}
            return copied;
        }}

        function copyByExecCommandRange(targetDoc) {{
            if (!targetDoc || !targetDoc.body) {{
                return false;
            }}

            const copyNode = targetDoc.createElement('div');
            copyNode.setAttribute('contenteditable', 'true');
            copyNode.setAttribute('aria-hidden', 'true');
            copyNode.textContent = text;
            copyNode.style.position = 'fixed';
            copyNode.style.top = '0';
            copyNode.style.left = '0';
            copyNode.style.opacity = '0';
            copyNode.style.pointerEvents = 'none';
            copyNode.style.whiteSpace = 'pre-wrap';
            targetDoc.body.appendChild(copyNode);

            const selection = targetDoc.getSelection ? targetDoc.getSelection() : null;
            const range = targetDoc.createRange ? targetDoc.createRange() : null;
            if (!selection || !range) {{
                targetDoc.body.removeChild(copyNode);
                return false;
            }}

            selection.removeAllRanges();
            range.selectNodeContents(copyNode);
            selection.addRange(range);
            copyNode.focus({{ preventScroll: true }});

            let copied = false;
            try {{
                copied = targetDoc.execCommand('copy');
            }} catch (e) {{
                copied = false;
            }}

            selection.removeAllRanges();
            targetDoc.body.removeChild(copyNode);
            return copied;
        }}

        function showManualCopyDialog(targetDoc) {{
            if (!targetDoc || !targetDoc.body) {{
                return;
            }}

            const styleId = 'global_manual_copy_dialog_style';
            if (!targetDoc.getElementById(styleId)) {{
                const styleEl = targetDoc.createElement('style');
                styleEl.id = styleId;
                styleEl.textContent = `
                    .global-manual-copy-overlay {{
                        position: fixed;
                        inset: 0;
                        background: rgba(0, 0, 0, 0.55);
                        z-index: 2147483001;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        padding: 16px;
                    }}
                    .global-manual-copy-panel {{
                        width: min(520px, 100%);
                        max-height: 80vh;
                        background: #ffffff;
                        border-radius: 12px;
                        box-shadow: 0 14px 36px rgba(0, 0, 0, 0.24);
                        padding: 12px;
                        display: flex;
                        flex-direction: column;
                        gap: 8px;
                    }}
                    .global-manual-copy-title {{
                        margin: 0;
                        font-size: 15px;
                        font-weight: 600;
                        color: #111827;
                    }}
                    .global-manual-copy-tip {{
                        margin: 0;
                        font-size: 13px;
                        color: #374151;
                    }}
                    .global-manual-copy-text {{
                        width: 100%;
                        min-height: 128px;
                        max-height: 48vh;
                        border: 1px solid #d1d5db;
                        border-radius: 8px;
                        padding: 8px;
                        font-size: 14px;
                        line-height: 1.4;
                        resize: vertical;
                        box-sizing: border-box;
                        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    }}
                    .global-manual-copy-actions {{
                        display: flex;
                        justify-content: flex-end;
                    }}
                    .global-manual-copy-close {{
                        border: none;
                        border-radius: 8px;
                        padding: 8px 12px;
                        font-size: 14px;
                        font-weight: 600;
                        color: #ffffff;
                        background: #2563eb;
                    }}
                `;
                targetDoc.head.appendChild(styleEl);
            }}

            const existing = targetDoc.getElementById('global_manual_copy_overlay');
            if (existing && existing.parentNode) {{
                existing.parentNode.removeChild(existing);
            }}

            const overlay = targetDoc.createElement('div');
            overlay.id = 'global_manual_copy_overlay';
            overlay.className = 'global-manual-copy-overlay';

            const panel = targetDoc.createElement('div');
            panel.className = 'global-manual-copy-panel';
            overlay.appendChild(panel);

            const title = targetDoc.createElement('p');
            title.className = 'global-manual-copy-title';
            title.textContent = '手动复制';
            panel.appendChild(title);

            const tip = targetDoc.createElement('p');
            tip.className = 'global-manual-copy-tip';
            tip.textContent = '自动复制失败，请长按下面文本并选择“复制”。';
            panel.appendChild(tip);

            const textarea = targetDoc.createElement('textarea');
            textarea.className = 'global-manual-copy-text';
            textarea.readOnly = true;
            textarea.value = text;
            panel.appendChild(textarea);

            const actionWrap = targetDoc.createElement('div');
            actionWrap.className = 'global-manual-copy-actions';
            panel.appendChild(actionWrap);

            const closeBtn = targetDoc.createElement('button');
            closeBtn.type = 'button';
            closeBtn.className = 'global-manual-copy-close';
            closeBtn.textContent = '关闭';
            actionWrap.appendChild(closeBtn);

            function closeDialog() {{
                if (overlay.parentNode) {{
                    overlay.parentNode.removeChild(overlay);
                }}
            }}

            closeBtn.addEventListener('click', closeDialog);
            overlay.addEventListener('click', function (e) {{
                if (e.target === overlay) {{
                    closeDialog();
                }}
            }});

            targetDoc.body.appendChild(overlay);
            setTimeout(function() {{
                textarea.focus({{ preventScroll: true }});
                textarea.select();
                textarea.setSelectionRange(0, textarea.value.length);
            }}, 30);
        }}

        function isIOSLike(navObj) {{
            const nav = navObj || navigator;
            const ua = (nav.userAgent || '').toLowerCase();
            const iOSUA = /iphone|ipad|ipod/.test(ua);
            const iPadOS = nav.platform === 'MacIntel' && nav.maxTouchPoints > 1;
            return iOSUA || iPadOS;
        }}

        function runExecCommandStrategies(targetDoc, preferRangeFirst) {{
            if (preferRangeFirst) {{
                return copyByExecCommandRange(targetDoc) || copyByExecCommandTextarea(targetDoc);
            }}
            return copyByExecCommandTextarea(targetDoc) || copyByExecCommandRange(targetDoc);
        }}

        function showManualCopyPrompt(targetDoc) {{
            try {{
                showManualCopyDialog(targetDoc);
            }} catch (e) {{
                try {{
                    window.prompt('自动复制受限，请手动复制以下内容：', text);
                }} catch (promptError) {{
                    // 某些 WebView 可能禁用 prompt，保持静默
                }}
            }}
        }}

        try {{
            const hostCtx = getCopyHostContext();
            let copied = false;
            const preferRangeFirst = isIOSLike(navigator) || isIOSLike(hostCtx.nav);

            // 先走同步 execCommand 方案，尽量留在用户点击手势链路内（移动端更稳）。
            copied = runExecCommandStrategies(document, preferRangeFirst);
            if (!copied && hostCtx.doc !== document) {{
                copied = runExecCommandStrategies(hostCtx.doc, preferRangeFirst);
            }}

            // 再尝试异步 Clipboard API。
            if (!copied) {{
                copied = await copyByClipboardApi(navigator, window);
            }}
            if (!copied && hostCtx.win !== window) {{
                copied = await copyByClipboardApi(hostCtx.nav, hostCtx.win);
            }}

            if (!copied) {{
                showManualCopyPrompt(hostCtx.doc || document);
                try {{
                    showGlobalToast({escaped_manual_message}, '#d97706', {error_duration});
                }} catch (toastError) {{
                    showTooltip({escaped_manual_message}, '#d97706', {error_duration});
                }}
                return;
            }}

            button.style.color = '#0e7c3a';
            button.style.backgroundColor = 'transparent';
            try {{
                showGlobalToast({escaped_success_message}, '#0e7c3a', {success_duration});
            }} catch (toastError) {{
                showTooltip({escaped_success_message}, '#0e7c3a', {success_duration});
            }}

            setTimeout(function() {{
                button.style.color = '{text_color}';
                button.style.backgroundColor = '{bg_color}';
            }}, {success_duration});
        }} catch (err) {{
            console.error('复制失败:', err);
            try {{
                showGlobalToast({escaped_error_message}, '#dc2626', {error_duration});
            }} catch (toastError) {{
                showTooltip({escaped_error_message}, '#dc2626', {error_duration});
            }}
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
