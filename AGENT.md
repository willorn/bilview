# Bilibili 视频转录与智能总结工具 - 开发规范 v1.0

## 一、AI 行为约束（最高优先级）

- 输出语言：中文
- git commit in chinese 
- 不允许执行任何未经确认的依赖安装、系统级构建或打包操作（如 pip install、pyinstaller等，需给出命令由开发者确认后才允许执行）
- 在编码前需先分析需求与潜在边界条件（如 API 超时、本地存储空间、网络异常），再给出实现代码
- 优先一次性给出完整方案和完整代码，避免挤牙膏式拆分成多轮修改

## 二、编码强制规范（必须遵守）

- 遵循 PEP 8 Python 代码风格规范。
- 强制使用 Python 3.10+ 的类型注解（Type Hints），例如 `def process_video(url: str) -> bool:`。
- 禁止在函数内部使用 `import`，所有标准库、第三方库、本地模块的导入必须置于文件顶部。
- 变量命名规则：
  1）类名使用大驼峰（CamelCase），如 `AudioTranscriber`
  2）函数、变量、模块名使用小写加下划线（snake_case），如 `download_audio`
  3）全局常量使用全大写加下划线（UPPER_SNAKE_CASE），如 `MAX_AUDIO_SIZE_MB`
- 【强制】数据库层（SQLite）所有涉及参数化查询的 SQL 语句，绝对禁止使用 f-string 或 `+` 拼接变量，必须使用 `?` 占位符以防止 SQL 注入。
- 【强制】严禁在 `app.py` (UI层) 中直接编写 SQL 语句或复杂的网络请求逻辑，必须调用 `core/` 或 `db/` 层的封装函数。

## 三、编码推荐规范（允许权衡）

- 集合数据处理优先使用列表推导式（List Comprehensions）或生成器表达式（Generator Expressions），替代简单的 `for` 循环 append 操作。
- 复杂的业务逻辑（如重试机制）推荐使用 `tenacity` 库或封装清晰的 `while` 循环处理。
- 涉及到可能为 `None` 的返回值，强制在类型注解中使用 `Optional[T]`。
- 单个函数推荐不超过 40 行，涉及复杂 API 请求封装与异常捕获的函数允许放宽至 60 行，但必须有清晰的行级注释。

### 开发规范细则

1. 所有的配置项、环境变量提取需统一放置在 `config.py` 中，禁止在业务代码中硬编码。
2. 接口/函数功能分类：`get_xxx`（查询）、`save_xxx`（保存/创建）、`delete_xxx`（删除）、`parse_xxx`（解析提取）。
3. 纯业务逻辑函数不处理 UI 交互；如果函数执行耗时较长，应通过生成器（`yield`）或回调机制向上层返回进度状态，供 Streamlit 渲染。
4. 核心功能（音视频下载、LLM 调用）必须有完整的 `try...except...finally` 异常捕获机制，并记录清晰的错误日志或返回标准的错误字典。
5. 涉及到本地文件操作（如写入、删除 `.mp4` / `.mp3`），必须使用 `pathlib.Path` 替代老旧的 `os.path`，并在操作前检查路径有效性。
6. 第三方库引入规范：优先使用生态主流库（如 `yt-dlp` 处理下载，`openai` 处理大模型 API），并在 `requirements.txt` 中锁定大版本号。

### 文件与注释模板规范

#### 规则：文件头与模块注释
1）每个核心 Python 模块（如 `downloader.py`, `database.py`）顶部必须有模块级 Docstring（三引号注释）。
2）业务类和核心函数必须编写函数级 Docstring，标明入参（Args）、出参（Returns）和可能抛出的异常（Raises）。

#### 模板：模块与类级注释参考
```python
"""
模块描述：负责处理 Bilibili 视频链接解析与本地音频下载的核心逻辑

@author 开发团队
@date 2023-10-27
@version v1.0
"""
import logging
from pathlib import Path
from typing import Optional

class BiliDownloader:
    """
    B站视频下载器
    封装了 yt-dlp 的核心调用，仅提取音频流并保存至本地指定目录。
    """
    def __init__(self, download_dir: str):
        ...