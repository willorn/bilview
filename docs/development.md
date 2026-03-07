# 开发与测试

## 目录说明

- `app.py`：Streamlit 页面与流程编排
- `core/`：下载、转写、总结核心逻辑
- `db/`：数据库初始化与 CRUD
- `utils/`：文件与网络等通用工具

更多分层说明见：[architecture.md](architecture.md)

## 开发前准备

```bash
pip install -r requirements.txt
```

## 基础检查

```bash
python -m py_compile app.py core/*.py db/*.py
```

## 单次链路快速验证

```bash
python - <<'PY'
from core.downloader import download_audio
from core.transcriber import audio_to_text
from core.summarizer import generate_summary
from db.database import init_db

init_db()
url = "https://b23.tv/dNNt3B6"
audio = download_audio(url)
text = audio_to_text(audio, language="zh")
print(generate_summary(text[:2000])[:200])
PY
```

## 开发注意事项

- 运行期数据（`data/`、`downloads/`）不要提交到仓库。
- 处理 SQL 时统一使用参数化占位符，避免字符串拼接。
- UI 层只做交互与展示，业务逻辑下沉到 `core/` 与 `db/`。
