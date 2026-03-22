"""
模块描述：使用 yt-dlp 仅提取 B 站视频的音频流并保存至本地。

功能要点：
1. 仅下载最佳音质的音频流（默认提取为 M4A）。
2. 自动创建 downloads 目录并生成规范文件名。
3. 预留 cookie 文件支持，处理需要登录态的会员视频。
4. 自动重试机制：区分网络临时性错误和永久性错误，使用指数退避策略。

@author 开发
@date 2026-02-23
@version v1.1 (新增重试机制)
"""
from __future__ import annotations

import logging
import re
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, Union

from yt_dlp import YoutubeDL
from utils.file_helper import ensure_dir
from utils.retry_helper import download_retry_decorator

logger = logging.getLogger(__name__)

DOWNLOAD_DIR_NAME = "downloads"
PREFERRED_CODEC = "m4a"
PREFERRED_QUALITY = "192"
DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / DOWNLOAD_DIR_NAME
DEFAULT_COOKIE_FILE = Path("cookie.txt")

# cookies 持久化目录（优先使用 /data 持久卷）
COOKIES_DIR = Path("/data") if Path("/data").exists() else Path(__file__).resolve().parent.parent / "cookies"
COOKIE_FILE = COOKIES_DIR / "bilibili_cookies.txt"

# B站扫码登录相关（新版 API）
BILI_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
BILI_QR_LOGIN_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
BILI_OAUTH_KEY_HEADER = "https://passport.bilibili.com"
COOKIE_RECEIVE_PORT = 9988


def _get_auth_cookie_path() -> Optional[Path]:
    # 优先使用已上传的 cookies
    if COOKIE_FILE.is_file():
        return COOKIE_FILE
    # 回退到 cookie.txt
    if DEFAULT_COOKIE_FILE.is_file():
        return DEFAULT_COOKIE_FILE
    return None


def save_uploaded_cookies(content: str) -> Path:
    """保存用户上传的 cookies 文件。"""
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(content, encoding="utf-8")
    return COOKIE_FILE


def has_bilibili_cookies() -> bool:
    """检查是否有已保存的 Bilibili cookies。"""
    return COOKIE_FILE.is_file() or DEFAULT_COOKIE_FILE.is_file()


def generate_bilibili_qr() -> dict:
    """请求B站生成扫码登录二维码。返回 {'oauth_key': ..., 'url': ...}"""
    import json, urllib.request

    payload = json.dumps({
        "app_id": 1,
        "local_id": "xxxx",
        "device": "pc",
        "platform": "web",
        "type": 2,
        "scopes": "login",
        "source": "main",
    }).encode()

    req = urllib.request.Request(
        BILI_QR_GENERATE_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())

    if result.get("code") != 0:
        raise RuntimeError(f"生成二维码失败：{result}")

    data = result["data"]
    return {"oauth_key": data["oauthKey"], "url": data["url"]}


