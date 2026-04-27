# 量化策略 Dashboard 部署教程

## 一键部署（推荐）

### 前置要求

- **操作系统**: Linux (Ubuntu/Debian/CentOS)
- **Python**: 3.8+
- **Node.js**: 14+ (用于安装 PM2)
- **权限**: sudo 权限（用于安装依赖和配置开机自启）

### 部署步骤

#### 1. 上传代码到服务器

```bash
# 方式 1: 使用 git
git clone <你的仓库地址>
cd lianghua_bian_04

# 方式 2: 使用 scp 上传
scp -r lianghua_bian_04 user@server:/path/to/deploy/
ssh user@server
cd /path/to/deploy/lianghua_bian_04
```

#### 2. 配置 .env 文件

```bash
# 确保 .env 文件已配置好所有必要参数
# 特别是以下关键配置：
# - SYMBOL, INTERVAL, NOTIONAL_USDT
# - AI_BASE_URL, AI_API_KEY, AI_MODEL
# - TRADE_START_TIME, TRADE_END_TIME
```

#### 3. 执行一键部署脚本

```bash
chmod +x deploy.sh
./deploy.sh
```

脚本会自动完成：
- ✅ 检查 Python3 和 PM2
- ✅ 安装 PM2（如果未安装）
- ✅ 安装 python3-venv
- ✅ 创建 Python 虚拟环境
- ✅ 安装 Python 依赖
- ✅ 创建运行时目录
- ✅ 启动应用
- ✅ 保存进程列表
- ✅ 配置开机自启

#### 4. 验证部署

```bash
# 查看进程状态
pm2 status

# 查看实时日志
pm2 logs quant-dashboard

# 访问 Dashboard
# 浏览器打开: http://服务器IP:你在 .env 中配置的 DASHBOARD_PORT
```

---

## 手动部署（高级）

### 1. 安装依赖

```bash
# 安装系统依赖
apt update
apt install -y python3-venv

# 安装 PM2
npm install -g pm2

# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境并安装 Python 依赖
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

### 2. 启动应用

```bash
# 创建运行时目录
mkdir -p runtime/logs

# 使用 ecosystem 配置启动
pm2 start ecosystem.config.js

# 或直接启动（需要根据 .env 中的配置调整端口）
pm2 start "./.venv/bin/python -- dashboard.py --host 0.0.0.0 --port 8081 --auto-start-strategy" --name quant-dashboard
```

### 3. 配置开机自启

```bash
# 保存当前进程列表
pm2 save

# 生成开机启动脚本
pm2 startup

# 执行输出的 sudo 命令（类似下面这样）
sudo env PATH=$PATH:/usr/bin pm2 startup systemd -u your_user --hp /home/your_user
```

---

## PM2 常用命令

### 进程管理

```bash
# 查看所有进程
pm2 list
pm2 status

# 查看详细信息
pm2 show quant-dashboard

# 重启应用
pm2 restart quant-dashboard

# 停止应用
pm2 stop quant-dashboard

# 删除应用
pm2 delete quant-dashboard

# 重载应用（0 秒停机）
pm2 reload quant-dashboard
```

### 日志管理

```bash
# 查看实时日志
pm2 logs quant-dashboard

# 查看错误日志
pm2 logs quant-dashboard --err

# 查看输出日志
pm2 logs quant-dashboard --out

# 清空日志
pm2 flush

# 查看最近 100 行
pm2 logs quant-dashboard --lines 100
```

### 监控

```bash
# 实时监控
pm2 monit

# 查看资源占用
pm2 status
```

---

## 配置说明

### ecosystem.config.js

**注意**：从最新版本开始，`ecosystem.config.js` 会自动从 `.env` 文件读取 `DASHBOARD_HOST`、`DASHBOARD_PORT` 和 `DASHBOARD_AUTO_START_STRATEGY`，无需手动修改配置文件。

如需修改端口，直接编辑 `.env` 文件：

```bash
DASHBOARD_PORT=9000
```

然后重启：

```bash
pm2 restart quant-dashboard
```

### 修改端口

**推荐方式**：直接修改 `.env` 文件中的 `DASHBOARD_PORT`：

```bash
# .env
DASHBOARD_PORT=9000
```

然后重启应用：

```bash
pm2 restart quant-dashboard
```

---

## 防火墙配置

根据你在 `.env` 中设置的端口开放防火墙（默认 8081）：

### Ubuntu/Debian (ufw)

```bash
# 如果使用默认端口 8081
sudo ufw allow 8081/tcp
sudo ufw reload

