# Docker 部署最佳实践

本指南适用于 BilView 的单机/云主机部署，目标是稳定、可维护、可升级。

## 1) 先决条件

- 主机已安装 Docker（建议 24+）和 Docker Compose 插件（`docker compose`）
- 准备好 API Key（至少 `X666_API_KEY`，建议同时配置 `GROQ_API_KEY`）

## 2) 最小可用部署（推荐）

项目根目录已提供：

- `Dockerfile`
- `docker-compose.yml`

先配置 `.env`（示例）：

```env
X666_API_KEY=your_llm_key
GROQ_API_KEY=your_groq_key
ASR_PROVIDER=groq
```

启动：

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f bilview
```

访问：

`http://<主机IP>:8501`

## 3) 为什么这是最佳实践

- 使用非 root 用户运行容器，降低安全风险
- 使用 `tini` 作为入口进程，正确处理信号和僵尸进程
- 挂载持久卷到 `/data`，任务历史和下载缓存不会因重建容器丢失
- 配置健康检查，便于平台自动发现异常实例
- API Key 通过 `.env`/平台 Secret 注入，不写死进镜像

## 4) 生产环境建议

1. 反向代理和 HTTPS  
在 Nginx/Caddy/Traefik 后面暴露，开启 TLS，不直接把 8501 裸露到公网。

2. 资源限制  
给容器设置 CPU/内存上限，避免转写任务挤占主机资源。

3. 日志策略  
配置 Docker 日志轮转（如 `max-size`, `max-file`），防止磁盘被日志打满。

4. 升级策略  
固定镜像版本标签，采用「拉新镜像 -> 启动新容器 -> 健康检查通过后切流」流程。

5. 数据备份  
定期备份卷中的 `/data`（至少备份 `data/` 数据库目录）。

## 5) 常用运维命令

重建并更新：

```bash
docker compose up -d --build
```

停止：

```bash
docker compose down
```

停止并删除卷（会清空历史数据，谨慎执行）：

```bash
docker compose down -v
```

进入容器排障：

```bash
docker exec -it bilview bash
```

## 6) 常见问题

1) `ffmpeg not found`  
请确认使用仓库内 `Dockerfile` 构建，而不是自定义精简镜像。

2) 页面可打开但处理失败  
优先检查 `.env` 中的 API Key 是否有效，再看容器日志。

3) 重建后历史任务丢失  
说明没有挂载持久卷。请确保 `docker-compose.yml` 中存在 `/data` 卷挂载。
