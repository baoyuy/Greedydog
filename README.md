# Greedydog - 币安量化交易系统

基于币安 Futures API 的量化交易系统，支持实时监控、策略回测和自动交易。

## 功能特性

- 🔄 实时行情监控与交易执行
- 📊 Web Dashboard 可视化界面
- 🤖 AI 辅助参数优化
- 📈 交易记录与持仓管理
- 🔐 安全的 API 密钥管理
- 🎯 支持模拟盘与实盘切换

## 快速开始

### 环境要求

- Python 3.8+
- pip

### 安装步骤

1. **克隆仓库**

```bash
git clone https://github.com/baoyuy/Greedydog.git
cd Greedydog
```

2. **安装依赖**

```bash
pip install -r requirements.txt
```

3. **配置环境变量**

复制示例配置文件并编辑：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置必要参数：

```bash
# 交易配置
SYMBOL=BTCUSDT
INTERVAL=1m
NOTIONAL_USDT=100

# 币安 API（实盘交易需要）
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_BASE_URL=https://fapi.binance.com

# Dashboard 配置
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=8080
```

4. **启动应用**

```bash
# 仅启动 Dashboard
python dashboard.py

# 启动 Dashboard 并自动开始策略
python dashboard.py --auto-start-strategy
```

访问 `http://127.0.0.1:8080` 查看 Dashboard。

## 项目结构

```
Greedydog/
├── man.py                      # 核心策略引擎
├── dashboard.py                # Web Dashboard
├── binance_client.py           # 币安 API 客户端
├── adapters/
│   └── binance_adapter.py      # 币安访问适配层
├── services/
│   ├── state_bus.py            # 状态总线
│   └── dashboard_state.py      # Dashboard 状态管理
├── templates/                  # HTML 模板
├── requirements.txt            # Python 依赖
└── .env.example                # 配置示例
```

## 配置说明

### 交易模式

- `TRADING_MODE=paper` - 模拟盘（默认）
- `TRADING_MODE=live` - 实盘交易（需配置 API Key）

### 关键参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `SYMBOL` | 交易对 | `BTCUSDT` |
| `INTERVAL` | K线周期 | `1m` |
| `NOTIONAL_USDT` | 每次交易金额 | `100` |
| `DASHBOARD_PORT` | Dashboard 端口 | `8080` |

完整配置请参考 `.env.example`。

## 生产部署

详细部署文档请查看 [DEPLOY.md](./DEPLOY.md)，包含：

- PM2 进程管理
- 虚拟环境配置
- 开机自启设置
- 故障排查指南

## 安全提示

⚠️ **重要**：
- 不要将 `.env` 文件提交到 Git
- API Key 仅授予必要权限
- 建议先在模拟盘测试
- 实盘交易请谨慎设置仓位

## 依赖项

- Flask - Web 框架
- pandas - 数据处理
- requests - HTTP 客户端
- python-dotenv - 环境变量管理

## License

MIT

## 免责声明

本项目仅供学习交流使用，使用本系统进行实盘交易的风险由使用者自行承担。
