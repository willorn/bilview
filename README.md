# BilView · 让 B 站长视频 3 分钟变成可读笔记

> 面向小白用户：贴链接就能自动完成下载、转写、总结和导出。

BilView 是一个本地化的 AI 视频知识提炼工具，帮助你把「看完就忘」的长视频，变成「可保存、可检索、可复用」的文字资产。

## 为什么用 BilView

- 上手门槛低：只需要会复制链接和点按钮。
- 全流程自动化：下载音频 → 语音转写 → AI 总结 → 本地保存。
- 结果可沉淀：历史任务可回看，支持逐字稿和总结导出。
- 部署灵活：本地跑、服务器跑、手机访问都支持。

## 小白 3 步跑起来（推荐）

1) 安装依赖
```bash
pip install -r requirements.txt
```

2) 在项目根目录创建 `.env`
```env
X666_API_KEY=your_llm_key
GROQ_API_KEY=your_groq_key
```

3) 启动应用
```bash
streamlit run app.py
```

打开 `http://localhost:8501`，粘贴 B 站链接即可开始。

## Docker 快速部署

1) 配置 `.env`（至少包含 `X666_API_KEY`，建议同时配置 `GROQ_API_KEY`）

2) 启动：
```bash
docker compose up -d --build
```

3) 访问：`http://localhost:8501`

完整最佳实践请看：[Docker 部署最佳实践](docs/docker-deployment.md)

## 文档导航

### 新手优先

- [新手快速开始](docs/getting-started.md)
- [使用指南（从输入到导出）](docs/usage.md)
- [常见问题排查](docs/troubleshooting.md)
- [文档总览](docs/README.md)

### 进阶与技术文档

- [配置说明](docs/configuration.md)
- [存储与部署](docs/storage-and-deployment.md)
- [Docker 部署最佳实践](docs/docker-deployment.md)
- [系统架构](docs/architecture.md)
- [开发与测试](docs/development.md)
- [默认总结 Prompt](docs/default_prompt.md)

## License

[MIT](LICENSE)
