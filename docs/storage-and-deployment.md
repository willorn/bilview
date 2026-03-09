# 存储与部署

本页告诉你数据存在哪、如何让别人能访问。

## 数据存储位置

BilView 会自动选择存储根目录：

- 优先使用 `/data`（适合云端持久卷）
- 否则使用项目根目录
- 你也可以手动指定：`BILVIEW_STORAGE_DIR`

存储根目录下包含：

- `data/`：数据库
- `downloads/`：音频缓存

## 本地启动（个人使用）

```bash
streamlit run app.py
```

访问：`http://localhost:8501`

## 局域网访问（手机/同事）

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

同一局域网设备访问：`http://<你的机器IP>:8501`

## 云端部署建议（进阶）

- 挂载持久卷到 `/data`，或设置 `BILVIEW_STORAGE_DIR`
- 把 API Key 放到平台 Secret，不写死在仓库
- 对外开放端口并配置网关/反向代理
- 定期清理 `downloads/` 避免磁盘占满

## Docker 部署

项目已提供 `Dockerfile` 和 `docker-compose.yml`。

```bash
docker compose up -d --build
```

详细最佳实践请查看：[docker-deployment.md](docker-deployment.md)
