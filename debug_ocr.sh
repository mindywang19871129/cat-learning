#!/bin/bash
# ============================================
# OCR 诊断脚本 — 打印飞书API完整返回
# 用法: bash debug_ocr.sh
# ============================================
set -e

cd /opt/cat-learning
source /opt/venv/bin/activate

echo "========================================="
echo "  飞书 OCR 深度诊断"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================="

# 1. 获取 token
echo ""
echo "━━━ ① 获取 tenant_access_token ━━━"
TOKEN=$(python3 -c "
import sys; sys.path.insert(0,'.')
from core import _get_feishu_token
token = _get_feishu_token()
if token:
    print(token)
else:
    print('FAILED')
    sys.exit(1)
")
echo "Token: ${TOKEN:0:25}..."

# 2. 创建测试图片
echo ""
echo "━━━ ② 创建测试图片 ━━━"
python3 -c "
from PIL import Image, ImageDraw, ImageFont
img = Image.new('RGB', (800, 200), 'white')
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 28)
except:
    font = ImageFont.load_default()
d.text((20, 30), 'Hello World 123', fill='black', font=font)
d.text((20, 80), '12/4=3 blocks', fill='black', font=font)
d.text((20, 130), 'Answer: 3 blocks', fill='black', font=font)
img.save('/tmp/ocr_debug.png')
import os
size = os.path.getsize('/tmp/ocr_debug.png')
print(f'图片大小: {size} bytes ({size/1024:.1f} KB)')
" && echo "✅ 测试图片已创建" || echo "❌ 图片创建失败"

# 3. 用 curl 直接调用飞书 OCR API，打印完整响应
echo ""
echo "━━━ ③ 飞书 OCR API 原始调用 ━━━"

# 准备 base64 图片
IMG_B64=$(python3 -c "
import base64
with open('/tmp/ocr_debug.png', 'rb') as f:
    print(base64.b64encode(f.read()).decode())
")
echo "Base64长度: ${#IMG_B64} 字符"

echo ""
echo "--- HTTP 请求 ---"
echo "POST https://open.feishu.cn/open-apis/optical_char_recognition/v1/image/basic_recognize"

echo ""
echo "--- HTTP 响应（完整） ---"
HTTP_CODE=$(curl -s -w "\n%{http_code}" -X POST \
  "https://open.feishu.cn/open-apis/optical_char_recognition/v1/image/basic_recognize" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "{\"image\":\"${IMG_B64}\"}" \
  -o /tmp/ocr_response.json)

RESPONSE_BODY=$(cat /tmp/ocr_response.json)

echo "HTTP状态码: ${HTTP_CODE}"
echo ""
echo "响应Body:"
echo "${RESPONSE_BODY}" | python3 -m json.tool 2>/dev/null || echo "${RESPONSE_BODY}"

# 4. 分析返回码
echo ""
echo "━━━ ④ 错误诊断 ━━━"

CODE=$(echo "${RESPONSE_BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('code','?'))" 2>/dev/null || echo "parse_error")
MSG=$(echo "${RESPONSE_BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('msg','?'))" 2>/dev/null || echo "parse_error")

echo "飞书返回 code: ${CODE}"
echo "飞书返回 msg: ${MSG}"

case "${CODE}" in
    0)
        echo ""
        echo "✅ OCR API 调用成功！"
        echo "   说明权限已生效。"
        echo "   请检查 verify_all.sh 是否使用正确的测试图片。"
        ;;
    9499|99991663|99991664|99991665|99991666|99991667|99991668|99991669)
        echo ""
        echo "❌ 权限不足 (code=${CODE})"
        echo ""
        echo "   修复步骤："
        echo "   1. 登录飞书开放平台: https://open.feishu.cn/app"
        echo "   2. 进入你的应用 → 左侧「权限管理」"
        echo "   3. 搜索并开通「optical_char_recognition」（OCR识别）权限"
        echo "   4. ⚠️ 关键步骤：点击页面顶部「发布新版本」按钮"
        echo "   5. 在弹窗中填写版本说明 → 确认发布"
        echo "   6. 等待1-2分钟生效后重试"
        ;;
    1150101)
        echo ""
        echo "❌ 参数错误 (code=1150101)"
        echo "   可能原因：图片base64格式有问题、图片太大等"
        ;;
    1150102)
        echo ""
        echo "❌ 服务异常 (code=1150102)"
        echo "   飞书OCR服务暂时不可用，稍后重试"
        ;;
    99991672)
        echo ""
        echo "❌ 应用未发布 (code=99991672)"
        echo "   需要在飞书开放平台发布应用新版本"
        ;;
    *)
        if [ "${HTTP_CODE}" != "200" ]; then
            echo ""
            echo "❌ HTTP非200: ${HTTP_CODE}"
            echo "   检查飞书API是否可访问: curl https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/check -H 'Authorization: Bearer ${TOKEN}'"
        fi
        ;;
esac

# 5. 测试 SDK 路径（core.py 的 _recognize_via_feishu）
echo ""
echo "━━━ ⑤ 通过 core.py 调用（带调试） ━━━"
python3 -c "
import sys, json, requests, base64
sys.path.insert(0, '/opt/cat-learning')
from core import _get_feishu_token, FEISHU_BASE

token = _get_feishu_token()
print(f'Token: {token[:25] if token else \"NONE\"}...')
print(f'FEISHU_BASE: {FEISHU_BASE}')

with open('/tmp/ocr_debug.png', 'rb') as f:
    img_b64 = base64.b64encode(f.read()).decode()

resp = requests.post(
    f'{FEISHU_BASE}/optical_char_recognition/v1/image/basic_recognize',
    headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json; charset=utf-8',
    },
    json={'image': img_b64},
    timeout=30,
)

print(f'HTTP Status: {resp.status_code}')
data = resp.json()
print(f'Response code: {data.get(\"code\")}')
print(f'Response msg: {data.get(\"msg\")}')
print(f'text_list: {data.get(\"data\", {}).get(\"text_list\", \"(无)\")}')

if data.get('code') == 0 and data.get('data', {}).get('text_list'):
    print('✅ _recognize_via_feishu 会返回成功')
else:
    error_codes = {
        9499: '权限不足(未开通)',
        99991663: '权限不足',
        99991664: '权限不足',
        1150101: '参数错误',
        1150102: '服务异常',
        99991672: '应用未发布',
    }
    err = error_codes.get(data.get('code'), f'未知错误')
    print(f'❌ _recognize_via_feishu 会返回 None ({err})')
"
