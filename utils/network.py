"""
网络工具：获取局域网 IPv4 地址。
"""
from __future__ import annotations

import socket
from typing import List


def get_lan_addresses() -> List[str]:
    """
    返回本机的局域网 IPv4 地址列表（排除 127.0.0.1）。
    """
    addrs: set[str] = set()
    hostname = socket.gethostname()
    try:
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if _is_ipv4_private(ip):
                addrs.add(ip)
    except socket.gaierror:
        pass

    # 兜底：尝试通过 UDP 套接字探测
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        if _is_ipv4_private(ip):
            addrs.add(ip)
    except Exception:
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    return sorted(addrs)


def _is_ipv4_private(ip: str) -> bool:
    if ip.startswith("127."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    first = int(parts[0])
    second = int(parts[1])
    if first == 10:
        return True
    if first == 172 and 16 <= second <= 31:
        return True
    if first == 192 and second == 168:
        return True
    return False
