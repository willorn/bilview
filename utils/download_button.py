from __future__ import annotations

"""
跨端下载按钮生成工具：输出 HTML + JS，兼容移动端与桌面端浏览器。
"""

import html
import json
from typing import Optional


def create_download_button(
    button_id: str,
    content: Optional[str],
    filename: str,
    label: str,
    mime: str = "text/plain",
    button_color: str = "#ff4b4b",
    button_hover_color: str = "#ff3333",
) -> str:
    """
    生成兼容移动端与桌面端的下载按钮 HTML（使用 Blob + 多端 fallback）。

    Args:
        button_id: 按钮唯一标识符（用于 JS 函数名 / DOM id）。
        content: 要下载的文本内容，为空时仍生成按钮但内容为空字符串。
        filename: 下载文件名，包含扩展名。
        label: 按钮显示文字。
        mime: MIME 类型，默认 text/plain。
        button_color: 按钮背景色。
        button_hover_color: 悬停时背景色。

    Returns:
        HTML + JavaScript 字符串，可用 st.markdown 渲染。
    """
    safe_content = json.dumps(content or "")
    safe_filename = json.dumps(filename)
    safe_mime = json.dumps(mime)
    safe_label = html.escape(label)

    # 判断是否使用超链接样式（透明背景 + 蓝色文字）
    is_link_style = button_color == "transparent"
    text_color = "#2563eb" if is_link_style else "white"
    bg_color = "transparent"
    border_style = "none"
    padding_y = "0.25rem" if is_link_style else "0.55rem"
    font_size = "0.875rem" if is_link_style else "1rem"
    font_weight = "500"
    text_decoration = "none"
    hover_decoration = "underline"

    return f"""
    <div style="width: 100%;">
      <button
        id="downloadBtn_{button_id}"
        onclick="downloadFile_{button_id}()"
        style="
          width: 100%;
          padding: {padding_y} 0.5rem;
          background-color: {bg_color};
          color: {text_color};
          border: {border_style};
          border-radius: 0.25rem;
          cursor: pointer;
          font-size: {font_size};
          font-weight: {font_weight};
          text-decoration: {text_decoration};
          transition: all 0.2s;
        "
        onmouseover="this.style.textDecoration='{hover_decoration}'"
        onmouseout="this.style.textDecoration='{text_decoration}'">
        {safe_label}
      </button>
    </div>
    <script>
      function downloadFile_{button_id}() {{
        const content = {safe_content};
        const filename = {safe_filename};
        const mime = {safe_mime};

        try {{
          const blob = new Blob([content], {{ type: mime + ';charset=utf-8' }});
          const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent);

          if (typeof navigator !== 'undefined' && navigator.msSaveOrOpenBlob) {{
            navigator.msSaveOrOpenBlob(blob, filename);
            return;
          }}

          if (isIOS) {{
            const reader = new FileReader();
            reader.onloadend = function () {{
              const link = document.createElement('a');
              link.href = reader.result;
              link.download = filename;
              document.body.appendChild(link);
              link.click();
              document.body.removeChild(link);
            }};
            reader.readAsDataURL(blob);
            return;
          }}

          if (window.URL && window.URL.createObjectURL) {{
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            link.target = '_blank';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            setTimeout(() => window.URL.revokeObjectURL(url), 1200);
            return;
          }}

          // 最后兜底：data URL 打开新窗口，兼容极老旧浏览器
          const dataUrl = 'data:' + mime + ';charset=utf-8,' + encodeURIComponent(content);
          window.open(dataUrl, '_blank');
        }} catch (error) {{
          const message = (error && error.message) ? error.message : error;
          alert('下载失败：' + message);
        }}
      }}
    </script>
    """
