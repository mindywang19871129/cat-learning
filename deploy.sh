#!/bin/bash
set -e

echo "🐱 小肥猫学习助手 v2.0 — 部署开始"
echo "================================"

PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    echo "❌ 未找到 Python3"
    exit 1
fi
echo "✅ Python: $($PYTHON --version 2>&1)"

# 虚拟环境
if [ ! -d "venv" ]; then
    $PYTHON -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt -q

# OCR引擎
if ! command -v tesseract &> /dev/null; then
    echo "⚠️  安装 tesseract-ocr..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update -qq && sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
    elif command -v yum &> /dev/null; then
        sudo yum install -y tesseract tesseract-langpack-chi-sim
    fi
else
    echo "✅ tesseract 已安装"
fi

mkdir -p data/sessions data/workspace data/images

# .env检查
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "⚠️  请编辑 .env 填入飞书凭证"
    exit 1
fi

echo ""
echo "🚀 启动服务（端口 8192）"
echo "   飞书事件端点: http://YOUR_IP:8192/feishu/event"
echo ""

gunicorn server:app --bind 0.0.0.0:8192 --workers 2 --timeout 120 --access-logfile - --error-logfile -