# 如果使用其他端口，替换为实际端口号
sudo ufw allow <你的端口>/tcp
sudo ufw reload
```

### CentOS/RHEL (firewalld)

```bash
# 如果使用默认端口 8081
sudo firewall-cmd --permanent --add-port=8081/tcp
sudo firewall-cmd --reload

# 如果使用其他端口，替换为实际端口号
sudo firewall-cmd --permanent --add-port=<你的端口>/tcp
sudo firewall-cmd --reload
```

---

## 故障排查

### 1. 应用无法启动

```bash
# 查看错误日志
pm2 logs quant-dashboard --err

# 检查虚拟环境
ls -la .venv/bin/python

# 手动测试启动
./.venv/bin/python dashboard.py
```

### 2. 无法访问 Dashboard

```bash
# 检查端口是否监听（根据 .env 中的 DASHBOARD_PORT）
netstat -tlnp | grep <你的端口>

# 检查防火墙
sudo ufw status
sudo firewall-cmd --list-ports

# 检查进程状态
pm2 status
```

### 3. 端口被占用

如果日志里出现：

```bash
Address already in use
Port XXXX is in use by another program
```

可以执行：

```bash
# 查看是谁占用了端口（替换为你的端口号）
lsof -i:<你的端口>

# 或者直接释放端口
fuser -k <你的端口>/tcp

# 删除旧的 PM2 进程并重新启动
pm2 delete quant-dashboard
pm2 start ecosystem.config.js
```

最新的 `deploy.sh` 已经内置了启动前自动从 `.env` 读取端口并清理占用的逻辑，重新执行即可：

```bash
chmod +x deploy.sh
./deploy.sh
```

### 4. 策略线程未启动

检查 `.env` 文件中：

```bash
DASHBOARD_AUTO_START_STRATEGY=true
```

或在启动命令中添加 `--auto-start-strategy`。

### 4. 内存占用过高

调整 `ecosystem.config.js` 中的 `max_memory_restart`：

```javascript
max_memory_restart: '1G',  // 改为 1GB
```

### 5. pip 安装依赖失败

如果遇到 `externally-managed-environment` 错误：

```bash
# 确保使用虚拟环境
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

**不要使用** `pip3 install --break-system-packages`，这会破坏系统 Python。

---

## 更新部署

```bash
# 拉取最新代码
git pull

# 激活虚拟环境并安装新依赖
./.venv/bin/pip install -r requirements.txt

# 重启应用
pm2 restart quant-dashboard

# 或重载（0 秒停机）
pm2 reload quant-dashboard
```

---

## 卸载

```bash
# 停止并删除应用
pm2 delete quant-dashboard

# 删除保存的进程列表
pm2 save --force

# 移除开机自启
pm2 unstartup systemd

# 删除虚拟环境
rm -rf .venv
```

---

## 虚拟环境说明

### 为什么使用虚拟环境？

从 Python 3.11+ 和 Debian 12+ 开始，系统禁止直接用 `pip` 安装包到系统 Python，必须使用虚拟环境。

### 虚拟环境的好处

1. **隔离依赖**: 不同项目的依赖互不影响
2. **系统安全**: 不会破坏系统 Python
3. **版本管理**: 每个项目可以使用不同版本的包
4. **易于清理**: 删除 `.venv` 目录即可完全清理

### 常用虚拟环境命令

```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境（手动操作时）
source .venv/bin/activate

# 退出虚拟环境
deactivate

# 在虚拟环境中安装包
./.venv/bin/pip install package_name

# 查看虚拟环境中的包
./.venv/bin/pip list
```

---

## 生产环境建议

1. **使用反向代理**: 建议使用 Nginx 作为反向代理
2. **HTTPS**: 配置 SSL 证书
3. **日志轮转**: 配置 logrotate 定期清理日志
4. **监控告警**: 使用 PM2 Plus 或其他监控工具
5. **备份**: 定期备份 `.env` 和交易记录
6. **资源限制**: 根据服务器配置调整 `max_memory_restart`
7. **虚拟环境**: 始终使用虚拟环境，不要用系统 Python

---

## 技术支持

如遇问题，请检查：

1. PM2 日志: `pm2 logs quant-dashboard`
2. 应用日志: `./runtime/logs/`
3. Python 版本: `python3 --version`
4. 虚拟环境: `ls -la .venv/bin/python`
5. 依赖版本: `./.venv/bin/pip list`
