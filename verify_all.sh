#!/bin/bash
# ============================================
# 小肥猫学习助手 - 飞书全链路验证脚本
# 在服务器上执行: bash verify_all.sh
# ============================================
set -e

GREEN='\033[32m'
RED='\033[31m'
YELLOW='\033[33m'
BLUE='\033[36m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS+1)); }
fail() { echo -e "${RED}[FAIL]${NC} $1"; FAIL=$((FAIL+1)); }
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "========================================="
echo "  小肥猫学习助手 v2.5 - 全链路验证"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

# ─── 1. 基础服务检查 ───
echo ""
echo "━━━ 第一步：基础服务 ━━━"

info "1.1 服务进程"
if systemctl is-active --quiet cat-learning; then
    pass "cat-learning 服务运行中"
else
    fail "cat-learning 服务未运行"
fi

info "1.2 健康检查"
HEALTH=$(curl -s http://localhost:8192/health 2>/dev/null)
if echo "$HEALTH" | grep -q '"ok"'; then
    pass "健康检查通过: $HEALTH"
else
    fail "健康检查失败: $HEALTH"
fi

info "1.3 端口监听"
if lsof -i :8192 2>/dev/null | grep -q LISTEN; then
    pass "端口8192已监听"
else
    fail "端口8192未监听"
fi

# ─── 2. API Token检查 ───
echo ""
echo "━━━ 第二步：API Token ━━━"

source /opt/venv/bin/activate 2>/dev/null || true

info "2.1 DeepSeek API Key"
DS_KEY=$(grep DEEPSEEK_API_KEY /opt/cat-learning/.env 2>/dev/null | cut -d= -f2 | tr -d ' ')
if [ -n "$DS_KEY" ] && [ "$DS_KEY" != "你的DeepSeek_API_Key" ]; then
    pass "DEEPSEEK_API_KEY 已配置"
else
    fail "DEEPSEEK_API_KEY 未配置或为默认值"
fi

info "2.2 飞书 App ID"
FS_ID=$(grep FEISHU_APP_ID /opt/cat-learning/.env 2>/dev/null | cut -d= -f2 | tr -d ' ')
if [ -n "$FS_ID" ] && [ "$FS_ID" != "cli_xxxxxxxxxxxx" ]; then
    pass "FEISHU_APP_ID 已配置"
else
    fail "FEISHU_APP_ID 未配置或为默认值"
fi

info "2.3 飞书 App Secret"
FS_SEC=$(grep FEISHU_APP_SECRET /opt/cat-learning/.env 2>/dev/null | cut -d= -f2 | tr -d ' ')
if [ -n "$FS_SEC" ] && [ "$FS_SEC" != "你的App_Secret" ]; then
    pass "FEISHU_APP_SECRET 已配置"
else
    fail "FEISHU_APP_SECRET 未配置或为默认值"
fi

# ─── 3. 飞书 Token 获取 ───
echo ""
echo "━━━ 第三步：飞书API连通性 ━━━"

info "3.1 飞书 access_token"
FS_RESULT=$(python3 -c "
import sys; sys.path.insert(0,'/opt/cat-learning')
from core import _get_feishu_token
token = _get_feishu_token()
print('OK:' + token[:20] if token else 'FAIL')
" 2>&1)
if echo "$FS_RESULT" | grep -q "^OK:"; then
    pass "飞书Token获取成功 (${FS_RESULT#OK:}...)"
else
    fail "飞书Token获取失败: $FS_RESULT"
fi

# ─── 4. 飞书 OCR 测试 ───
echo ""
echo "━━━ 第四步：飞书OCR识别 ━━━"

info "4.1 创建测试图片"
python3 -c "
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (500, 150), 'white')
d = ImageDraw.Draw(img)
d.text((20, 30), '第Fri-M1题: 12÷4=3块', fill='black')
d.text((20, 70), '第Fri-M2题: 大米更重', fill='black')
d.text((20, 110), '答: 3块和更重的', fill='black')
img.save('/tmp/ocr_verify.png')
" 2>&1 && pass "测试图片已创建" || fail "测试图片创建失败"

info "4.2 飞书OCR调用"
OCR_RESULT=$(python3 -c "
import sys, json; sys.path.insert(0,'/opt/cat-learning')
from core import ocr_image
r = json.loads(ocr_image('/tmp/ocr_verify.png'))
print(json.dumps(r, ensure_ascii=False))
" 2>&1)

OCR_ENGINE=$(echo "$OCR_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('engine','none'))" 2>/dev/null || echo "parse_error")
OCR_SUCCESS=$(echo "$OCR_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success',False))" 2>/dev/null || echo "False")
OCR_TEXT=$(echo "$OCR_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text','')[:80])" 2>/dev/null || echo "")

if [ "$OCR_SUCCESS" = "True" ]; then
    pass "OCR成功: engine=$OCR_ENGINE, 文字=$OCR_TEXT"
else
    fail "OCR失败: engine=$OCR_ENGINE"
    echo "    详细结果: $OCR_RESULT"
fi

# ─── 5. DeepSeek LLM 测试 ───
echo ""
echo "━━━ 第五步：LLM API ━━━"

info "5.1 DeepSeek调用"
LLM_RESULT=$(python3 -c "
import sys; sys.path.insert(0,'/opt/cat-learning')
from core import call_llm
r = call_llm('请只回复\"LLM测试成功\"这5个字，不要其他内容')
print(r.strip()[:50])
" 2>&1)
if echo "$LLM_RESULT" | grep -q "成功"; then
    pass "LLM API正常: $LLM_RESULT"
else
    fail "LLM API异常: $LLM_RESULT"
fi

# ─── 6. 轮询配置检查 ───
echo ""
echo "━━━ 第六步：消息轮询 ━━━"

info "6.1 轮询状态"
POLL_INFO=$(curl -s http://localhost:8192/feishu/config 2>/dev/null)
POLL_ENABLED=$(echo "$POLL_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('enabled',False))" 2>/dev/null || echo "False")
POLL_CHATS=$(echo "$POLL_INFO" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('chat_ids',[])))" 2>/dev/null || echo "0")

if [ "$POLL_ENABLED" = "True" ] && [ "$POLL_CHATS" -gt 0 ]; then
    pass "轮询已启用，监控${POLL_CHATS}个聊天"
elif [ "$POLL_ENABLED" = "True" ]; then
    warn "轮询已启用但无聊天ID配置"
else
    warn "轮询未启用"
fi

# ─── 7. 数据文件检查 ───
echo ""
echo "━━━ 第七步：运行时数据 ━━━"

for f in mastery.json error_book.json adjustments.json knowledge_map.json ket_plan.md grading_rules.json; do
    if [ -f "/opt/cat-learning/data/$f" ]; then
        pass "data/$f 存在"
    else
        warn "data/$f 不存在（首次运行后会自动创建）"
    fi
done

# ─── 8. root.md 系统提示词验证 ───
echo ""
echo "━━━ 第八步：系统提示词完整性 ━━━"

ROOT_MD="/opt/cat-learning/root.md"
if [ ! -f "$ROOT_MD" ]; then
    fail "root.md 不存在"
else
    info "8.1 教材知识体系"
    if grep -q "教材知识体系（出题边界铁律）" "$ROOT_MD"; then
        pass "包含教材知识体系定义"
    else
        fail "缺少教材知识体系定义"
    fi
    
    info "8.2 出题范围约束"
    if grep -q "出题铁律" "$ROOT_MD" && grep -q "不考" "$ROOT_MD"; then
        pass "出题范围约束已定义"
    else
        fail "缺少出题范围约束"
    fi
    
    info "8.3 错题本分析流程"
    if grep -q "错题本深度分析" "$ROOT_MD"; then
        pass "错题本分析流程已定义"
    else
        fail "缺少错题本分析流程"
    fi
    
    info "8.4 飞书动态调整"
    if grep -q "飞书动态调整" "$ROOT_MD"; then
        pass "飞书动态调整指令已定义"
    else
        fail "缺少飞书动态调整指令"
    fi
    
    info "8.5 错误类型分类"
    if grep -q "错误类型（4选1）" "$ROOT_MD" || grep -q "计算粗心.*概念不清.*审题偏差.*方法错误" "$ROOT_MD"; then
        pass "错误类型4分类已定义"
    else
        fail "缺少错误类型分类"
    fi
fi

# ─── 9. error_book.json 结构验证 ───
echo ""
echo "━━━ 第九步：错题本数据结构 ━━━"

info "9.1 error_book 追加策略检查"
if grep -q "追加而非覆盖" "$ROOT_MD"; then
    pass "错题本追加策略已明确"
else
    fail "错题本缺少追加策略说明"
fi

info "9.2 同类变式题生成"
if grep -q "变式题" "$ROOT_MD"; then
    pass "同类变式题生成逻辑已定义"
else
    fail "缺少同类变式题生成逻辑"
fi

if [ -f "/opt/cat-learning/data/error_book.json" ]; then
    info "9.3 已有错题记录"
    EB_COUNT=$(python3 -c "import json; d=json.load(open('/opt/cat-learning/data/error_book.json')); print(len(d) if isinstance(d,list) else 0)" 2>/dev/null || echo "0")
    pass "error_book.json 存在，包含 ${EB_COUNT} 条错题记录"
else
    warn "error_book.json 不存在（首次运行后会自动创建）"
fi

# ─── 汇总 ───
echo ""
echo "========================================="
echo "  验证结果汇总"
echo "========================================="
echo -e "  ${GREEN}通过: $PASS${NC}"
echo -e "  ${RED}失败: $FAIL${NC}"
echo ""

if [ "$FAIL" -eq 0 ]; then
    echo "  🎉 所有检查通过！"
    echo ""
    echo "  下一步端到端测试："
    echo "  1. 在飞书给机器人发消息"
    echo "  2. 等待10秒后检查: journalctl -u cat-learning --since '1 min ago' | grep POLL"
    echo "  3. 应该在飞书收到回复"
else
    echo "  ⚠️  有 $FAIL 项未通过，请根据上方提示排查"
    echo ""
    echo "  常见修复："
    echo "  - Token失败: 编辑 /opt/cat-learning/.env"
    echo "  - OCR失败: 飞书开放平台→权限管理→开通 optical_char_recognition→发布新版本"
    echo "  - 服务未运行: journalctl -u cat-learning -n 30 看错误日志"
fi
