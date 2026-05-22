"""
小肥猫学习助手 — 服务器
======================================================================
Flask Web 服务器。极简设计：
  - 飞书事件接收 → 交给 LLM Agent Loop
  - 定时调度（每日推送）→ 交给 LLM Agent Loop
  - 家长管理接口 → 密码验证 + 交给 LLM Agent Loop
"""
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

HOME = Path(os.environ.get("CATLEARN_HOME", Path(__file__).parent.resolve()))
sys.path.insert(0, str(HOME))

from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from core import (
    init_new_session, run, download_feishu_image,
    verify_password, DATA_DIR, CFG, HOME as CORE_HOME,
)

app = Flask(__name__)

FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")


# ══════════════════════════════════════════════════════════════════════
# 飞书事件处理
# ══════════════════════════════════════════════════════════════════════

def _handle_feishu_event(event: dict):
    """处理单个飞书事件，全部交给 LLM。"""
    event_type = event.get("type", "")
    if event_type != "im.message.receive_v1":
        return

    sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    message_id = event.get("message", {}).get("message_id", "")
    msg = event.get("message", {})
    msg_type = msg.get("message_type", "text")
    content_str = msg.get("content", "{}")

    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {}

    text = content.get("text", "").strip()
    if not text and msg_type != "image":
        return

    print(f"[INFO] 飞书消息: sender={sender_id[:12]}... type={msg_type} text={text[:80]}")

    session = init_new_session()

    if msg_type == "image":
        image_key = content.get("image_key", "")
        if not image_key:
            return
        local_path = download_feishu_image(message_id, image_key)
        if not local_path:
            run(
                f"[系统上下文：飞书用户 sender_open_id={sender_id}]\n"
                f"用户发来一张图片但下载失败，请告知用户。用 send_feishu 回复。",
                session,
            )
            return

        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}]\n"
            f"学生发来一张图片，已保存到 {local_path}。\n"
            f"请用 ocr_image 识别图片内容，然后判断：\n"
            f"1. 如果是答题 → 批改并发送结果\n"
            f"2. 如果不是答题 → 友好回复\n"
            f"处理完用 send_feishu 发送结果给 {sender_id}。",
            session,
        )
        print(f"[INFO] 图片处理完成: {result[:100] if result else 'None'}")
    else:
        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}]\n"
            f"学生发来消息：{text}\n\n"
            f"请根据消息内容自主判断和处理：\n"
            f"- 如果是答题（如'第1题答案是...'）→ 批改\n"
            f"- 如果是家长调参指令（如'调整难度'、'查看学习报告'）→ 先验证密码再执行\n"
            f"- 如果是普通对话 → 友好回复\n"
            f"处理完用 send_feishu 发送结果给 {sender_id}。",
            session,
        )
        print(f"[INFO] 文本处理完成: {result[:100] if result else 'None'}")


# ══════════════════════════════════════════════════════════════════════
# 定时任务
# ══════════════════════════════════════════════════════════════════════

def scheduled_daily_push():
    """定时每日推送学习任务。"""
    print(f"[SCHEDULER] 执行每日推送: {datetime.now()}")
    try:
        session = init_new_session()
        result = run(
            "请生成今天的学习计划：\n"
            "1. 读取 data/mastery.json 了解掌握度\n"
            "2. 读取 data/error_book.json 检查需要复习的错题\n"
            "3. 读取 data/adjustments.json 查看家长的调参设置\n"
            "4. 读取 data/knowledge_map.json 了解知识体系\n"
            "5. 用 call_llm 生成今天的数学和英语练习题\n"
            "6. 把题目存入 data/today_questions.json\n"
            "7. 如果配置了推送，用 send_feishu 发卡片消息\n"
            "注意：发送 feishu 时 receive_id 参数待定，请先完成题目生成。",
            session,
        )
        print(f"[SCHEDULER] 每日推送完成: {result[:200] if result else 'None'}")
    except Exception as e:
        print(f"[SCHEDULER] 每日推送失败: {e}")


