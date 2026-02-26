"""
URL 处理工具：提取和清洗 B 站链接。

功能：
1. 从包含标题的文本中提取 URL
2. 支持 b23.tv 短链接和完整 bilibili.com 链接
3. 清洗和验证 URL 格式

@author 开发
@date 2026-02-26
@version v1.0
"""
from __future__ import annotations

import re
from typing import Optional


def extract_bilibili_url(text: str) -> Optional[str]:
    """
    从文本中提取 B 站视频链接。

    支持格式：
    - 纯 URL: https://b23.tv/xxxx 或 b23.tv/xxxx
    - 纯 URL: https://www.bilibili.com/video/BVxxxx
    - 带标题: 【标题】 https://b23.tv/xxxx
    - 移动端: https://m.bilibili.com/video/BVxxxx
    - 番剧: https://www.bilibili.com/bangumi/play/epxxxx
    - 大小写不敏感

    Args:
        text: 用户输入的文本

    Returns:
        提取出的 URL，如果未找到则返回 None
    """
    if not text or not isinstance(text, str):
        return None

    # 去除首尾空白
    text = text.strip()

    # 正则表达式匹配 B 站链接（大小写不敏感）
    # 支持带协议和不带协议的链接
    url_patterns = [
        # b23.tv 短链接（支持无协议）
        r'(?:https?://)?b23\.tv/[a-zA-Z0-9]+(?:[/?#][^\s]*)?',
        # bilibili.com 各种链接（支持 www/m/无前缀，支持 video/bangumi 等路径）
        r'(?:https?://)?(?:www\.|m\.)?bilibili\.com/(?:video|bangumi/play)/[a-zA-Z0-9]+(?:[/?#][^\s]*)?',
    ]

    for pattern in url_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            url = match.group(0)
            # 如果没有协议，自动添加 https://（大小写不敏感检查）
            if not url.lower().startswith(('http://', 'https://')):
                url = 'https://' + url
            return url

    # 如果没有匹配到，检查是否整个文本就是一个 URL
    if text.lower().startswith(('http://', 'https://')):
        return text

    return None


def clean_bilibili_url(url: str) -> str:
    """
    清洗 B 站 URL，保留重要参数，移除分享追踪参数。

    保留的参数：
    - t: 时间戳（跳转到指定时间）
    - p: 分P（多P视频的指定分集）

    移除的参数：
    - share_source, share_medium, share_plat 等分享追踪参数
    - 其他营销和追踪参数

    Args:
        url: 原始 URL

    Returns:
        清洗后的 URL
    """
    if not url:
        return url

    # 如果没有查询参数，直接返回
    if '?' not in url:
        return url

    # 分离 URL 和查询参数
    base_url, query_string = url.split('?', 1)

    # 解析查询参数
    params = {}
    for param in query_string.split('&'):
        if '=' in param:
            key, value = param.split('=', 1)
            params[key] = value

    # 保留重要参数
    important_params = ['t', 'p']
    kept_params = {k: v for k, v in params.items() if k in important_params}

    # 重建 URL
    if kept_params:
        query = '&'.join(f'{k}={v}' for k, v in kept_params.items())
        return f'{base_url}?{query}'

    return base_url


def validate_bilibili_url(url: str) -> bool:
    """
    验证是否为有效的 B 站链接。

    Args:
        url: 待验证的 URL

    Returns:
        True 如果是有效的 B 站链接，否则 False
    """
    if not url or not isinstance(url, str):
        return False

    # 检查是否包含 B 站域名（大小写不敏感）
    url_lower = url.lower()
    valid_domains = ['b23.tv', 'bilibili.com']
    return any(domain in url_lower for domain in valid_domains)


def process_user_input(text: str) -> Optional[str]:
    """
    处理用户输入，提取并清洗 B 站链接。

    这是一个便捷函数，组合了提取、清洗和验证的功能。

    Args:
        text: 用户输入的文本

    Returns:
        处理后的 URL，如果无效则返回 None

    Examples:
        >>> process_user_input("【标题】 https://b23.tv/xxxx")
        'https://b23.tv/xxxx'

        >>> process_user_input("https://www.bilibili.com/video/BVxxxx?share=1")
        'https://www.bilibili.com/video/BVxxxx'
    """
    # 提取 URL
    url = extract_bilibili_url(text)
    if not url:
        return None

    # 清洗 URL
    url = clean_bilibili_url(url)

    # 验证 URL
    if not validate_bilibili_url(url):
        return None

    return url
