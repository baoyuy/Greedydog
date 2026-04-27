#!/bin/bash
set -e

echo "=========================================="
echo "量化策略 Dashboard 一键部署脚本"
echo "=========================================="

if ! command -v python3 &> /dev/null; then
    echo "❌ 未检测到 python3，请先安装 Python 3.8+"
    exit 1
fi

echo "✅ Python3 已安装: $(python3 --version)"

if ! command -v pm2 &> /dev/null; then
    echo "⚠️  未检测到 PM2，正在安装..."
    if command -v npm &> /dev/null; then
        npm install -g pm2
        echo "✅ PM2 安装完成"
    else
        echo "❌ 未检测到 npm，请先安装 Node.js 和 npm"
        exit 1
    fi
else
    echo "✅ PM2 已安装: $(pm2 --version)"
fi

if ! dpkg -s python3-venv >/dev/null 2>&1; then
    echo "📦 安装 python3-venv..."
    apt update
    apt install -y python3-venv
fi

if [ ! -f ".env" ]; then
    echo "⚠️  未检测到 .env 文件，请先配置 .env"
    exit 1
fi

echo "✅ .env 文件已存在"

echo ""
echo "🐍 创建虚拟环境..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

echo ""
echo "📦 安装 Python 依赖..."
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

echo ""
echo "🗂️  创建运行时目录..."
mkdir -p runtime/logs

echo ""
echo "🔄 停止旧进程..."
pm2 delete quant-dashboard 2>/dev/null || true

echo ""
echo "🔍 检查端口占用..."
DASHBOARD_PORT=$(grep -E "^DASHBOARD_PORT=" .env | cut -d'=' -f2 | tr -d ' ')
DASHBOARD_PORT=${DASHBOARD_PORT:-8080}

if command -v fuser &> /dev/null; then
    fuser -k ${DASHBOARD_PORT}/tcp 2>/dev/null || true
elif command -v lsof &> /dev/null; then
    lsof -ti:${DASHBOARD_PORT} | xargs kill -9 2>/dev/null || true
fi
sleep 1

echo ""
echo "🚀 启动应用..."
pm2 start ecosystem.config.js

echo ""
echo "💾 保存进程列表..."
pm2 save

echo ""
echo "⚙️  配置开机自启..."
pm2 startup | grep -E "^sudo" | bash || true

echo ""
echo "=========================================="
echo "✅ 部署完成！"
echo "=========================================="
echo ""
echo "📊 查看状态: pm2 status"
echo "📋 查看日志: pm2 logs quant-dashboard"
echo "🔄 重启应用: pm2 restart quant-dashboard"
echo "⏹️  停止应用: pm2 stop quant-dashboard"
echo "🗑️  删除应用: pm2 delete quant-dashboard"
echo ""
echo "🌐 访问地址: http://服务器IP:${DASHBOARD_PORT}"
echo ""
