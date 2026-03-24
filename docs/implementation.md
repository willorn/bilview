# 实现细节

本文档详细说明 BilView 的系统架构、技术实现、数据流和关键设计决策。

---

## 1. 系统架构总览

BilView 是一个 B 站视频知识提炼工具，核心流程为：

```
用户输入 B 站链接
       │
       ▼
   音频下载（yt-dlp）
       │
       ▼
   语音转写（Groq Whisper API）
       │
       ▼
   自动补标点（本地方案）
       │
       ▼
   AI 总结（LLM）
       │
       ▼
   结果展示与导出
```

所有处理均在服务端完成，浏览器端仅负责输入、展示和交互。

---

## 2. 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| 前端框架 | Streamlit 1.36+ | 页面构建与状态管理 |
| 音频下载 | yt-dlp | 提取 B 站音频流 |
| 语音转写 | Groq Whisper API | 云端 Whisper，速度快 |
| 本地标点 | punctuator.py | 纯规则补标点，不调 API |
| 总结生成 | x666.me LLM API | 支持 gemini/gpt 等模型 |
| 数据持久化 | SQLite + Supabase/D1（可选） | 任务状态与历史记录 |
| 部署平台 | Streamlit Cloud / Render | 托管运行 |

---

## 3. 目录结构

```
bilview/
├── app.py                    # Streamlit 前端（页面 + 任务调度）
├── config.py                 # 配置加载与环境变量读取
├── requirements.txt          # Python 依赖
│
├── core/
│   ├── downloader.py         # 音频下载（yt-dlp）
│   ├── transcriber.py        # 转写编排层（切片 + 进度回调）
│   ├── speech_recognition.py # Groq ASR 封装（多 key 轮询）
│   ├── punctuator.py         # 本地规则补标点
│   └── summarizer.py         # LLM 总结调用
│
├── db/
│   └── database.py           # SQLite 持久化 + Supabase/D1 支持
│
├── pages/
│   └── history.py            # 历史记录页面
│
├── utils/
│   ├── copy_button.py        # 复制按钮组件
│   ├── download_button.py    # 下载按钮组件
│   ├── file_helper.py        # 文件操作工具
│   ├── network.py            # 局域网地址获取
│   ├── retry_helper.py       # 重试装饰器
│   └── url_helper.py         # URL 处理工具
│
├── docs/
│   ├── default_prompt.md     # 默认总结 Prompt
│   └── ...
│
└── tests/                    # 单元测试与集成测试
```

---

## 4. 核心模块详解

### 4.1 音频下载 — `core/downloader.py`

使用 `yt-dlp` 仅提取最佳音质音频流，转换为 M4A 格式保存。

**关键配置：**
```python
format: "bestaudio/best"       # 只下载音频轨道
merge_output_format: "m4a"      # 输出格式
preferred_quality: "192"        # 码率
postprocessors: FFmpegExtractAudio  # 音频提取
```

**文件命名：**
```
<标题前80字符>_<时间戳>.m4a
```

**URL 兼容：**
- 支持完整 BV 链接：`https://www.bilibili.com/video/BVxxxxxx`
- 支持短链接：`https://b23.tv/xxxxxx`（自动解析重定向）
- 支持 AV 号（历史格式）

**错误处理：**
- HTTP 403：提示可能需要 `cookie.txt`（会员内容）
- 短链解析失败：回退原地址继续尝试
- 永久性错误（视频不存在）不重试

---

### 4.2 语音转写 — `core/transcriber.py` + `core/speech_recognition.py`

#### 4.2.1 切片策略

为避免单个音频文件过大导致 API 报错（413），超过以下阈值时自动分片：

| 阈值 | 值 |
|------|-----|
| 时长 | 300 秒（5 分钟）|
| 文件大小 | 25 MB |

每个分片独立调用 API，文本结果按顺序拼接。

#### 4.2.2 转写流程

```
audio_to_text()
    │
    ├─ 加载音频 → 检查是否需要分片
    │
    ├─ 不需要分片 → 直接调用 Groq API
    │
    └─ 需要分片
          ├─ 按 5 分钟切分音频
          ├─ 逐片调用 Groq API
          ├─ 每次切片触发 progress_callback
          └─ 文本拼接后返回
```

#### 4.2.3 Groq API 封装 — `speech_recognition.py`

**多 Key 轮询机制：**

```python
class ApiKeyRoundRobin:
    # 线程安全轮询
    # 单 key 限流（429）→ 自动切换下一个 key
    # 401/403 → 跳过（认证问题，不会恢复）
    # 5xx / 网络错误 → 重试下一个 key
```

**请求参数：**
```python
model: "whisper-large-v3-turbo"
language: "zh"
prompt: "请输出简体中文逐字稿，并尽量补全自然中文标点符号"
timeout: 120 秒
```

