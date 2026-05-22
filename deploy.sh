#!/bin/bash
# ============================================
# 小肥猫学习助手 v2.0 — 一键部署脚本
# 自动完成: 虚拟环境、依赖安装、systemd配置、服务启动
# 用法: bash deploy.sh [--no-systemd]
# ============================================
set -e

RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[36m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERROR]${NC} $1"; }

NO_SYSTEMD=false
if [ "$1" = "--no-systemd" ]; then
    NO_SYSTEMD=true
fi

echo ""
echo "========================================="
echo "  小肥猫学习助手 v2.1 - 纯云端部署"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

# 检查是否为 root（systemd 安装需要）
if [ "$NO_SYSTEMD" = false ] && [ "$EUID" -ne 0 ]; then
    warn "非 root 用户运行，将跳过 systemd 服务安装"
    NO_SYSTEMD=true
fi

# ---- Python 检查 ----
info "检查 Python..."
PYTHON=$(which python3 2>/dev/null || which python 2>/dev/null)
if [ -z "$PYTHON" ]; then
    err "未找到 Python3，请先安装: sudo apt-get install python3"
    exit 1
fi
ok "Python: $($PYTHON --version 2>&1)"

# ---- 虚拟环境 ----
VENV_DIR="/opt/venv"
info "设置虚拟环境..."

if [ ! -d "$VENV_DIR" ]; then
    if [ "$EUID" -eq 0 ]; then
        $PYTHON -m venv "$VENV_DIR"
    else
        VENV_DIR="$(pwd)/venv"
        $PYTHON -m venv "$VENV_DIR"
        warn "非 root 用户，虚拟环境创建在项目目录: $VENV_DIR"
    fi
fi
source "$VENV_DIR/bin/activate"
ok "虚拟环境: $VENV_DIR"

# ---- Python 依赖 ----
info "安装 Python 依赖..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Python 依赖安装完成"

# ---- 数据目录 ----
info "创建数据目录..."
mkdir -p data/sessions data/workspace data/notes data/images
ok "数据目录就绪"

# ---- .env 检查 ----
info "检查 .env 配置文件..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        warn ".env 已从 .env.example 创建，请编辑填入真实密钥！"
        warn "  vim .env"
        exit 1
    else
        err ".env 文件不存在，且无 .env.example 模板"
        exit 1
    fi
else
    ok ".env 文件已存在"
fi

# ---- 先杀掉旧进程 ----
info "清理旧进程..."
# 杀掉所有旧的 gunicorn 进程（避免端口占用导致 systemctl 卡死）
pkill -f "gunicorn server:app" 2>/dev/null || true
# 也杀掉占用 8192 端口的任何进程
OLD_PID=$(lsof -ti:8192 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    kill -9 $OLD_PID 2>/dev/null || true
    warn "已杀掉占用端口8192的旧进程: $OLD_PID"
fi
sleep 1
ok "旧进程已清理"

# ---- systemd 服务 ----
if [ "$NO_SYSTEMD" = false ]; then
    info "安装 systemd 服务..."
    
    # 调整 service 文件中的路径（注意：不用 --daemon，由 systemd 管理进程）
    SERVICE_FILE="cat-learning.service"
    if [ -f "$SERVICE_FILE" ]; then
        WORKDIR=$(pwd)
        sed -i "s|WorkingDirectory=.*|WorkingDirectory=$WORKDIR|" "$SERVICE_FILE"
        sed -i "s|ExecStart=.*|ExecStart=$VENV_DIR/bin/gunicorn server:app --bind 0.0.0.0:8192 --workers 2 --timeout 120 --access-logfile /var/log/cat-learning.log --error-logfile /var/log/cat-learning.log|" "$SERVICE_FILE"
    fi
    
    cp "$SERVICE_FILE" /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable cat-learning
    ok "systemd 服务已安装并设为开机自启"
    
    # 启动服务（加超时，避免卡死）
    info "启动服务（最多等待30秒）..."
    if ! timeout 30 systemctl start cat-learning 2>/dev/null; then
        warn "systemctl 超时，检查状态..."
    fi
    sleep 2
    
    if systemctl is-active --quiet cat-learning; then
        ok "服务启动成功"
    else
        err "服务启动失败，查看日志:"
        echo ""
        systemctl status cat-learning --no-pager -l || true
        echo ""
        echo "--- 最近的应用日志 ---"
        tail -20 /var/log/cat-learning.log 2>/dev/null || echo "  (无日志)"
        echo ""
        warn "尝试手动启动排查问题:"
        echo "  source $VENV_DIR/bin/activate"
        echo "  cd $(pwd)"
        echo "  $VENV_DIR/bin/gunicorn server:app --bind 0.0.0.0:8192 --workers 2"
        exit 1
    fi
else
    # 无 systemd，尝试直接前台启动
    warn "跳过 systemd 安装，直接启动 gunicorn..."
    info "启动命令:"
    echo "  $VENV_DIR/bin/gunicorn server:app --bind 0.0.0.0:8192 --workers 2 --timeout 120 --access-logfile - --error-logfile -"
    echo ""
fi

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
echo "  健康检查: curl http://localhost:8192/health"
echo "  测试脚本: bash test.sh"
echo "  查看日志: sudo journalctl -u cat-learning -f"
echo "  应用日志: tail -f /var/log/cat-learning.log"
echo ""
echo "  飞书回调地址: http://$(hostname -I | awk '{print $1}'):8192/feishu/event"
echo ""
