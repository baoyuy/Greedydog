# Greedydog

一个面向 Binance U 本位合约的量化交易项目，包含：

- 交易策略主程序
- Web Dashboard 可视化界面
- 币安 API 访问适配层
- 运行状态、配置、日志查看能力
- Linux 服务器部署脚本与 PM2 进程管理方案

> ⚠️ 本项目涉及真实交易能力，请务必先使用小资金、模拟环境或最小风险参数验证后再用于实盘。

---

## 项目简介

Greedydog 主要用于运行一套基于 Binance Futures 的自动化交易流程，并通过 Dashboard 页面查看：

- 当前运行状态
- 配置信息
- 日志输出
- 持仓/交易相关信息
- 参数调整与运行控制

项目当前代码结构中，核心入口主要包括：

- `man.py`：策略与交易主逻辑
- `dashboard.py`：Flask Dashboard 服务
- `binance_client.py`：Binance API 客户端
- `adapters/binance_adapter.py`：访问适配层
- `services/`：状态总线与 Dashboard 状态管理

---

## 功能特性

- 支持 Binance U 本位合约交易流程
- 支持 Dashboard 可视化查看运行状态
- 支持通过 `.env` 管理运行参数
- 支持代理配置
- 支持 AI 参数优化相关配置
- 支持 PM2 守护运行与开机自启
- 自带 `deploy.sh`，便于 Linux 服务器快速部署

---

## 项目结构

```text
Greedydog/
├── man.py                      # 核心策略主程序
├── dashboard.py                # Flask Dashboard 入口
├── binance_client.py           # Binance API 客户端
├── adapters/
│   └── binance_adapter.py      # Binance 访问适配层
├── services/
│   ├── state_bus.py            # 状态总线
│   └── dashboard_state.py      # Dashboard 状态聚合
├── templates/                  # Dashboard 页面模板
├── .env.example                # 环境变量模板
├── deploy.sh                   # Linux 一键部署脚本
├── ecosystem.config.js         # PM2 配置
├── DEPLOY.md                   # 详细部署文档
└── requirements.txt            # Python 依赖
```

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/baoyuy/Greedydog.git
cd Greedydog
```

### 2. 创建虚拟环境并安装依赖

#### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. 配置 `.env`

复制配置模板：

#### Linux / macOS

```bash
cp .env.example .env
```

#### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

然后编辑 `.env`，至少确认这些关键项：

```env
SYMBOL=BTCUSDT
INTERVAL=5m
NOTIONAL_USDT=100
TRADE_START_TIME=00:00
TRADE_END_TIME=23:59

USE_PROXY=false
PROXY_URL=

DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080

BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_BASE_URL=https://fapi.binance.com
```

> 提示：完整参数说明请直接查看 `.env.example`，里面已经带了较完整的中文注释。

### 4. 启动 Dashboard

```bash
python dashboard.py
```

默认访问地址：

```text
http://127.0.0.1:8080
```

如果需要启动 Dashboard 时自动带起策略：

```bash
python dashboard.py --auto-start-strategy
```

---

## 本地运行说明

### 仅运行 Dashboard

```bash
python dashboard.py
```

### 指定监听地址和端口

```bash
python dashboard.py --host 0.0.0.0 --port 8081
```

### 自动启动策略

```bash
python dashboard.py --auto-start-strategy
```

---

## 配置说明

### 交易相关

| 参数 | 说明 |
|---|---|
| `SYMBOL` | 交易对，如 `BTCUSDT` |
| `INTERVAL` | K线周期，如 `1m` / `5m` / `15m` |
| `NOTIONAL_USDT` | 每次交易使用的名义金额 |
| `TAKER_FEE_RATE` | 手续费率 |
| `LEVERAGE` | 杠杆倍数 |

### 时间窗口相关

| 参数 | 说明 |
|---|---|
| `TRADE_START_TIME` | 允许开始交易时间 |
| `TRADE_END_TIME` | 允许结束交易时间 |
| `STATUS_INTERVAL_SECONDS` | 状态输出频率 |

### Dashboard 相关

| 参数 | 说明 |
|---|---|
| `DASHBOARD_HOST` | Dashboard 监听地址 |
| `DASHBOARD_PORT` | Dashboard 监听端口 |

### Binance API 相关

| 参数 | 说明 |
|---|---|
| `BINANCE_API_KEY` | 币安 API Key |
| `BINANCE_API_SECRET` | 币安 API Secret |
| `BINANCE_BASE_URL` | 币安接口地址 |

### AI 优化相关

| 参数 | 说明 |
|---|---|
| `AI_ENABLED` | 是否启用 AI 优化 |
| `AI_BASE_URL` | AI 接口基础地址 |
| `AI_API_KEY` | AI 接口密钥 |
| `AI_MODEL` | AI 模型名称 |

---

## Linux 服务器部署

项目已经自带部署脚本和 PM2 配置，适合直接上传到 GitHub 后在 Linux 服务器拉取部署。

### 前置要求

- Linux（Ubuntu / Debian / CentOS）
- Python 3.8+
- Node.js 14+（用于安装 PM2）
- sudo 权限

### 一键部署

```bash
git clone https://github.com/baoyuy/Greedydog.git
cd Greedydog
cp .env.example .env
# 编辑 .env
chmod +x deploy.sh
./deploy.sh
```

脚本会自动完成：

- 检查 Python3 和 PM2
- 安装 PM2（如未安装）
- 安装 `python3-venv`
- 创建虚拟环境
- 安装 Python 依赖
- 创建运行时目录
- 启动应用
- 保存 PM2 进程列表
- 配置开机自启

### 验证部署

```bash
pm2 status
pm2 logs quant-dashboard
```

浏览器访问：

```text
http://服务器IP:你在 .env 中配置的 DASHBOARD_PORT
```

---

## 手动部署

如果你不想使用一键脚本，也可以手动执行：

```bash
apt update
apt install -y python3-venv
npm install -g pm2
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
mkdir -p runtime/logs
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

如果需要直接指定命令启动：

```bash
pm2 start "./.venv/bin/python -- dashboard.py --host 0.0.0.0 --port 8081 --auto-start-strategy" --name quant-dashboard
```

---

## PM2 常用命令

```bash
pm2 list
pm2 status
pm2 show quant-dashboard
pm2 restart quant-dashboard
pm2 stop quant-dashboard
pm2 delete quant-dashboard
pm2 reload quant-dashboard
pm2 logs quant-dashboard
pm2 monit
```

---

## 防火墙与端口

根据 `.env` 中的 `DASHBOARD_PORT` 开放对应端口。

### Ubuntu / Debian

```bash
sudo ufw allow 8081/tcp
sudo ufw reload
```

### CentOS / RHEL

```bash
sudo firewall-cmd --permanent --add-port=8081/tcp
sudo firewall-cmd --reload
```

如果你修改了端口，请把 `8081` 替换为你的实际端口。

---

## 故障排查

### 应用无法启动

```bash
pm2 logs quant-dashboard --err
./.venv/bin/python dashboard.py
```

### 无法访问 Dashboard

```bash
pm2 status
netstat -tlnp | grep <你的端口>
```

### 端口被占用

```bash
lsof -i:<你的端口>
fuser -k <你的端口>/tcp
pm2 delete quant-dashboard
pm2 start ecosystem.config.js
```

### 安装依赖失败

请确认你正在使用虚拟环境，不要直接往系统 Python 安装：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

---

## 详细部署文档

如果你需要更完整的部署说明，请查看：

- [DEPLOY.md](./DEPLOY.md)

---

## 安全提示

- 不要把 `.env` 提交到 GitHub
- 不要把真实 API Key 暴露在截图、日志或文档中
- 建议先使用最小仓位或模拟环境测试
- 对外开放 Dashboard 时，建议配合 Nginx、HTTPS 和访问控制
- 定期备份 `.env` 和运行日志

---

## 免责声明

本项目仅供学习与研究使用。任何因使用本项目进行实盘交易所造成的损失，由使用者自行承担。
