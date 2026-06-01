#!/bin/bash
# 部署更新脚本 - 修复图片识别和上下文记忆问题

set -e

echo "=== 部署小肥猫学习助手更新 ==="
echo "修复内容:"
echo "1. 图片识别错误太多 - 增加LLM视觉能力"
echo "2. 记不住前面的问题 - 添加会话缓存"
echo ""

# 检查当前目录
if [ ! -f "core.py" ]; then
    echo "❌ 错误: 请在项目根目录运行此脚本"
    exit 1
fi

# 备份原始文件
echo "📁 备份原始文件..."
timestamp=$(date +%Y%m%d_%H%M%S)
backup_dir="backup_${timestamp}"
mkdir -p "$backup_dir"

cp core.py "$backup_dir/core.py.backup"
cp server.py "$backup_dir/server.py.backup"
echo "✅ 备份完成: $backup_dir"

# 检查环境变量
echo "🔧 检查环境变量..."
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "⚠️  警告: DEEPSEEK_API_KEY 未设置"
    echo "   请设置环境变量: export DEEPSEEK_API_KEY=your_key"
fi

if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ]; then
    echo "⚠️  警告: 飞书API凭证未设置"
    echo "   传统OCR可能无法使用，但LLM视觉仍可工作"
fi

# 检查Python依赖
echo "📦 检查Python依赖..."
python3 -c "import openai, flask, apscheduler" 2>/dev/null || {
    echo "❌ 缺少Python依赖"
    echo "   请安装: pip install openai flask apscheduler requests PyPDF2"
    exit 1
}

# 验证代码语法
echo "🔍 验证代码语法..."
python3 -m py_compile core.py server.py && echo "✅ 语法检查通过" || {
    echo "❌ 语法检查失败"
    exit 1
}

# 创建必要的目录
echo "📁 创建必要目录..."
mkdir -p data/sessions data/images data/files
mkdir -p test_images

# 更新配置文件（如果需要）
if [ ! -f "config.toml" ]; then
    echo "⚠️  警告: config.toml 不存在，使用默认配置"
    cat > config.toml << 'EOF'
[api]
model = "deepseek-v4-pro"
base_url = "https://api.deepseek.com/v1"
max_tokens = 4096
temperature = 0.7

[paths]
workspace = "workspace"
sessions = "data/sessions"
notes = "data/notes"
methods = "methods"
data = "data"
tools = "tools"

[runtime]
max_iterations = 30
max_tool_output = 8000
server_port = 8192

[education]
math_daily_count = 4
english_daily_count = 4
exam_questions_count = 25
push_time = "09:00"
friday_3days = false
course_start_date = "2026-05-14"
difficulty_bias = "hard"
ebbinghaus_intervals = [1, 2, 4, 7, 15]

[education.mastery]
mastered = 95
learning = 70
weak = 50
critical = 0

[education.mastery_score]
perfect = 5
partial = 1
wrong = -10

[feishu]
base_url = "https://open.feishu.cn/open-apis"
card_color = "orange"

[feishu.poll]
enabled = true
chat_ids = ["oc_a232ed8b096918c5ff89fa9149e321fa"]
interval_seconds = 10

[ocr]
# OCR引擎（纯云端，代码自动选择：飞书OCR → ocr.space 降级）
# 飞书OCR为主力引擎，需在飞书开放平台开通 optical_char_recognition 权限
# ocr.space 为备用（免费25000次/月），需在 https://ocr.space/ocrapi 注册获取API Key
# API Key 配置在 .env 文件的 OCR_SPACE_API_KEY 字段

[exam]
math_timeout_minutes = 60
ket_reading_writing_minutes = 60
ket_listening_minutes = 30
EOF
    echo "✅ 创建默认 config.toml"
fi

# 检查.env文件
if [ ! -f ".env" ]; then
    echo "⚠️  警告: .env 文件不存在"
    echo "   创建示例 .env 文件..."
    cat > .env.example << 'EOF'
# DeepSeek API密钥
DEEPSEEK_API_KEY=sk-your-deepseek-api-key-here

# 飞书企业自建应用凭证
FEISHU_APP_ID=cli_your_app_id_here
FEISHU_APP_SECRET=your_app_secret_here
FEISHU_VERIFICATION_TOKEN=your_verification_token_here
FEISHU_ENCRYPT_KEY=

# 可选：Tavily搜索API（DeepSeek联网搜索不生效时的降级方案）
TAVILY_API_KEY=

# OCR.space免费API密钥（https://ocr.space/ocrapi 免费注册，每月25000次）
OCR_SPACE_API_KEY=helloworld
EOF
    echo "✅ 创建 .env.example，请复制为 .env 并填写实际值"
fi

# 启动测试
echo "🚀 启动测试服务器..."
echo ""
echo "=== 更新摘要 ==="
echo "✅ 核心更新:"
echo "   - 增强的图片识别 (LLM视觉 + 传统OCR)"
echo "   - 会话缓存系统 (保持对话历史)"
echo "   - 上下文记忆优化"
echo ""
echo "📋 下一步:"
echo "1. 编辑 .env 文件，填写API密钥"
echo "2. 运行测试: python test_ocr.py"
echo "3. 启动服务器: python server.py"
echo "4. 访问: http://localhost:8192"
echo ""
echo "🐱 小肥猫学习助手更新完成!"