def check_bilibili_login_status(oauth_key: str) -> dict:
    """查询扫码登录状态。返回 {'code': ..., 'message': ...}"""
    import json, urllib.request

    payload = json.dumps({
        "oauthKey": oauth_key,
        "source": "main",
        "scopes": "login",
    }).encode()

    req = urllib.request.Request(
        BILI_QR_LOGIN_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://www.bilibili.com/",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())

    # code: 86100=待扫码, 86038=已扫码待确认, 86090=已确认, -2=超时
    code = result.get("data", {}).get("code", result.get("code", -1))
    url = result.get("data", {}).get("url", "")

    cookies = ""
    if code == 86090 and url:
        cookies = _extract_cookies_from_callback(url)

    messages = {
        86100: "等待扫码",
        86038: "已扫码，请在手机端确认",
        86090: "登录成功",
        -2: "二维码已过期",
        -1: "登录取消",
    }

    return {
        "code": code,
        "message": messages.get(code, f"未知状态({code})"),
        "cookies": cookies,
    }


def _extract_cookies_from_callback(url: str) -> str:
    """从登录回调 URL 中提取 cookies，格式化为 Netscape 格式。"""
    import time, re
    from urllib.parse import parse_qs, urlparse

    cookies: dict = {}
    # 从 hash 参数中提取
    parsed = urlparse(url)
    if parsed.fragment:
        for part in parsed.fragment.split("&"):
            if "=" in part:
                k, _, v = part.partition("=")
                k = k.strip()
                if k in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
                    cookies[k] = v

    # 从 query 参数中提取
    if not cookies:
        query = parse_qs(parsed.query)
        for k, vals in query.items():
            if k in ("SESSDATA", "bili_jct", "DedeUserID", "bili_jct"):
                cookies[k] = vals[0]

    # 从 URL 字符串中直接正则匹配
    if not cookies:
        for pat in (r"SESSDATA=([^&]+)", r"bili_jct=([^&]+)", r"DedeUserID=([^&]+)"):
            m = re.search(pat, url)
            if m:
                cookies[m.group(0).split("=")[0]] = m.group(1)

    if not cookies:
        return ""

    now = int(time.time())
    expire = now + 25 * 24 * 3600
    lines = ["# Netscape HTTP Cookie File", "# Generated by BilView"]
    for name, value in cookies.items():
        lines.append(f".bilibili.com\tTRUE\t/\tTRUE\t{expire}\t{name}\t{value}")
    return "\n".join(lines)


class _CookieReceiver:
    """内嵌 HTTP 服务，接收扫码登录后的 cookies。"""

    def __init__(self):
        import threading
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self.cookies_received = ""
        self.ready = False

    def _make_handler(self):
        import http.server, json, socketserver

        me = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                # 返回轮询页面（浏览器扫码后访问此 URL）
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    "<html><body style='font-family:sans-serif;text-align:center;padding:40px'>"
                    "<h2>&#10004; 已收到登录确认</h2>"
                    "<p>Cookies 已保存，可以关闭此页面了。</p>"
                    "<p>请回到 BilView 应用页面查看结果。</p>"
                    "</body></html>".encode("utf-8")
                )

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    data = json.loads(body)
                    me.cookies_received = data.get("cookies", "")
                    if me.cookies_received:
                        save_uploaded_cookies(me.cookies_received)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except Exception:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"ok":false}')

            def log_message(self, *_):
                pass  # 关闭日志

        return Handler

    def start(self):
        import http.server, socketserver, threading

        socketserver.TCPServer.allow_reuse_address = True
        self._server = socketserver.TCPServer(("", COOKIE_RECEIVE_PORT), self._make_handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.ready = True

    def stop(self):
        if self._server:
            self._server.shutdown()
            self._server = None


_cookie_receiver: Optional[_CookieReceiver] = None


def get_cookie_receiver() -> _CookieReceiver:
    global _cookie_receiver
    if _cookie_receiver is None:
        _cookie_receiver = _CookieReceiver()
        _cookie_receiver.start()
    return _cookie_receiver


def get_cookie_receive_url() -> str:
    """返回接收 cookies 的 URL，供浏览器在扫码确认后回调。"""
    return f"http://localhost:{COOKIE_RECEIVE_PORT}/save"


INVALID_FILENAME_CHARS = r'[^a-zA-Z0-9\\-_\\.]'
ANSI_ESCAPE_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
SHORT_URL_DOMAIN = "b23.tv"
DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Origin": "https://www.bilibili.com",
}


@download_retry_decorator
def download_audio(
    url: str,
    download_dir: Path | str = DEFAULT_DOWNLOAD_DIR,
    cookie_file: Optional[Path | str] = DEFAULT_COOKIE_FILE,
    return_info: bool = False,
) -> Union[Path, Tuple[Path, dict]]:
    """
    下载指定 B 站链接的音频流并返回本地文件路径。

    自动重试策略：
    - 最大重试 3 次
    - 指数退避：2-30 秒
    - 可重试错误：网络超时、连接失败、临时不可用
    - 不可重试错误：视频不存在、权限问题、地区限制

    Args:
        url: B 站视频链接。
        download_dir: 音频保存目录，默认使用项目根目录下 downloads/。
        cookie_file: 可选的 cookie 文件路径，用于会员或受限视频。
        return_info: True 时同时返回 yt-dlp 抽取的 info 字典。

    Returns:
        下载完成后的音频文件绝对路径；若 return_info=True，则返回 (路径, info)。

    Raises:
        RuntimeError: 下载或后处理失败时抛出异常。
    """
    target_dir = ensure_dir(download_dir)
    normalized_url = _normalize_bilibili_url(url)
    cookie_path = _resolve_cookie_file(cookie_file)

    ydl_opts = _build_options(target_dir, cookie_path)

    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalized_url, download=True)
            # yt-dlp 会在后处理阶段将扩展名替换为首选编解码格式
            raw_path = Path(ydl.prepare_filename(info))
            final_path = raw_path.with_suffix(f".{PREFERRED_CODEC}")
            logger.info("音频下载成功: %s", final_path)
            return (final_path, info) if return_info else final_path
    except Exception as exc:  # noqa: BLE001
        clean_error = _sanitize_exception_message(str(exc))
        logger.warning("音频下载失败: %s", clean_error)
        raise RuntimeError(
            _build_download_error_message(
                error_text=clean_error,
                has_cookie=bool(cookie_path),
                source_url=normalized_url,
            )
        ) from exc


