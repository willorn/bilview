# 新手快速开始

目标：用最少配置，在 3-5 分钟内跑通 BilView。

## 第 1 步：检查环境

- Python `3.10` 或 `3.12`
- 系统已安装 `ffmpeg`

> Python 3.13 也可用，但请确认 `audioop-lts` 可安装。

## 第 2 步：安装依赖

```bash
pip install -r requirements.txt
```

## 第 3 步：创建 `.env`

在项目根目录新建 `.env`：

```env
X666_API_KEY=your_llm_key
GROQ_API_KEY=your_groq_key
```

说明：
- `X666_API_KEY`：用于 AI 总结
- `GROQ_API_KEY`：用于语音转写

## 第 4 步：启动

```bash
streamlit run app.py
```

浏览器访问：`http://localhost:8501`

## 第 5 步：验证是否成功

1. 粘贴一个 B 站链接。
2. 点击“开始处理”。
3. 看到转写和总结结果即表示跑通。

如果报错，请先看：[常见问题排查](troubleshooting.md)
