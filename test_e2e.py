#!/usr/bin/env python3
"""
小肥猫学习助手 — 端到端诊断脚本
逐环节测试：DeepSeek → 飞书Token → 飞书发消息 → 轮询模拟 → LLM全流程
"""
import sys, os, json, time, traceback
from pathlib import Path

HOME = Path(os.environ.get("CATLEARN_HOME", Path(__file__).parent.resolve()))
sys.path.insert(0, str(HOME))

# 加载 .env
env_file = HOME / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

from core import (
    call_llm, send_feishu, init_new_session, run,
    DATA_DIR, CFG, MODEL, BASE_URL, API_KEY,
)

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
NC = "\033[0m"

def ok(msg):    print(f"  {GREEN}✅{NC} {msg}")
def fail(msg):  print(f"  {RED}❌{NC} {msg}")
def warn(msg):  print(f"  {YELLOW}⚠️{NC} {msg}")
def info(msg):  print(f"  {CYAN}📋{NC} {msg}")
def title(msg): print(f"\n{'='*60}\n  {msg}\n{'='*60}")

errors = []

# ═══════════════════════════════════════════════════════════════
title("1. 配置检查")

info(f"MODEL: {MODEL}")
info(f"BASE_URL: {BASE_URL}")
info(f"API_KEY: {'已配置' if API_KEY else '未配置'}")

feishu_app_id = os.environ.get("FEISHU_APP_ID", "")
feishu_app_secret = os.environ.get("FEISHU_APP_SECRET", "")
info(f"FEISHU_APP_ID: {'已配置' if feishu_app_id else '未配置'}")
info(f"FEISHU_APP_SECRET: {'已配置' if feishu_app_secret else '未配置'}")

poll_cfg = CFG.get("feishu", {}).get("poll", {})
chat_ids = poll_cfg.get("chat_ids", [])
info(f"轮询聊天ID: {chat_ids}")
info(f"轮询启用: {poll_cfg.get('enabled')}")

# ═══════════════════════════════════════════════════════════════
title("2. DeepSeek API 连通性")

try:
    r = call_llm("请只回复'OK'这两个字母，不要其他任何内容。")
    if "OK" in r:
        ok(f"DeepSeek API 正常 (回复: {r.strip()})")
    else:
        warn(f"DeepSeek 回复异常: {r[:100]}")
except Exception as e:
    fail(f"DeepSeek API 调用失败: {e}")
    errors.append(f"DeepSeek: {e}")

# ═══════════════════════════════════════════════════════════════
title("3. 飞书 Token 获取")

import requests as req
try:
    resp = req.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": feishu_app_id, "app_secret": feishu_app_secret},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") == 0:
        ok(f"飞书 Token 获取成功 (expire: {data.get('expire')}s)")
        feishu_token = data["tenant_access_token"]
    else:
        fail(f"飞书 Token 获取失败: code={data.get('code')} msg={data.get('msg')}")
        errors.append(f"飞书Token: {data}")
        feishu_token = None
except Exception as e:
    fail(f"飞书 Token 请求异常: {e}")
    errors.append(f"飞书Token请求: {e}")
    feishu_token = None

# ═══════════════════════════════════════════════════════════════
title("4. 飞书消息发送测试")