**响应解析：**
直接读取 OpenAI 兼容响应的 `response.text` 字段。

---

### 4.3 标点补充 — `core/punctuator.py`

纯本地方案，基于规则的标点插入，不依赖任何 API。

**触发条件：**
- 标点密度 < 0.8%（原始 ASR 输出通常标点极少）
- 无句子结束符（。！？）

**规则设计：**

| 规则 | 说明 |
|------|------|
| 句子最大长度 | 46 字符，超出则强制断句 |
| 子句最大长度 | 20 字符，超出且遇到转折词则加逗号 |
| 问句判断 | 结尾字在疑问字表（吗/么/呢/吧等）中则加问号 |
| 句子连接词 | 检测到"然后/所以/因此"等则断句 |

**一致性校验：**
- 补标点后对文本做"去标点+去空白"归一化
- 与原文对比，不一致时回退原文（避免 ASR 已输出标点被破坏）

---

### 4.4 AI 总结 — `core/summarizer.py`

**接口调用：**
```python
# OpenAI 兼容的 Chat Completions 接口
POST https://x666.me/v1/chat/completions
Authorization: Bearer <X666_API_KEY>
model: gemini-2.5-pro-1m（可配置）
temperature: 0.2（低随机性）
```

**Prompt 来源：**
1. 优先读取 `docs/default_prompt.md` 文件
2. 文件不存在或为空时使用内置 fallback prompt

**Fallback Prompt（内置）：**
```
你是一个专业的长视频笔记助手，请将输入的完整转录文本，
提炼为结构化笔记，需包含：
1) 内容摘要：3-5 条
2) 核心亮点/金句：2-4 条
3) 结论与行动建议：2-3 条
要求：用中文输出；保持事实准确，不臆测；
必要时保留数字、公式或关键引用。
```

**速率限制：**
- 全局锁 + 时间戳控制
- 两次 LLM 调用间隔至少 20 秒（防止触发服务商限流）

**自动重试：**
- 可重试：429、5xx、网络超时
- 不可重试：4xx（除 429）
- 指数退避：1-20 秒

---

## 5. 数据持久化 — `db/database.py`

### 5.1 表结构

```text
-- SQLite schema (仅供参考)
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bilibili_url TEXT NOT NULL,          -- B 站原始链接
    video_title TEXT NOT NULL,           -- 视频标题
    video_duration_seconds INTEGER,      -- 视频时长
    audio_file_path TEXT,                -- 音频文件路径
    transcript_text TEXT,                -- 加标点后的逐字稿
    transcript_raw_text TEXT,            -- 原始逐字稿（无标点）
    summary_text TEXT,                  -- AI 总结结果
    cancel_requested INTEGER DEFAULT 0, -- 用户取消标志
    error_stage TEXT,                   -- 失败阶段（downloading/transcribing/summarizing）
    error_code TEXT,                    -- 错误码
    error_message TEXT,                 -- 错误信息
    error_updated_at DATETIME,           -- 错误更新时间
    status TEXT DEFAULT 'waiting',       -- 任务状态
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
)

CREATE INDEX idx_tasks_created_at ON tasks (created_at DESC)
CREATE INDEX idx_tasks_status ON tasks (status)
```

### 5.2 任务状态机

```
waiting → downloading → transcribing → summarizing → completed
   │          │              │              │
   └──────────┴──────────────┴──────────────┴──→ failed
   └───────────────────→ cancelled（用户取消）
   └───────────────────→ timeout（执行超时）
```

### 5.3 断点续传

转写过程中，每个完成的切片文本实时写入数据库。若中途崩溃或被取消，下次启动时通过 `recover_interrupted_tasks()` 扫描未完成任务，自动跳过已完成的切片继续处理。

### 5.4 多数据库后端

| 后端 | 说明 |
|------|------|
| SQLite（默认） | 本地文件，性能好，适合单机部署 |
| Supabase PostgreSQL | 云端数据库，推荐用于 Streamlit Cloud 部署 |
| Cloudflare D1 | HTTP API，需要 CF 凭据 |

---

## 6. 前端交互 — `app.py`

### 6.1 页面布局

```
┌──────────────┬─────────────────────────────────────┐
│  侧边栏      │  主内容区                            │
│              │                                     │
│  历史记录列表  │  B 站链接输入框                     │
│  （按时间倒序）│  开始处理按钮                        │
│              │  任务状态进度条                       │
│  删除按钮     │  逐字稿展示（可复制）                │
│              │  总结结果展示（可复制）               │
│              │  导出按钮                           │
└──────────────┴─────────────────────────────────────┘
```

### 6.2 任务调度器 — `_PersistentTaskExecutor`

后台守护线程，轮询数据库中 `waiting` 状态的任务：

