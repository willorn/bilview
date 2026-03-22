"""
pyaudioop 兼容层：Python 3.13 移除了标准库 audioop，pydub 依赖它。
本模块用 stdlib 实现 audioop.rms 核心功能，供 pydub 使用。
"""
from __future__ import annotations

import struct
from types import ModuleType

# pydub.utils 执行 `import pyaudioop as audioop`，然后调用 audioop.rms(data, width)
# 我们创建一个模块代理对象，暴露 rms 函数。
audioop = ModuleType("audioop")


def rms(audio_data: bytes, width: int) -> float:
    """
    计算 PCM 音频数据的均方根（RMS）振幅。
    等价于已移除的 audioop.rms()。
    """
    if not audio_data:
        return 0.0

    if width == 1:
        # 8-bit unsigned，带 128 偏移
        samples = struct.unpack(f"{len(audio_data)}B", audio_data)
        samples = [s - 128 for s in samples]
    elif width == 2:
        samples = struct.unpack(f"<{len(audio_data) // 2}h", audio_data)
    elif width == 3:
        # 24-bit 小端序有符号（手动处理）
        total = 0.0
        n = len(audio_data) // 3
        for i in range(n):
            b0, b1, b2 = audio_data[i * 3], audio_data[i * 3 + 1], audio_data[i * 3 + 2]
            val = b0 | (b1 << 8) | ((b2 << 24) >> 8)  # 有符号 24-bit 扩展
            total += val * val
        return (total / n) ** 0.5 if n else 0.0
    elif width == 4:
        samples = struct.unpack(f"<{len(audio_data) // 4}i", audio_data)
    else:
        return 0.0

    total = sum(s * s for s in samples)
    return (total / len(samples)) ** 0.5 if samples else 0.0


audioop.rms = rms