def _resolve_cookie_file(cookie_file: Optional[Path | str]) -> Optional[Path]:
    """解析 cookie 文件路径，存在时返回绝对路径。优先使用扫码登录的 Cookies。"""
    # 1. 优先使用扫码登录保存的 cookies
    auth_path = _get_auth_cookie_path()
    if auth_path and auth_path.is_file():
        return auth_path

    # 2. 其次使用指定的 cookie 文件
    if cookie_file is None:
        return None
    candidate = Path(cookie_file).expanduser().resolve()
    if candidate.is_file():
        return candidate
    return None


def _build_options(download_dir: Path, cookie_path: Optional[Path]) -> dict:
    """封装 yt-dlp 配置，确保只下载音频。"""
    outtmpl = str(download_dir / "%(title).80s_%(epoch)s.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "restrictfilenames": True,
        "merge_output_format": PREFERRED_CODEC,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": PREFERRED_CODEC,
                "preferredquality": PREFERRED_QUALITY,
            }
        ],
        "trim_file_name": 240,
        "cachedir": False,
        "outtmpl_na_placeholder": "unknown",
        "paths": {"home": str(download_dir)},
        "http_headers": DEFAULT_HTTP_HEADERS,
    }
    if cookie_path:
        options["cookiefile"] = str(cookie_path)
    return options


def _normalize_bilibili_url(url: str) -> str:
    """尽量将 b23.tv 短链解析为完整链接，失败则回退原地址。"""
    if SHORT_URL_DOMAIN not in url.lower():
        return url

    request = urllib.request.Request(url, headers=DEFAULT_HTTP_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            redirected = response.geturl()
        if redirected:
            return redirected
    except Exception as exc:  # noqa: BLE001
        logger.warning("短链解析失败，继续使用原链接: %s", exc)
    return url


def _sanitize_exception_message(message: str) -> str:
    """移除 ANSI 控制符并压缩空白字符，避免错误信息污染页面。"""
    cleaned = ANSI_ESCAPE_PATTERN.sub("", message)
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_download_error_message(error_text: str, has_cookie: bool, source_url: str) -> str:
    """构造可读且可操作的下载错误提示。"""
    lowered_error = error_text.lower()
    is_http_403 = "http error 403" in lowered_error or "403: forbidden" in lowered_error
    if not is_http_403:
        return f"音频下载失败：{error_text}"

    hints = ["B站返回 HTTP 403，可能触发风控、访问频率限制或需要登录态。"]
    if SHORT_URL_DOMAIN in source_url.lower():
        hints.append("建议改用完整 BV 链接再试。")
    if has_cookie:
        hints.append("Cookies 已加载但可能已过期，请在设置中重新扫码登录。")
    hints.append("请在右上角「⚙️ → 🔑 B站登录」扫码登录后再试。")
    hints.append("若持续报错，请升级 yt-dlp 后重试。")

    hint_text = " ".join(hints)
    return f"音频下载失败（HTTP 403）：{hint_text} 原始错误：{error_text}"


def sanitize_title(title: str) -> str:
    """
    将视频标题清洗为安全的文件名片段（兜底工具函数，未在默认流程中调用）。

    Args:
        title: 原始标题。

    Returns:
        仅包含字母、数字、下划线、短横线和点号的字符串。
    """
    cleaned = re.sub(INVALID_FILENAME_CHARS, "_", title)
    return cleaned.strip("._") or "audio"