if feishu_token and chat_ids:
    test_chat = chat_ids[0]
    try:
        resp = req.post(
            f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers={
                "Authorization": f"Bearer {feishu_token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": test_chat,
                "msg_type": "text",
                "content": json.dumps({"text": "🐱 小肥猫诊断测试：如果你看到这条消息，说明飞书消息发送通道正常！"}),
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            ok(f"飞书消息发送成功 → chat_id={test_chat[:16]}...")
        else:
            fail(f"飞书消息发送失败: code={data.get('code')} msg={data.get('msg')}")
            errors.append(f"飞书发送: {data}")
    except Exception as e:
        fail(f"飞书消息发送异常: {e}")
        errors.append(f"飞书发送异常: {e}")
else:
    warn("跳过（无 Token 或无 chat_ids）")

# ═══════════════════════════════════════════════════════════════
title("5. today_questions.json 状态")

today_file = DATA_DIR / "today_questions.json"
if today_file.exists():
    try:
        data = json.loads(today_file.read_text(encoding="utf-8"))
        info(f"日期: {data.get('date')}")
        info(f"数学: {len(data.get('math', []))} 题")
        info(f"英语: {len(data.get('english', []))} 题")
        all_q = data.get("math", []) + data.get("english", [])
        completed = sum(1 for q in all_q if "score" in q or "batch_id" in q)
        info(f"已批改: {completed}/{len(all_q)}")
        if not data.get("date"):
            warn("日期字段为 None/空，可能导致推送阻塞")
    except Exception as e:
        fail(f"读取失败: {e}")
else:
    info("文件不存在（正常，今天还没出题）")

# ═══════════════════════════════════════════════════════════════
title("6. 模拟轮询 → 处理消息（完整流程）")

if not chat_ids:
    warn("跳过（无 chat_ids）")
else:
    test_chat = chat_ids[0]
    
    # 先获取最新一条用户消息
    try:
        resp = req.get(
            f"https://open.feishu.cn/open-apis/im/v1/messages"
            f"?container_id_type=chat&container_id={test_chat}&page_size=3&sort_type=ByCreateTimeDesc",
            headers={"Authorization": f"Bearer {feishu_token}"},
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            fail(f"获取消息列表失败: {data}")
        else:
            items = data.get("data", {}).get("items", [])
            info(f"最近 {len(items)} 条消息:")
            for msg in items:
                sender_type = msg.get("sender", {}).get("sender_type", "")
                msg_type = msg.get("msg_type", "text")
                body = msg.get("body", {}).get("content", "{}")
                try:
                    body_json = json.loads(body)
                    text = body_json.get("text", "")[:60]
                except:
                    text = body[:60]
                tag = "🤖机器人" if sender_type == "app" else "👤用户"
                info(f"  {tag} [{msg_type}] {text}")
    except Exception as e:
        fail(f"获取消息列表异常: {e}")

# ═══════════════════════════════════════════════════════════════
title("7. 清理旧题 + 手动触发推送")

# 先清理旧题
try:
    resp = req.post("http://localhost:8192/clear_today", timeout=5)
    info(f"清理旧题: {resp.json()}")
except Exception as e:
    warn(f"清理失败（可能服务未运行）: {e}")

# 手动触发推送
try:
    resp = req.post("http://localhost:8192/push", timeout=5)
    info(f"触发推送: {resp.json()}")
    info("等待15秒让 LLM 完成出题+推送...")
    time.sleep(15)
except Exception as e:
    warn(f"触发推送失败: {e}")

# ═══════════════════════════════════════════════════════════════
title("8. 检查飞书是否收到消息")

if feishu_token and chat_ids:
    try:
        resp = req.get(
            f"https://open.feishu.cn/open-apis/im/v1/messages"
            f"?container_id_type=chat&container_id={chat_ids[0]}&page_size=5&sort_type=ByCreateTimeDesc",
            headers={"Authorization": f"Bearer {feishu_token}"},
            timeout=10,
        )
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        info(f"最新 {len(items)} 条消息:")
        found_card = False
        for msg in items:
            msg_type = msg.get("msg_type", "")
            sender_type = msg.get("sender", {}).get("sender_type", "")
            body = msg.get("body", {}).get("content", "{}")
            try:
                body_json = json.loads(body)
                text = body_json.get("text", "")[:80] if msg_type == "text" else json.dumps(body_json, ensure_ascii=False)[:80]
            except:
                text = body[:80]
            tag = "🤖机器人" if sender_type == "app" else "👤用户"
            if "小肥猫" in text or "学习任务" in text:
                found_card = True
                ok(f"  {tag} [{msg_type}] {text}")
            else:
                info(f"  {tag} [{msg_type}] {text}")
        if found_card:
            ok("飞书已收到小肥猫推送消息！")
        else:
            warn("未找到小肥猫推送消息，检查 send_feishu 是否成功调用")
    except Exception as e:
        fail(f"检查消息失败: {e}")

# ═══════════════════════════════════════════════════════════════
title("诊断总结")

if errors:
    print(f"\n  {RED}发现 {len(errors)} 个问题:{NC}")
    for e in errors:
        print(f"    - {e}")
else:
    print(f"\n  {GREEN}所有检查通过！{NC}")

print(f"\n  如果飞书没收到消息，请检查：")
print(f"  1. 飞书开放平台 → 权限管理 → 确认已开通 im:message 和 im:message:send_as_bot")
print(f"  2. 飞书开放平台 → 安全设置 → 确认机器人已添加到群聊")
print(f"  3. 群聊设置 → 群机器人 → 确认小肥猫机器人在列表中")
print()
