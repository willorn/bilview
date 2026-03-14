#!/usr/bin/env python3
"""
远程部署脚本 - 用于从本地一键部署到远程服务器

使用方法:
1. 安装依赖: pip3 install paramiko
2. 修改下面的服务器配置
3. 运行: python3 deploy_remote.py
"""

import paramiko
import os
import sys
import time

# ==================== 服务器配置 ====================
# 请修改以下配置为你的服务器信息
HOST = "your_server_ip"  # 服务器 IP
PORT = 22  # SSH 端口
USERNAME = "ubuntu"  # 用户名
PASSWORD = "your_password"  # 密码
REMOTE_PATH = "/opt/bilview"  # 远程部署路径
# ==================================================


def create_ssh_client():
    """创建 SSH 客户端连接"""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, PORT, USERNAME, PASSWORD, timeout=30)
    return client


def run_command(client, command, timeout=600, print_output=True):
    """在远程服务器执行命令"""
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=True)
    
    # 实时输出
    output_lines = []
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            line = stdout.channel.recv(1024).decode('utf-8', errors='ignore')
            output_lines.append(line)
            if print_output:
                print(line, end='')
    
    # 获取剩余输出
    remaining = stdout.read().decode('utf-8', errors='ignore')
    if remaining:
        output_lines.append(remaining)
        if print_output:
            print(remaining, end='')
    
    exit_status = stdout.channel.recv_exit_status()
    error = stderr.read().decode('utf-8', errors='ignore')
    
    return exit_status, ''.join(output_lines), error


def upload_directory(sftp, local_dir, remote_dir, client):
    """递归上传目录"""
    import tarfile
    import tempfile
    
    # 创建临时 tar 文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.tar.gz', delete=False) as tmp:
        tmp_path = tmp.name
    
    # 打包目录
    print(f"  打包目录: {local_dir}")
    with tarfile.open(tmp_path, "w:gz") as tar:
        tar.add(local_dir, arcname=os.path.basename(local_dir))
    
    # 上传 tar 文件
    remote_tar = f"/tmp/{os.path.basename(local_dir)}.tar.gz"
    print(f"  上传: {local_dir} -> {remote_tar}")
    sftp.put(tmp_path, remote_tar)
    
    # 在服务器上解压
    run_command(client, f"cd {remote_dir} && tar -xzf {remote_tar} && rm {remote_tar}", print_output=False)
    
    # 清理临时文件
    os.unlink(tmp_path)


def main():
    print("=" * 60)
    print("BiliView 远程部署脚本")
    print("=" * 60)
    
    # 获取项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    
    try:
        # 1. 连接服务器
        print("\n[1/6] 连接到服务器...")
        client = create_ssh_client()
        sftp = client.open_sftp()
        print("✓ 连接成功")
        
        # 2. 创建远程目录
        print("\n[2/6] 创建远程目录...")
        run_command(client, f"sudo mkdir -p {REMOTE_PATH}", print_output=False)
        run_command(client, f"sudo chown -R {USERNAME}:{USERNAME} {REMOTE_PATH}", print_output=False)
        print("✓ 目录创建成功")
        
        # 3. 上传部署文件
        print("\n[3/6] 上传部署文件...")
        deploy_files = ["Dockerfile", "docker-compose.yml", "nginx.conf"]
        for file in deploy_files:
            local_file = os.path.join(script_dir, file)
            if os.path.exists(local_file):
                remote_file = f"{REMOTE_PATH}/deploy/{file}"
                run_command(client, f"mkdir -p {REMOTE_PATH}/deploy", print_output=False)
                sftp.put(local_file, remote_file)
                print(f"  ✓ {file}")
        
        # 4. 上传项目代码
        print("\n[4/6] 上传项目代码...")
        key_files = ["app.py", "requirements.txt", "config.py"]
        for file in key_files:
            local_file = os.path.join(project_root, file)
            if os.path.exists(local_file):
                remote_file = f"{REMOTE_PATH}/{file}"
                sftp.put(local_file, remote_file)
                print(f"  ✓ {file}")
        
        # 上传目录
        dirs_to_upload = ["core", "db", "utils", "pages"]
        for dir_name in dirs_to_upload:
            local_dir = os.path.join(project_root, dir_name)
            if os.path.exists(local_dir):
                upload_directory(sftp, local_dir, REMOTE_PATH, client)
                print(f"  ✓ {dir_name}/")
        
        sftp.close()
        print("✓ 代码上传完成")
        
        # 5. 检查 Docker
        print("\n[5/6] 检查 Docker 环境...")
        exit_code, output, error = run_command(client, "docker --version", print_output=False)
        if exit_code == 0:
            print(f"  {output.strip()}")
        else:
            print("  Docker 未安装，正在安装...")
            run_command(client, "curl -fsSL https://get.docker.com | sh", timeout=300)
            run_command(client, "sudo systemctl enable docker && sudo systemctl start docker")
            print("  ✓ Docker 安装完成")
        
        # 6. 构建和启动
        print("\n[6/6] 构建并启动 Docker 容器...")
        print("  这可能需要 5-15 分钟，请耐心等待...\n")
        
        compose_cmd = f"""
cd {REMOTE_PATH}/deploy

# 停止旧容器
echo "=== 停止旧容器 ==="
docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true

# 构建镜像
echo ""
echo "=== 构建 Docker 镜像 ==="
docker compose build --no-cache 2>/dev/null || docker-compose build --no-cache

# 启动容器
echo ""
echo "=== 启动容器 ==="
docker compose up -d 2>/dev/null || docker-compose up -d

# 等待服务启动
echo ""
echo "=== 等待服务启动 ==="
sleep 15

# 检查状态
echo ""
echo "=== 容器状态 ==="
docker ps | grep bilview

echo ""
echo "=== 服务日志 ==="
docker compose logs --tail=20 2>/dev/null || docker-compose logs --tail=20
"""
        
        exit_code, output, error = run_command(client, compose_cmd, timeout=900)
        
        # 关闭连接
        client.close()
        
        print("\n" + "=" * 60)
        print("部署完成！")
        print("=" * 60)
        print(f"\n✓ 访问地址: http://{HOST}:8501")
        print(f"\n⚠ 重要提醒:")
        print(f"  你需要配置 API Keys 才能正常使用！")
        print(f"\n  请运行以下命令编辑 .env 文件:")
        print(f"  ssh {USERNAME}@{HOST}")
        print(f"  nano {REMOTE_PATH}/.env")
        print(f"\n  填入你的 API Keys 后，重启服务:")
        print(f"  cd {REMOTE_PATH}/deploy && docker compose restart")
        print(f"\n常用命令:")
        print(f"  查看日志: ssh {USERNAME}@{HOST} 'cd {REMOTE_PATH}/deploy && docker compose logs -f'")
        print(f"  重启服务: ssh {USERNAME}@{HOST} 'cd {REMOTE_PATH}/deploy && docker compose restart'")
        print(f"  停止服务: ssh {USERNAME}@{HOST} 'cd {REMOTE_PATH}/deploy && docker compose down'")
        
    except Exception as e:
        print(f"\n✗ 部署失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