def start_scheduler():
    push_time = CFG.get("education", {}).get("push_time", "09:00")
    hour, minute = map(int, push_time.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_daily_push, 'cron', hour=hour, minute=minute, id='daily_push')
    scheduler.start()
    print(f"[SCHEDULER] 已启动，每日 {push_time} 推送")


# ══════════════════════════════════════════════════════════════════════
# Flask 路由
# ══════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    return jsonify({"app": "小肥猫学习助手", "version": "2.0", "status": "running", "time": datetime.now().isoformat()})


@app.route("/feishu/event", methods=["POST"])
def feishu_event():
    """飞书事件订阅回调。"""
    body = request.get_json(force=True, silent=True) or {}

    # URL 验证
    if body.get("type") == "url_verification":
        token = body.get("token", "")
        challenge = body.get("challenge", "")
        if token == FEISHU_VERIFY_TOKEN:
            return jsonify({"challenge": challenge})
        return jsonify({"error": "invalid token"}), 403

    # 事件处理
    try:
        event = body.get("event", {})
        if event:
            _handle_feishu_event(event)
        return jsonify({"code": 0})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"code": -1, "msg": str(e)}), 500


@app.route("/admin/init", methods=["POST"])
def admin_init():
    """初始化系统（首次设置密码）。"""
    body = request.get_json(force=True, silent=True) or {}
    new_password = body.get("password", "")
    if not new_password or len(new_password) < 4:
        return jsonify({"code": -1, "msg": "密码至少4位"}), 400

    import hashlib
    adjustments = {
        "admin_password": hashlib.sha256(new_password.encode()).hexdigest(),
        "settings": {
            "math_daily_count": CFG.get("education", {}).get("math_daily_count", 4),
            "english_daily_count": CFG.get("education", {}).get("english_daily_count", 4),
            "difficulty_bias": "normal",
            "focus_topics": [],
            "excluded_topics": [],
        },
        "schedule": {
            "push_time": CFG.get("education", {}).get("push_time", "09:00"),
            "friday_3days": True,
        },
    }
    adj_file = DATA_DIR / "adjustments.json"
    adj_file.write_text(json.dumps(adjustments, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"code": 0, "msg": "初始化成功，密码已设置"})


@app.route("/admin/adjust", methods=["POST"])
def admin_adjust():
    """家长调参接口（HTTP备用）。"""
    body = request.get_json(force=True, silent=True) or {}
    password = body.get("password", "")
    if not verify_password(password):
        return jsonify({"code": -1, "msg": "密码错误"}), 403

    session = init_new_session()
    action = body.get("action", "")
    params = body.get("params", {})
    cmd = f"调整 {action}"
    for k, v in params.items():
        cmd += f" {k}={v}"

    result = run(
        f"[系统上下文：家长调参，已通过密码验证]\n"
        f"指令：{cmd}\n"
        f"请读取 data/adjustments.json 并执行相应修改，然后确认。",
        session,
    )
    return jsonify({"code": 0, "msg": "已处理", "result": result})


@app.route("/admin/report", methods=["GET"])
def admin_report():
    """查看学习报告。"""
    password = request.args.get("password", "")
    if not verify_password(password):
        return jsonify({"code": -1, "msg": "密码错误"}), 403

    session = init_new_session()
    result = run(
        "请生成一份学习报告：\n"
        "1. 读取 data/mastery.json 获取掌握度数据\n"
        "2. 读取 data/error_book.json 获取错题统计\n"
        "3. 汇总分析：已掌握/学习中/薄弱的知识点\n"
        "4. 给出学习建议\n"
        "将报告以清晰的 JSON 格式输出。",
        session,
    )
    try:
        return jsonify(json.loads(result or "{}"))
    except json.JSONDecodeError:
        return jsonify({"raw": result})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})


# ══════════════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════════════

def main():
    port = CFG.get("runtime", {}).get("server_port", 8192)
    start_scheduler()
    print(f"\n🐱 小肥猫学习助手 v2.0 启动")
    print(f"   端口: {port}")
    print(f"   飞书事件: http://0.0.0.0:{port}/feishu/event")
    print(f"   管理接口: http://0.0.0.0:{port}/admin/init\n")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
