# BiliView 项目规则

## 安全提交规则（重要！）

### 提交前必须检查

在每次执行 `git commit` 之前，**必须**执行以下检查：

#### 1. 检查敏感信息

运行以下命令检查是否包含敏感信息：

```bash
# 检查 IP 地址
git diff --cached | grep -E "\b([0-9]{1,3}\.){3}[0-9]{1,3}\b"

# 检查密码/密钥模式
git diff --cached | grep -iE "(password|passwd|pwd|secret|key|token|api_key)\"?\s*[=:]\s*\"?[^\"\s]{8,}"

# 检查常见密码模式
git diff --cached | grep -E "(Aq[0-9]{6,}|password[0-9]{3,}|admin[0-9]{3,})"
```

#### 2. 禁止提交的敏感信息

以下信息**绝对禁止**提交到 git：

- [ ] 服务器 IP 地址（如 `124.222.168.33`）
- [ ] 服务器密码或 SSH 密钥
- [ ] API Keys（如 `X666_API_KEY`, `GROQ_API_KEY` 的实际值）
- [ ] 数据库密码
- [ ] 任何生产环境的凭据

#### 3. 替代方案

敏感信息应使用占位符：

```python
# ❌ 错误
HOST = "124.222.168.33"
PASSWORD = "Aq123123"
API_KEY = "sk-actual-key-12345"

# ✅ 正确
HOST = "your_server_ip"
PASSWORD = "your_password"
API_KEY = "your_api_key_here"
```

#### 4. 自动化检查脚本

创建 `pre-commit` 钩子脚本（`.git/hooks/pre-commit`）：

```bash
#!/bin/bash
# 检查敏感信息

# 定义敏感模式
SENSITIVE_PATTERNS=(
    "124\.222\.168\.33"  # 服务器 IP
    "Aq123123"           # 已知密码
    "[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}"  # 任意 IP
)

# 检查暂存区
for pattern in "${SENSITIVE_PATTERNS[@]}"; do
    if git diff --cached | grep -qE "$pattern"; then
        echo "❌ 错误：检测到敏感信息匹配模式: $pattern"
        echo "请检查以下内容："
        git diff --cached | grep -nE "$pattern"
        exit 1
    fi
done

echo "✅ 敏感信息检查通过"
exit 0
```

赋予执行权限：
```bash
chmod +x .git/hooks/pre-commit
```

---

## 项目开发规则

### 代码规范

1. **Python 代码**：遵循 PEP 8 规范
2. **提交信息**：使用 Conventional Commits 格式
   - `feat:` 新功能
   - `fix:` 修复问题
   - `docs:` 文档更新
   - `perf:` 性能优化
   - `refactor:` 代码重构

### 分支管理

- `main`: 主分支，保持稳定
- `feature/*`: 功能分支
- `fix/*`: 修复分支

### 部署相关

所有部署相关文件放在 `deploy/` 目录：
- `Dockerfile` - Docker 镜像配置
- `docker-compose.yml` - 服务编排
- `deploy.sh` - 部署脚本
- `deploy_remote.py` - 远程部署脚本
- `README.md` - 部署文档

---

## 历史教训

### 2026-03-14 安全事件

**问题**：在提交 `d017dc7` 中意外提交了服务器密码 `Aq123123` 和 IP `124.222.168.33`。

**解决**：使用 `git-filter-repo` 从历史中彻底移除敏感信息。

**预防措施**：
1. 建立此安全提交规则文档
2. 配置 pre-commit 钩子
3. 提交前强制检查

---

**最后更新**: 2026-03-14
