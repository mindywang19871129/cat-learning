# 小肥猫学习助手 v2.1 — 生产部署指南（纯云端）

---

## 📋 环境要求

| 组件 | 最低版本 | 说明 |
|------|----------|------|
| Ubuntu | 20.04 / 22.04 | 推荐 22.04 LTS |
| Python | 3.10+ | `python3 --version` |
| Git | 2.x | `git --version` |
| Nginx | 可选 | 反向代理（如需公网 HTTPS） |

---

## 🚀 快速部署（3步走）

### 第一步：拉取代码

```bash
# 克隆仓库到 /opt/cat-learning
cd /opt
git clone https://github.com/mindywang19871129/cat-learning.git
cd cat-learning
```

### 第二步：创建 .env 密钥文件

```bash
cat > .env << 'EOF'
# 火山方舟 API Key（推荐，火山方舟控制台 → API Key 管理 → 创建）
ARK_API_KEY=你的火山方舟API_Key
# 兼容旧版 DeepSeek API Key
DEEPSEEK_API_KEY=你的DeepSeek_API_Key
FEISHU_APP_ID=你的飞书App_ID
FEISHU_APP_SECRET=你的飞书App_Secret
FEISHU_VERIFICATION_TOKEN=cat_learning_2026
FEISHU_ENCRYPT_KEY=
TAVILY_API_KEY=
EOF
```

### 第三步：一键部署

```bash
bash deploy.sh
```

`deploy.sh` 会自动完成：
- 创建 Python 虚拟环境 `/opt/venv`
- 安装所有 Python 依赖
- 创建必要的数据目录
- 配置 systemd 服务实现 24/7 运行
- 启动服务

---

## 🔧 手动分步部署

如果一键部署遇到问题，可以手动执行：

```bash
# 1. 安装系统依赖
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv git

# 2. 创建虚拟环境
python3 -m venv /opt/venv
source /opt/venv/bin/activate

# 3. 安装 Python 依赖
pip install -r requirements.txt

# 4. 创建 .env（同上）

# 5. 创建数据目录
mkdir -p data/sessions data/workspace data/notes data/images

# 6. 安装 systemd 服务
sudo cp cat-learning.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cat-learning
sudo systemctl start cat-learning

# 7. 检查状态
sudo systemctl status cat-learning
```

---

## 🔄 Systemd 服务管理

```bash
# 查看服务状态
sudo systemctl status cat-learning

# 启动服务
sudo systemctl start cat-learning

# 停止服务
sudo systemctl stop cat-learning

# 重启服务
sudo systemctl restart cat-learning

# 查看日志（实时）
sudo journalctl -u cat-learning -f

# 查看应用日志
tail -f /var/log/cat-learning.log

# 设置开机自启
sudo systemctl enable cat-learning

# 取消开机自启
sudo systemctl disable cat-learning
```

服务配置特点：
- **自动重启**：崩溃后 5 秒自动恢复
- **开机自启**：服务器重启后自动启动
- **日志记录**：访问日志和错误日志写入 `/var/log/cat-learning.log`

---

## 🧪 端到端测试

项目内置了测试脚本：

```bash
# 基本测试
bash test.sh

# 指定服务器地址
bash test.sh 10.100.13.215 8192
```

测试项包括：
1. 健康检查 `/health` → 期望 `{"status":"ok"}`
2. 首页 `/` → 期望包含"小肥猫学习助手"
3. 飞书回调 `/feishu/event` → 期望可达
4. 管理员初始化 `/admin/init` → 期望正常响应

---

## 📡 飞书集成（内网无公网IP - 轮询模式）

> ⚠️ 如果没有公网 IP，飞书回调无法到达内网服务器。本项目支持**消息轮询模式**，服务器主动拉取聊天消息，无需公网IP。

### 配置轮询

部署完成后，告诉服务器要监控哪个聊天：

```bash
# 替换成你的聊天ID（去飞书给机器人发条消息后获取）
curl -X POST http://localhost:8192/feishu/config \
  -H "Content-Type: application/json" \
  -d '{"chat_ids":["你的聊天ID"]}'

# 重启生效
systemctl restart cat-learning
```

### 获取聊天ID

在服务器上执行，从已发送的消息中获取：
```bash
TOKEN=$(curl -s -X POST 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"cli_aa84657cf6fa1bc1","app_secret":"UF0E3oW1OiTUXTEJWOrTAcDZTlwPhnar"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['tenant_access_token'])")

curl -s "https://open.feishu.cn/open-apis/im/v1/chats?page_size=20" \
  -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys,json
data = json.load(sys.stdin)
for item in data.get('data',{}).get('items',[]):
    print(f\"{item['name']:20s}  chat_id={item['chat_id']}\")
"
```

### 详细验证

参考 [VERIFY.md](./VERIFY.md) 查看完整的端到端验证步骤。

---

## 📡 飞书开放平台配置（仅公网IP需要）
> - 使用内网穿透工具（frp / ngrok）
> - 配置 Nginx 反向代理 + 公网入口

---

## 🔑 初始化管理密码

```bash
curl -X POST http://你的服务器IP:8192/admin/init \
  -H "Content-Type: application/json" \
  -d '{"password": "你想要的密码"}'
```

---

## 📱 飞书使用方式

部署成功后，在飞书里搜索你的机器人应用，发送消息即可：

- **答题**：直接发「第1题答案是...」
- **拍照批改**：拍照发图，自动 OCR 识别+批改
- **家长调参**：发「调整难度 easy」，密码验证后调整
- **查看报告**：发「查看学习报告」，输入密码即可查看

---

## 🔄 代码更新

当 GitHub 上有新版本时，在服务器执行：

```bash
cd /opt/cat-learning
git pull
sudo systemctl restart cat-learning
```

---

## 🛠️ 故障排查

```bash
# 1. 检查服务是否运行
sudo systemctl status cat-learning

# 2. 手动启动看报错
source /opt/venv/bin/activate
cd /opt/cat-learning
gunicorn server:app --bind 0.0.0.0:8192 --workers 2

# 3. 检查端口占用
sudo lsof -i :8192

# 4. 查看系统日志
sudo journalctl -u cat-learning -n 50 --no-pager

# 5. 查看应用日志
cat /var/log/cat-learning.log

# 6. 测试端口连通性
curl http://localhost:8192/health
```
