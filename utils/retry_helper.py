"""
重试工具模块：提供统一的重试逻辑和错误分类。

功能：
1. 区分可重试和不可重试的错误
2. 提供预配置的重试装饰器
3. 支持指数退避和随机抖动

@author 开发
@date 2026-02-26
@version v1.0
"""
from __future__ import annotations

import logging
import socket
import urllib.error
from typing import Callable

from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)


def is_retryable_http_error(exc: Exception) -> bool:
    """
    判断 HTTP 错误是否可重试。

    可重试错误：
    - HTTP 429 (Too Many Requests)
    - HTTP 5xx (服务端错误)
    - URLError (网络连接问题)
    - socket.timeout (超时)

    不可重试错误：
    - HTTP 4xx (除 429 外，如 401/403/404)
    - 其他客户端错误
    """
    if isinstance(exc, urllib.error.HTTPError):
        # 429 限流和 5xx 服务端错误可重试
        return exc.code == 429 or 500 <= exc.code < 600

    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError)):
        # 网络连接问题和超时可重试
        return True

    return False


def is_retryable_download_error(exc: Exception) -> bool:
    """
    判断下载错误是否可重试。

    可重试错误：
    - 包含 "network" 关键字的错误
    - 包含 "timeout" 关键字的错误
    - 包含 "connection" 关键字的错误
    - HTTP 相关的可重试错误

    不可重试错误：
    - 视频不存在/已删除
    - 权限问题
    - 格式不支持
    """
    # 先检查 HTTP 错误
    if is_retryable_http_error(exc):
        return True

    # 检查错误消息中的关键字
    error_msg = str(exc).lower()
    retryable_keywords = ["network", "timeout", "connection", "temporary", "unavailable"]

    if any(keyword in error_msg for keyword in retryable_keywords):
        return True

    # 不可重试的关键字
    non_retryable_keywords = [
        "not found",
        "does not exist",
        "unavailable",
        "private",
        "deleted",
        "copyright",
        "geo",
        "region",
    ]

    if any(keyword in error_msg for keyword in non_retryable_keywords):
        return False

    # 默认不重试未知错误
    return False


def create_retry_decorator(
    max_attempts: int,
    wait_min: int,
    wait_max: int,
    retry_condition: Callable[[Exception], bool],
) -> Callable:
    """
    创建自定义重试装饰器。

    Args:
        max_attempts: 最大重试次数
        wait_min: 最小等待时间（秒）
        wait_max: 最大等待时间（秒）
        retry_condition: 判断是否重试的函数

    Returns:
        配置好的重试装饰器
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=wait_min, max=wait_max),
        retry=retry_if_exception(retry_condition),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


# 预配置的装饰器：API 调用重试
api_retry_decorator = create_retry_decorator(
    max_attempts=4,
    wait_min=1,
    wait_max=20,
    retry_condition=is_retryable_http_error,
)

# 预配置的装饰器：下载重试
download_retry_decorator = create_retry_decorator(
    max_attempts=3,
    wait_min=2,
    wait_max=30,
    retry_condition=is_retryable_download_error,
)
