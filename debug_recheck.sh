#!/bin/bash
# 小肥猫学习助手 - recheck 调试脚本
# 在 jumpserver 上运行: bash debug_recheck.sh

echo "=== 1. 服务状态 ==="
systemctl status cat-learning --no-pager -l 2>/dev/null || echo "systemctl 不可用"

echo ""
echo "=== 2. 最近日志（最后30行）==="
journalctl -u cat-learning --no-pager -n 30 2>/dev/null || echo "journalctl 不可用"

echo ""
echo "=== 3. 检查 V0616A 存档 ==="
DATA_DIR="/opt/cat-learning/data"
if [ -d "$DATA_DIR/questions" ]; then
    echo "questions 目录内容:"
    ls -la "$DATA_DIR/questions/"
    echo ""
    echo "搜索 V0616A:"
    grep -rl "V0616A" "$DATA_DIR/questions/" 2>/dev/null || echo "未找到 V0616A"
    grep -rl "V0616A" "$DATA_DIR/today_questions.json" 2>/dev/null || echo "today_questions.json 中也未找到"
else
    echo "data/questions 目录不存在！"
fi

echo ""
echo "=== 4. 检查服务是否在监听 ==="
curl -s http://localhost:8192/health 2>/dev/null || echo "服务未响应"

echo ""
echo "=== 5. 手动触发轮询 ==="
curl -s -X POST http://localhost:8192/feishu/poll 2>/dev/null || echo "轮询触发失败"

echo ""
echo "=== 6. 测试飞书通道 ==="
curl -s http://localhost:8192/test_push 2>/dev/null || echo "测试推送失败"

echo ""
echo "=== 7. 检查代码版本 ==="
cd /opt/cat-learning && git --no-pager log --oneline -3 2>/dev/null || echo "git 不可用"

echo ""
echo "=== 8. 检查 Python 进程 ==="
ps aux | grep -E "server.py|gunicorn" | grep -v grep || echo "无 Python 进程"
