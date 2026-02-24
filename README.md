## Bilibili 视频转录与智能总结工具

本项目提供本地化的一键流程：输入 B 站链接 → 下载音频 → Whisper 转写 → LLM 总结 → 本地持久化与导出。

### 主要特性
- **全本地转写**：内置 Whisper（默认 tiny，可切换 base/small…），避免在线 ASR 成本。
- **一键流程**：Streamlit 界面触发，自动串联下载 / 转写 / 总结并写入 SQLite。
- **结果持久化**：`data/app.db` 自动建表，历史记录可浏览与下载。
- **可配置 LLM**：默认 `gemini-2.5-pro-1m`（x666 接口），支持自定义模型、温度、API Key。
- **安全实践**：状态存储英文枚举，UI 层中文映射；SQL 全部使用占位符。
- **元信息存储**：任务表包含视频标题与时长（秒），便于后续统计与展示。

### 目录结构
```
app.py                # Streamlit 前端与流程编排
config.py             # 配置与 .env 加载
core/
  downloader.py       # yt-dlp 仅提取音频
  transcriber.py      # Whisper 转写（分片+模型缓存+设备自动选择）
  summarizer.py       # LLM 调用与速率限制
db/
  database.py         # SQLite 封装（init/CRUD）
utils/
  file_helper.py      # 目录与文件工具
  network.py          # 局域网地址获取
data/                 # SQLite 存放目录（已忽略）
downloads/            # 音频缓存目录（已忽略）
```

### 快速开始
1) 安装依赖（已包含 yt-dlp / whisper / streamlit 等）  
   ```bash
   pip install -r requirements.txt
   ```
   需本地可用 `ffmpeg`；首次运行 Whisper 会下载模型（需联网）。

2) 配置 API Key（可选，默认使用文档示例值）  
   新建 `.env`（已被 .gitignore）：  
   ```
   X666_API_KEY=你的key
   ```

3) 启动前端  
   ```bash
   streamlit run app.py
   ```
   浏览器打开 `http://localhost:8501`。

### 使用说明
- 在输入框粘贴 B 站链接，点击“开始处理”。流程：下载 → 转写（Whisper） → 总结（LLM）。  
- 右侧历史记录可查看/下载逐字稿与总结。  
- 转写默认使用 Whisper tiny、中文语言，超 25MB 或 5 分钟自动分片。  
- LLM 调用全局 20s 速率限制，超时/错误将把任务标记为失败。

### 移动端访问
- 可在本机/服务器运行后用手机浏览器访问，命令示例：  
  `streamlit run app.py --server.address 0.0.0.0 --server.port 8501`  
  然后在同一局域网手机浏览器打开 `http://<电脑IP>:8501`。远程则需放行端口或使用内网穿透（frp/ngrok 等）。
- Streamlit 对窄屏会自动折叠为单列布局；下载按钮在手机上可直接保存文件。

### 配置要点
- `.env`：`X666_API_KEY`（优先）  
- `config.py`：`DEFAULT_LLM_API_URL`、`DEFAULT_LLM_MODEL`、`DB_PATH`、`DOWNLOAD_DIR` 等集中管理。  
- 状态枚举：DB 中存英文 (`waiting/downloading/...`)，UI 层通过 `STATUS_MAP` 映射中文。

### 开发与测试
- 语法检查：`python -m py_compile app.py core/*.py db/*.py`  
- 单次链路快速验证（示例）：  
  ```bash
  python - <<'PY'
  from core.downloader import download_audio
  from core.transcriber import audio_to_text
  from core.summarizer import generate_summary
  from db.database import init_db
  init_db()
  url = "https://b23.tv/dNNt3B6"
  audio = download_audio(url)
  text = audio_to_text(audio, model_size="tiny", language="zh")
  print(generate_summary(text[:2000])[:200])
  PY
  ```

### 注意事项
- `data/` 与 `downloads/` 已忽略，请勿提交运行期数据或音频文件。  
- 如需 GPU/MPS 加速，安装对应的 `torch` 版本；代码会自动选择可用设备并在不支持时回退 CPU。  
- 若需更高精度，可将 `model_size` 调为 `small/base/medium`，性能与显存占用相应上升。  
- 若需更复杂的限流/重试策略，可在 `core/summarizer.py` 扩展。 

### 架构与流程图
- 查看详细架构说明： [docs/architecture.md](docs/architecture.md)

