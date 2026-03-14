# BiliView 部署指南

> 本文档记录了完整的部署流程、踩过的坑以及解决方案。
> 
> **案例记录**: 2026-03-14 成功部署到腾讯云服务器

---

## 📁 部署文件说明

本目录包含所有部署相关的文件：

| 文件 | 说明 |
|------|------|
| `Dockerfile` | Docker 镜像构建配置 |
| `docker-compose.yml` | Docker Compose 服务编排 |
| `nginx.conf` | Nginx 反向代理配置（可选） |
| `deploy.sh` | Bash 自动化部署脚本 |
| `deploy_remote.py` | Python 远程部署脚本 |
| `README.md` | 部署文档（本文档） |

---

## 📋 部署前准备

### 服务器配置要求
- **操作系统**: Ubuntu 20.04+ / CentOS 7+
- **内存**: 建议 2GB 以上（视频转录需要较多内存）
- **磁盘**: 建议 20GB 以上（下载的视频和音频文件占用空间）
- **网络**: 需要能访问外网（下载视频、调用 API）

### 必须开放的端口
- `8501` - Streamlit 应用端口（必需）
- `22` - SSH 端口（必需）
- `80/443` - 如需使用 Nginx（可选）

**注意**: 在腾讯云控制台的安全组中开放这些端口。

---

## 🚀 快速部署（推荐）

### 方法一：使用 Python 远程部署脚本

#### 1. 配置部署脚本

编辑 `deploy_remote.py`，修改服务器配置：

```python
# ==================== 服务器配置 ====================
HOST = "your_server_ip"  # 你的服务器 IP
PORT = 22  # SSH 端口
USERNAME = "ubuntu"  # 用户名
PASSWORD = "your_password"  # 密码
REMOTE_PATH = "/opt/bilview"  # 远程部署路径
# ==================================================
```

#### 2. 运行部署脚本

```bash
# 在项目根目录执行
cd /path/to/bilview

# 安装依赖
pip3 install paramiko

# 运行部署脚本
python3 deploy/deploy_remote.py
```

#### 3. 配置 API Keys

部署完成后，SSH 登录服务器配置 API Keys：

```bash
ssh ubuntu@你的服务器IP
nano /opt/bilview/.env
```

填入你的 API Keys：
```env
X666_API_KEY=sk-xxxxx
GROQ_API_KEY=gsk_xxxxx
```

#### 4. 重启服务

```bash
cd /opt/bilview/deploy
docker compose restart
```

---

### 方法二：手动部署

#### 1. 上传代码到服务器

```bash
# 在项目根目录打包
tar -czf bilview.tar.gz --exclude='.git' --exclude='__pycache__' --exclude='venv' .

# 上传到服务器
scp bilview.tar.gz ubuntu@服务器IP:/opt/
```

#### 2. 在服务器上解压

```bash
ssh ubuntu@服务器IP
cd /opt
sudo tar -xzf bilview.tar.gz
sudo chown -R ubuntu:ubuntu bilview
```

#### 3. 安装 Docker

```bash
# 安装 Docker
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker
sudo usermod -aG docker $USER
newgrp docker
```

#### 4. 配置环境变量

```bash
cd /opt/bilview

# 创建 .env 文件
cat > .env << 'EOF'
X666_API_KEY=your_api_key_here
GROQ_API_KEY=your_groq_api_key_here
EOF
```

#### 5. 构建并启动

```bash
cd deploy
docker compose build --no-cache
docker compose up -d
```

---

### 方法三：使用自动化脚本（在服务器上执行）

```bash
# 上传代码后，在服务器上执行
cd /opt/bilview/deploy
sudo bash deploy.sh
```

---

## 📊 部署流程图

