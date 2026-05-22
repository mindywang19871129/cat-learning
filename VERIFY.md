# 小肥猫学习助手 - 端到端验证指南

---

## 背景

服务器在**内网（无公网IP）**，飞书无法主动推送事件回调。采用**轮询模式**：服务器定时拉取聊天消息 → 处理 → 回复。

---

## 第一步：启动服务

```bash
systemctl restart cat-learning
sleep 2
curl http://localhost:8192/health
```

期望返回 `{"status":"ok","poll_enabled":false,...}`

---

## 第二步：配置轮询的聊天ID

聊天ID就是你和机器人的私聊。拿你之前发消息那个聊天 `oc_a232ed8b096918c5ff89fa9149e321fa`：

```bash
curl -X POST http://localhost:8192/feishu/config \
  -H "Content-Type: application/json" \
  -d '{"chat_ids":["oc_a232ed8b096918c5ff89fa9149e321fa"]}'
```

---

## 第三步：重启服务，激活轮询

```bash
systemctl restart cat-learning
sleep 3
curl http://localhost:8192/health
```

这次应显示 `"poll_enabled":true,"poll_chats":1`

---

## 第四步：端到端测试 - 消息问答

在飞书里给机器人发一条消息，然后等10秒，去服务器看日志：

```bash
journalctl -u cat-learning -f
```

如果看到类似 `[POLL] 新消息:`，说明轮询成功。

---

## 第五步：端到端测试 - 出题批改

在飞书里发：
```
第1题答案是：42
```

等10秒看日志和飞书回复。

---

## 第六步：端到端测试 - 家长管理

先初始化密码（如果还没设）：
```bash
curl -X POST http://localhost:8192/admin/init \
  -H "Content-Type: application/json" \
  -d '{"password":"mima1234"}'
```

然后在飞书里发：
```
查看学习报告
```

---

## 问题排查

### 轮询没生效
```bash
# 手动触发一次轮询看结果
curl -X POST http://localhost:8192/feishu/poll

# 查看日志
journalctl -u cat-learning --no-pager -n 30
```

### 消息没回复
```bash
# 看LLM调用是否正常
tail -50 /var/log/cat-learning.log
```

### 飞书API不通
```bash
curl -s -X POST 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' \
  -H 'Content-Type: application/json' \
  -d '{"app_id":"cli_aa84657cf6fa1bc1","app_secret":"UF0E3oW1OiTUXTEJWOrTAcDZTlwPhnar"}' | python3 -m json.tool
```

---

## API 快速参考

| 接口 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查（含轮询状态） |
| `/feishu/event` | POST | 飞书事件回调（需公网IP） |
| `/feishu/poll` | POST | 手动触发一次轮询 |
| `/feishu/config` | GET/POST | 查看/设置轮询聊天ID |
| `/admin/init` | POST | 初始化管理密码 |
| `/admin/adjust` | POST | 家长调参 |
| `/admin/report` | GET | 查看学习报告 |
