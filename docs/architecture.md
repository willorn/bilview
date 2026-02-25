# 系统架构与流程图

下图展示了从链接输入到总结输出的端到端流程（下载→转写→总结→存储→展示）。

![架构与流程](./architecture.png)

## 模块分层
- UI：`app.py`（Streamlit）
- 下载：`core/downloader.py`（yt-dlp）
- 转写：`core/transcriber.py`（Whisper，含分片与设备选择）
- 总结：`core/summarizer.py`（LLM 调用，含速率限制）
- 数据：`db/database.py`（SQLite 封装）
- 存储路径：`config.STORAGE_ROOT` 自动选择 `/data` 或项目根目录，可用环境变量 `BILVIEW_STORAGE_DIR` 覆盖，`data/` 与 `downloads/` 均挂在其下
- 配置与工具：`config.py`、`utils/`（目录/网络工具等）

> 若需更新流程，请同步替换同目录下的 `architecture.png`。
