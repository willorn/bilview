#!/bin/bash

# BiliView 部署脚本
# 用于腾讯云服务器一键部署

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 打印带颜色的信息
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查是否为 root 用户
if [ "$EUID" -ne 0 ]; then
   print_error "请使用 root 权限运行此脚本 (sudo ./deploy.sh)"
   exit 1
fi

print_info "开始部署 BiliView..."

# 1. 安装 Docker（如果未安装）
if ! command -v docker &> /dev/null; then
    print_info "正在安装 Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    print_info "Docker 安装完成"
else
    print_info "Docker 已安装"
fi

# 2. 安装 Docker Compose（如果未安装）
if ! command -v docker-compose &> /dev/null; then
    print_info "正在安装 Docker Compose..."
    DOCKER_COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
    curl -L "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
    print_info "Docker Compose 安装完成"
else
    print_info "Docker Compose 已安装"
fi

# 3. 创建应用目录
APP_DIR="/opt/bilview"
print_info "创建应用目录: $APP_DIR"
mkdir -p $APP_DIR
cd $APP_DIR

# 4. 克隆或更新代码
if [ -d ".git" ]; then
    print_info "更新代码..."
    git pull
else
    print_info "请确保代码已上传到服务器 $APP_DIR 目录"
    print_warn "如果没有上传，请先运行: git clone <你的仓库地址> $APP_DIR"
fi

# 5. 创建必要的目录
mkdir -p data downloads ssl

# 6. 检查 .env 文件
if [ ! -f ".env" ]; then
    print_warn ".env 文件不存在，请创建并配置 API Keys"
    cat > .env << 'EOF'
# API Keys 配置
X666_API_KEY=your_x666_api_key_here
GROQ_API_KEY=your_groq_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here

# 其他配置
DEFAULT_ASR_PROVIDER=groq
DEFAULT_LLM_MODEL=gemini-2.5-pro-1m
EOF
    print_info "已创建 .env 模板文件，请编辑并填入你的 API Keys"
    nano .env
fi

# 7. 构建并启动容器
print_info "构建 Docker 镜像..."
cd deploy
docker-compose build --no-cache

print_info "启动服务..."
docker-compose up -d

# 8. 等待服务启动
print_info "等待服务启动..."
sleep 10

# 9. 检查服务状态
if docker-compose ps | grep -q "Up"; then
    print_info "服务启动成功！"
    print_info "访问地址: http://$(curl -s ifconfig.me):8501"
    
    # 如果使用 Nginx
    if docker-compose ps | grep -q "nginx"; then
        print_info "Nginx 反向代理: http://$(curl -s ifconfig.me)"
    fi
else
    print_error "服务启动失败，请检查日志:"
    docker-compose logs
    exit 1
fi

# 10. 设置定时清理任务（可选）
print_info "设置定时清理任务..."
(crontab -l 2>/dev/null; echo "0 2 * * * docker system prune -f") | crontab -

print_info "部署完成！"
print_info ""
print_info "常用命令:"
print_info "  查看日志: cd deploy && docker-compose logs -f"
print_info "  重启服务: cd deploy && docker-compose restart"
print_info "  停止服务: cd deploy && docker-compose down"
print_info "  更新代码: git pull && cd deploy && docker-compose up -d --build"