```
dispatch_loop():
    while not stopped:
        task = claim_next_waiting_task()  # 原子操作，防止并发抢任务
        if task:
            submit(_run_task_background, task.id)
        else:
            sleep(poll_interval)

_run_task_background(task_id):
    _run_download(task_id)
    _run_transcribe(task_id)
    _run_summarize(task_id)
    → 失败则写 error_stage / error_code
```

**线程池配置（可在 config.py 中调）：**
- `TASK_EXECUTOR_MAX_WORKERS = 1`（默认单任务串行，防止资源竞争）
- `TASK_EXECUTOR_TIMEOUT_OVERFLOW_WORKERS = 1`（超时任务额外扩容）

**超时控制：**
- 单任务超时（默认 5400 秒 = 90 分钟）
- 用户可中途取消（`cancel_requested` 标志）

### 6.3 历史记录 — `pages/history.py`

- 按 `created_at DESC` 列出所有任务
- 支持按视频标题关键词搜索
- 可查看逐字稿、总结内容
- 可重新生成总结（修改 Prompt）
- 可删除任务

---

## 7. 配置体系 — `config.py`

### 7.1 存储路径

```python
# 优先使用云端持久卷 /data（Hugging Face Spaces）
# 其次使用项目根目录
STORAGE_ROOT = /data 或 ./
DATA_DIR = STORAGE_ROOT/data/
DOWNLOAD_DIR = STORAGE_ROOT/downloads/
DB_PATH = DATA_DIR/app.db
```

### 7.2 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `X666_API_KEY` | LLM 总结 API Key | 必填 |
| `GROQ_API_KEY` | 语音转写 API Key | 必填 |
| `GROQ_API_KEYS` | 多 key 轮询（逗号分隔） | 空 |
| `GROQ_ASR_MODEL` | Whisper 模型名 | whisper-large-v3-turbo |
| `ASR_REQUEST_TIMEOUT_SECONDS` | 转写超时 | 120 |
| `BILVIEW_STORAGE_DIR` | 自定义存储目录 | 自动选择 |
| `DB_AUTO_INIT_ON_STARTUP` | 启动初始化 DB | 0 |

### 7.3 云数据库配置

```env
# Supabase PostgreSQL
SUPABASE_POSTGRES_URL=postgresql://postgres:your_password@db.your-project.supabase.co:5432/postgres

# Cloudflare D1
CLOUDFLARE_ACCOUNT_ID=xxx
CLOUDFLARE_D1_DATABASE_ID=xxx
CLOUDFLARE_API_TOKEN=xxx
```

---

## 8. 关键设计决策

### 8.1 为什么用 Groq 而非本地 Whisper？

- Groq LPU 芯片推理速度快（实时级），本地 CPU 无法比拟
- 无需管理模型权重下载和版本更新
- 多 key 轮询提供高可用性
- Render / Streamlit Cloud 等平台无 GPU，本地 Whisper 不实用

### 8.2 为什么不把标点也交给 LLM 生成？

- 补标点无需理解语义，纯规则即可完成
- 节省 LLM token 消耗和调用延迟
- 避免 LLM 生成标点时"发挥创意"破坏原文

### 8.3 为什么用 SQLite 而非内存状态？

- Streamlit 重载/重启不丢任务状态
- 支持多页面共享历史记录
- 断点续传依赖持久化进度

### 8.4 为什么用轮询而非异步回调？

- Streamlit 是请求-响应模型，无常驻连接
- SQLite 锁冲突概率低（单线程轮询）
- 实现简单，无需额外基础设施

---

## 9. 错误处理体系

### 9.1 分层异常

| 层级 | 异常类型 | 处理方式 |
|------|---------|---------|
| 下载 | `RuntimeError` + 友好提示 | 写 `error_stage=downloading`，展示用户提示 |
| 转写 | `RuntimeError` | 写 `error_stage=transcribing`，可断点续传 |
| 总结 | `RuntimeError` | 写 `error_stage=summarizing` |
| 用户取消 | `TaskCancelledError` | 状态 → cancelled |
| 执行超时 | `TaskCancelledError(reason=timeout)` | 状态 → timeout |

### 9.2 错误码

| 错误码 | 含义 |
|--------|------|
| `TASK_TIMEOUT` | 任务执行超时 |
| `HTTP_4xx` | 特定 HTTP 4xx 错误 |
| `HTTP_5xx` | 服务器错误 |
| `UNKNOWN` | 其他未分类错误 |

---

## 10. 外部依赖说明

| 依赖 | 版本 | 用途 |
|------|------|------|
| `streamlit` | ≥1.36 | 前端框架 |
| `yt-dlp` | ≥2024.12 | B 站视频下载 |
| `openai` | ≥1.54 | Groq API 客户端 |
| `pydub` | ≥0.25 | 音频分片处理 |
| `tenacity` | ≥8.2 | 自动重试装饰器 |