```
┌─────────────────┐
│   准备部署文件   │
│  (修改配置)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  上传代码到服务器 │
│   (/opt/bilview)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  安装 Docker    │
│  和 Docker Compose
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  构建 Docker    │────▶│   构建失败？     │
│    镜像         │     │ 检查内存/网络    │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  启动容器       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐
│  配置 .env     │────▶│  缺少 API Key？  │
│  (API Keys)     │     │ 编辑 .env 文件  │
└────────┬────────┘     └─────────────────┘
         │
         ▼
┌─────────────────┐
│  重启容器       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  验证部署       │
│ 访问 :8501      │
└─────────────────┘
```

---

## 🔑 关键配置说明

### 1. .env 文件配置

**文件位置**: `/opt/bilview/.env`

**必需配置：**
```env
# X666_API_KEY - 用于 AI 总结（必需！）
# 获取地址: https://x666.me/
X666_API_KEY=sk-your-key-here

# GROQ_API_KEY - 用于语音转录（推荐）
# 获取地址: https://groq.com/
GROQ_API_KEY=gsk-your-key-here
```

**验证配置是否生效：**
```bash
# 在容器内查看环境变量
docker compose exec bilview env | grep X666_API_KEY
```

### 2. Docker Compose 配置

**构建命令：**
```bash
cd /opt/bilview/deploy
docker compose build --no-cache
docker compose up -d
```

**关键配置说明：**
- `build.context: ..` - 构建上下文是项目根目录
- `build.dockerfile: deploy/Dockerfile` - 使用 deploy 目录下的 Dockerfile
- `volumes` - 挂载数据目录和 .env 文件

---

## ⚠️ 常见坑与解决方案

### 坑 1：X666_API_KEY 未配置

**现象：**
```
缺少 X666_API_KEY，请在 .env 中配置或设置环境变量。
```

**解决方案：**
```bash
# 1. 检查 .env 文件
cat /opt/bilview/.env | grep X666_API_KEY

# 2. 确保值不为空
# 错误：X666_API_KEY=
# 正确：X666_API_KEY=sk-xxxxx

# 3. 重启容器
cd /opt/bilview/deploy && docker compose restart
```

### 坑 2：端口未开放

**现象：**
- 浏览器无法访问 `http://服务器IP:8501`
- 连接超时

**解决方案：**
```bash
# 在腾讯云控制台开放 8501 端口
# 或者使用命令
sudo ufw allow 8501/tcp
```

### 坑 3：内存不足

**现象：**
```
Error: Out of memory
```

**解决方案：**
```bash
# 增加 Swap 空间
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```

### 坑 4：构建失败

**现象：**
```
Error response from daemon: client version too old
```

**解决方案：**
```bash
# 在服务器本地执行，不要远程执行
cd /opt/bilview/deploy
sudo docker compose build --no-cache
```

---

## 🛠️ 常用管理命令

### 查看状态
```bash
cd /opt/bilview/deploy

# 容器状态
docker compose ps

# 资源使用
docker stats

# 日志
docker compose logs -f
```

### 重启服务
```bash
cd /opt/bilview/deploy
docker compose restart
```

### 更新代码
```bash
cd /opt/bilview
git pull
cd deploy
docker compose up -d --build
```

### 停止服务
```bash
cd /opt/bilview/deploy
docker compose down
```

---

## 🎯 部署成功标志

当你看到以下输出时，说明部署成功：

### 1. 容器状态
```bash
$ docker compose ps
NAME                STATUS          PORTS
bilview-app         Up 5 minutes    0.0.0.0:8501->8501/tcp
```

### 2. 应用日志
```bash
$ docker compose logs --tail=5
bilview-app  |   You can now view your Streamlit app in your browser.
bilview-app  |   External URL: http://your_server_ip:8501
```

### 3. 网页访问
浏览器打开 `http://服务器IP:8501`，能看到 Streamlit 界面。

---

## 📞 获取帮助

如果在部署过程中遇到问题：

1. 查看本文档的 **常见坑与解决方案** 章节
2. 查看应用日志：`docker compose logs -f`
3. 检查系统资源：`docker stats` 和 `free -h`

---

**最后更新**: 2026-03-14
**部署版本**: v1.0
