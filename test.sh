#!/bin/bash
# ============================================
# 小肥猫学习助手 - 端到端测试脚本
# 用法: bash test.sh [服务器IP] [端口]
# 示例: bash test.sh 10.100.13.215 8192
# ============================================
set -e

HOST="${1:-localhost}"
PORT="${2:-8192}"
BASE="http://${HOST}:${PORT}"
PASS=0
FAIL=0

green() { echo -e "\033[32m[PASS]\033[0m $1"; }
red()   { echo -e "\033[31m[FAIL]\033[0m $1"; }
info()  { echo -e "\033[36m[INFO]\033[0m $1"; }

check() {
    local desc="$1" method="$2" url="$3" expected_code="$4" expected_body="$5" post_data="$6"
    local http_code body
    
    if [ "$method" = "POST" ]; then
        http_code=$(curl -s -o /tmp/cat_test_resp.txt -w "%{http_code}" -X POST "$url" -H "Content-Type: application/json" -d "$post_data" 2>/dev/null)
        body=$(cat /tmp/cat_test_resp.txt)
    else
        http_code=$(curl -s -o /tmp/cat_test_resp.txt -w "%{http_code}" "$url" 2>/dev/null)
        body=$(cat /tmp/cat_test_resp.txt)
    fi
    
    if [ "$http_code" = "$expected_code" ]; then
        if [ -n "$expected_body" ]; then
            if echo "$body" | grep -q "$expected_body"; then
                green "$desc (code=$http_code, body ok)"
                PASS=$((PASS+1))
            else
                red "$desc - body不匹配: 期望包含 '$expected_body'"
                echo "    实际返回: $body"
                FAIL=$((FAIL+1))
            fi
        else
            green "$desc (code=$http_code)"
            PASS=$((PASS+1))
        fi
    else
        red "$desc - HTTP $http_code (期望 $expected_code)"
        echo "    返回内容: $body"
        FAIL=$((FAIL+1))
    fi
}

echo "========================================="
echo "  小肥猫学习助手 - 端到端测试"
echo "  目标: $BASE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="
echo ""

# 1. 健康检查
info "测试1: 健康检查 /health"
check "健康检查" "GET" "$BASE/health" "200" "ok"

# 2. 首页检查
info "测试2: 首页 /"
check "首页" "GET" "$BASE/" "200" "小肥猫学习助手"

# 3. 飞书事件回调端点
info "测试3: 飞书回调 /feishu/event"
check "飞书回调可达性" "POST" "$BASE/feishu/event" "200" "" '{"challenge":"test123","token":"test","type":"url_verification"}'

# 4. 管理员初始化检查
info "测试4: 管理员 /admin/init"
check "管理员初始化" "POST" "$BASE/admin/init" "200" "" '{"password":"test123456"}'

# 5. 静态文件检查
info "测试5: 静态文件 /static/"
static_code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/static/" 2>/dev/null)
echo "  静态文件返回 HTTP $static_code"

echo ""
echo "========================================="
echo "  测试结果汇总"
echo "========================================="
echo -e "  \033[32m通过: $PASS\033[0m"
if [ "$FAIL" -gt 0 ]; then
    echo -e "  \033[31m失败: $FAIL\033[0m"
    exit 1
else
    echo -e "  \033[31m失败: $FAIL\033[0m"
fi

if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "  🎉 所有测试通过！服务运行正常。"
else
    echo ""
    echo "  ⚠️  存在失败项，请检查服务日志: sudo journalctl -u cat-learning -f"
    exit 1
fi
