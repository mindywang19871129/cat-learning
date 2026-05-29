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
import time
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("CATLEARN_HOME", Path(__file__).parent.resolve()))
sys.path.insert(0, str(HOME))

from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler

from core import (
    init_new_session, run, download_feishu_image,
    download_feishu_file, extract_text_from_pdf,
    ocr_image, send_feishu,
    verify_password, DATA_DIR, CFG, HOME as CORE_HOME,
)

app = Flask(__name__)

FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")


# ══════════════════════════════════════════════════════════════════════
# 飞书事件处理
# ══════════════════════════════════════════════════════════════════════

# ── 发题/新题 触发关键词 ──
_FATI_KEYWORDS = ["发题", "新题", "做题", "来一套", "再来一套", "发一套", "再发", "做新题", "出题", "推送题目", "今日题目", "每日一练"]


def _check_today_questions_completed():
    """Python层快速检查 today_questions.json 完成状态（不依赖LLM）。
    用于手动发题请求：任何未完成题目都拦截，不区分日期。
    返回 (can_generate: bool, reason: str)
    """
    today_file = DATA_DIR / "today_questions.json"
    if not today_file.exists():
        return True, "今天还没有题目"

    try:
        data = json.loads(today_file.read_text(encoding="utf-8"))
    except Exception:
        return True, "文件读取失败，按无题目处理"

    math = data.get("math", [])
    english = data.get("english", [])
    all_q = math + english
    total = len(all_q)

    if total == 0:
        return True, "没有题目记录"

    # 已批改 = 有 score 或 batch_id 字段
    completed = sum(1 for q in all_q if "score" in q or "batch_id" in q)
    if completed < total:
        return False, f"还有 {total - completed}/{total} 道题未完成"

    return True, f"全部 {total} 道题已完成"


def _can_scheduled_push_today():
    """定时推送专用检查：防止旧题永久阻塞每日推送。
    规则：
    - 文件不存在 → 允许推送
    - 文件日期 = 今天 → 今天已推送，不重复
    - 文件日期 = 昨天且未完成 → 前一天未批改，不出后一天
    - 文件日期更早 → 旧题作废，允许推送
    返回 (can_push: bool, reason: str)
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_file = DATA_DIR / "today_questions.json"

    if not today_file.exists():
        return True, "今天还没有题目"

    try:
        data = json.loads(today_file.read_text(encoding="utf-8"))
    except Exception:
        return True, "文件读取失败，按无题目处理"

    file_date = data.get("date", "")
    math = data.get("math", [])
    english = data.get("english", [])
    all_q = math + english
    total = len(all_q)

    # 日期为空/None → 旧题作废
    if not file_date:
        return True, "题目文件日期缺失，按旧题作废处理"

    # 文件日期是今天 → 已推送过
    if file_date == today_str:
        if total == 0:
            return True, "今天题目为空，允许重新推送"
        completed = sum(1 for q in all_q if "score" in q or "batch_id" in q)
        if completed >= total:
            return False, "今天题目已全部完成，无需重复推送"
        return False, f"今天还有 {total - completed}/{total} 道题未完成，不重复推送"

    # 文件日期是昨天 → 检查是否完成
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if file_date == yesterday:
        if total == 0:
            return True, "昨天没有题目记录"
        completed = sum(1 for q in all_q if "score" in q or "batch_id" in q)
        if completed < total:
            return False, f"昨天还有 {total - completed}/{total} 道题未完成，今天不出新题"
        return True, "昨天题目已完成，推送今日新题"

    # 文件日期是更早的 → 旧题作废，允许推送
    return True, f"旧题目（{file_date}）已过期，推送新题"


def _is_request_new_questions(text: str) -> bool:
    """判断消息是否是'发一套新题'意图（非调参）。"""
    text_lower = text.lower().replace(" ", "")
    return any(kw in text_lower for kw in _FATI_KEYWORDS)


def _is_parent_adjust(text: str) -> bool:
    """判断消息是否是家长调参意图。"""
    kw = ["调整", "增加", "减少", "更换教材", "调整年级", "查看报告", "学习报告", "密码"]
    return any(k in text for k in kw)

def _handle_feishu_event(event: dict):
    """处理单个飞书事件，全部交给 LLM。"""
    event_type = event.get("type", "")
    if event_type != "im.message.receive_v1":
        return

    sender_id = event.get("sender", {}).get("sender_id", {}).get("open_id", "")
    message_id = event.get("message", {}).get("message_id", "")
    chat_id = event.get("message", {}).get("chat_id", "")
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

    # 回复目标：群聊消息回群聊，私聊消息回私聊
    reply_target = chat_id if chat_id else sender_id
    target_desc = f"回复目标ID={reply_target}（群聊→发到群，私聊→发到用户）"

    print(f"[INFO] 飞书消息: sender={sender_id[:12]}... type={msg_type} text={text[:80]} reply_to={reply_target[:16]}...")

    session = init_new_session()

    if msg_type == "image":
        image_key = content.get("image_key", "")
        if not image_key:
            return
        local_path = download_feishu_image(message_id, image_key)
        if not local_path:
            run(
                f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]\n"
                f"用户发来一张图片但下载失败，请告知用户。用 send_feishu(receive_id=\"{reply_target}\", ...) 回复。",
                session,
            )
            return

        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]\n"
            f"学生发来一张图片，已保存到 {local_path}。\n\n"
            f"请严格按照 root.md「图片处理」流程执行：\n"
            f"1. 用 ocr_image 识别（一次即可，返回 confidence_hint）\n"
            f"2. 不管 confidence_hint 是多少，都必须用 call_llm 做【第1轮清理】→【第2轮结构化】→【第3轮交叉验证】\n"
            f"3. 三轮增强后如有[疑似]项>30%，用 send_feishu 请学生确认\n"
            f"4. 确认后按批改流程（读today_questions.json→call_llm批改→更新mastery/error_book→send_feishu发送结果）\n\n"
            f"⚠️ 关键约束：\n"
            f"- 绝对不要用 bash 执行本地OCR脚本\n"
            f"- ocr_image 只调用1次\n"
            f"- 必须走完3轮LLM增强再决定是否需要确认\n"
            f"- receive_id 用 \"{reply_target}\"",
            session,
        )
        print(f"[INFO] 图片处理完成: {result[:100] if result else 'None'}")
    elif msg_type == "file":
        # ── 文件上传处理（教材PDF/文档等）──
        file_key = content.get("file_key", "")
        file_name = content.get("file_name", "unknown")
        if not file_key:
            return

        print(f"[INFO] 飞书文件: sender={sender_id[:12]}... file={file_name}")

        local_path = download_feishu_file(message_id, file_key, file_name)
        if not local_path:
            run(
                f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]\n"
                f"用户上传了文件 '{file_name}' 但下载失败，请告知用户。用 send_feishu(receive_id=\"{reply_target}\", ...) 回复。",
                init_new_session(),
            )
            return

        # 根据文件类型提取内容
        ext = file_name.lower().rsplit(".", 1)[-1] if "." in file_name else ""
        if ext in ("pdf",):
            file_content = extract_text_from_pdf(local_path)
            content_type_desc = "PDF文档"
        elif ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
            # 图片文件 → 用OCR
            ocr_result = json.loads(ocr_image(local_path))
            file_content = ocr_result.get("text", "") if ocr_result.get("success") else "(OCR失败)"
            content_type_desc = "图片"
        else:
            file_content = f"(不支持的文件类型: .{ext}，请上传PDF或图片)"
            content_type_desc = "未知类型"

        # 截断过长内容
        if len(file_content) > 6000:
            file_content = file_content[:6000] + "\n\n[... 内容过长，已截断为前6000字符 ...]"

        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]\n"
            f"用户上传了一个{content_type_desc}文件：'{file_name}'。\n\n"
            f"═══════════════════════════════════════\n"
            f"【文件内容】（已提取）\n"
            f"═══════════════════════════════════════\n"
            f"{file_content}\n\n"
            f"═══════════════════════════════════════\n"
            f"请判断这个文件的用途：\n"
            f"- 如果是教材/课本/教学大纲（文件名或内容含教材、课本、目录、知识点等）→ 提取知识体系，更新 data/knowledge_map.json，然后告知用户已完成\n"
            f"- 如果是作业/题目/试卷 → 按批改流程处理\n"
            f"- 如果是其他内容 → 友好回复说明\n"
            f"处理完用 send_feishu(receive_id=\"{reply_target}\", ...) 发送结果。",
            init_new_session(),
        )
        print(f"[INFO] 文件处理完成: {result[:100] if result else 'None'}")
    else:
        # ── Python层快速预检：发题/新题意图 ──
        # 不依赖LLM做拦截，保证一定有飞书回复
        if _is_request_new_questions(text) and not _is_parent_adjust(text):
            can_gen, reason = _check_today_questions_completed()
            _log(f"[PRE-CHECK] 发题意图检测: can_gen={can_gen}, reason={reason}")
            if not can_gen:
                # 题目未完成 → 直接发飞书回复，不走LLM（快速、可靠）
                reply = f"🐱 {reason}哦～先把今天的题目完成，我会等你！回复时写「第X题答案是...」就可以啦～"
                send_feishu(receive_id=reply_target, msg_type="text", content=reply)
                _log(f"[INFO] 发题拦截: {reason} → 已直接回复飞书")
                return
            _log(f"[PRE-CHECK] 题目已完成/无题目，交给LLM生成新题")

        session = init_new_session()
        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]\n"
            f"学生发来消息：{text}\n\n"
            f"═══════════════════════════════════════\n"
            f"请根据消息内容判断处理类型并执行：\n"
            f"═══════════════════════════════════════\n\n"
            f"类型A·家长调参（最高优先级）→ 消息含调参关键词+密码，读adjustments.json，用 send_feishu 确认\n"
            f"类型B·发新题 → 消息含 发题/新题/做题/再来一套 等 → 读adjustments/mastery/error_book/knowledge_map → call_llm出题 → write_file存today_questions.json → send_feishu推送卡片\n"
            f"  出题标准：数学只出提升+拓展，复合应用60%+图形30%+拓展10%；KET写作35%+词汇25%+语法20%\n"
            f"类型C·答题批改 → 消息含第X题/答案是 → 读today_questions.json批改 → 更新mastery/error_book → send_feishu回复\n"
            f"类型D·普通对话 → 友好回复\n\n"
            f"⚠️ 铁则：无论哪种类型，必须调用 send_feishu(receive_id=\"{reply_target}\", ...) 发送结果！",
            session,
        )
        _log(f"[INFO] 文本处理完成: {result[:300] if result else 'None'}")


# ══════════════════════════════════════════════════════════════════════
# 定时任务
# ══════════════════════════════════════════════════════════════════════

def _log(msg: str):
    """立即写入日志（解决 gunicorn 子线程 stdout 缓冲问题）。"""
    line = f"{msg}\n"
    sys.stdout.write(line)
    sys.stdout.flush()
    sys.stderr.write(line)
    sys.stderr.flush()


def scheduled_daily_push():
    """定时每日推送学习任务：先检查日期和完成状态 → 读取数据 → LLM出题 → 推送。"""
    _log(f"[SCHEDULER] 执行每日推送: {datetime.now()}")

    # ── Python层前置检查（含日期判断，防止旧题永久阻塞）──
    can_push, reason = _can_scheduled_push_today()
    _log(f"[SCHEDULER] 推送前置检查: can_push={can_push}, reason={reason}")
    if not can_push:
        _log(f"[SCHEDULER] ⛔ 跳过推送: {reason}")
        return
    
    # 读轮询配置，获取推送目标
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    target_chat_ids = poll_cfg.get("chat_ids", [])
    if not target_chat_ids:
        _log("[SCHEDULER] 未配置推送目标 chat_ids，跳过")
        return
    
    chat_ids_str = json.dumps(target_chat_ids, ensure_ascii=False)
    _log(f"[SCHEDULER] 推送目标: {chat_ids_str}")
    
    try:
        _log("[SCHEDULER] 初始化 LLM 会话...")
        session = init_new_session()
        _log("[SCHEDULER] 开始 LLM 出题流程...")
        result = run(
            "请执行完整的每日出题推送流程。\n\n"
            "═══════════════════════════════════════\n"
            "第一步：读取数据\n"
            "═══════════════════════════════════════\n"
            "1. 读取 data/mastery.json → 找到首批薄弱知识点（score<95的先出，95分达标线）\n"
            "2. 读取 data/error_book.json → 按艾宾浩斯曲线检查今天该复习的错题\n"
            "3. 读取 data/adjustments.json → 获取题数/难度设置\n"
            "4. 读取 data/knowledge_map.json → 确认知识范围\n\n"
            "═══════════════════════════════════════\n"
            "第二步：出题（用 call_llm，严格质量）\n"
            "═══════════════════════════════════════\n"
            "数学题要求（难度=hard）：\n"
            "- 从北师大版三下7个单元中，优先出 mastery<95 的知识点（95分达标线）\n"
            "- ⚠️ 只出「提升」和「拓展」难度，禁止出基础题\n"
            "- 每道题至少需要2步以上推理才能完成，考察综合运用能力\n"
            "- 题目要有多层逻辑嵌套（如：先算面积→再比较→最后决策）\n"
            "- 应用题要求从实际场景中抽象数学模型，不是简单的代入公式\n"
            "- 图形题需结合测量、估算、空间想象等复合能力\n"
            "- 允许少量超纲挑战题（用🌶️标注），激发思考但给出提示\n"
            "- 包含：复合应用题60% + 图形综合30% + 思维拓展10%\n"
            "- 每道题给出完整标准答案和详细解题思路\n\n"
            "英语KET题要求（重点：写作+词汇+语法）：\n"
            "- ⚠️ 先读 KET备考计划.md 确认当前备考阶段和语法范围\n"
            "- ⚠️ 再读 root.md「KET备考体系」确认出题边界铁律（禁止超阶段出题）\n"
            "- ⚠️ 重点倾斜：写作35% + 词汇25% + 语法20% + 阅读10% + 听力口语各5%\n"
            "- 写作题要求：≥35词短文，给出范文和评分要点（语法/词汇/结构各占分）\n"
            "- 词汇题要求：同义词辨析、短语搭配、语境选词，不只考拼写\n"
            "- 语法题要求：时态填空、改错、句型转换，标注对应KET语法点编号\n"
            "- 包含：短文写作+语法填空+词汇选择+完形填空+句型转换\n"
            "- 每道题给出完整标准答案、纠错提示和知识点链接\n\n"
            "═══════════════════════════════════════\n"
            "第三步：存储 → 存入 data/today_questions.json\n"
            "═══════════════════════════════════════\n"
            "格式：{\"date\":\"2026-05-22\",\"math\":[{id,question,answer,hint,difficulty,topic_id}],\"english\":[{id,question,answer,hint,topic_id}]}\n\n"
            "═══════════════════════════════════════\n"
            "第四步：推送 → 用 send_feishu 发卡片到每个 chat\n"
            "═══════════════════════════════════════\n"
            f"推送目标聊天ID：{chat_ids_str}\n"
            "卡片消息格式要求：\n"
            "- 使用 interactive 卡片，header 用 orange 色，标题「🐱 小肥猫今日学习任务 (日期)」\n"
            "- 数学区用 📐 标识，英语区用 📖 标识\n"
            "- 每道题单独编号（如「第1题」「第2题」），方便小朋友回复\n"
            "- 末尾明确告诉小朋友：回复时请写「第X题答案是...」，可以拍照也可以打字\n"
            "- 加上🐱鼓励语，语气温暖亲切\n\n"
            "⚠️ 重要：send_feishu 的 receive_id 参数就是上面提供的 chat_id（oc_开头），系统会自动识别为聊天ID。",
            session,
        )
        _log(f"[SCHEDULER] 每日推送完成: {result[:300] if result else 'None'}")
    except Exception as e:
        _log(f"[SCHEDULER] 每日推送失败: {e}")
        _log(traceback.format_exc())


def start_scheduler():
    push_time = CFG.get("education", {}).get("push_time", "09:00")
    hour, minute = map(int, push_time.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_daily_push, 'cron', hour=hour, minute=minute, id='daily_push')
    
    # 飞书消息轮询（内网无公网IP模式）
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    if poll_cfg.get("enabled") and poll_cfg.get("chat_ids"):
        interval = poll_cfg.get("interval_seconds", 10)
        scheduler.add_job(poll_feishu_messages, 'interval', seconds=interval, id='feishu_poll')
        print(f"[POLL] 飞书消息轮询已启动，间隔 {interval}s，监控 {len(poll_cfg['chat_ids'])} 个聊天")
    
    scheduler.start()
    print(f"[SCHEDULER] 已启动，每日 {push_time} 推送")


# ══════════════════════════════════════════════════════════════════════
# 飞书消息轮询（内网无公网IP模式）
# ══════════════════════════════════════════════════════════════════════

POLL_STATE_FILE = DATA_DIR / "poll_state.json"

def _load_poll_state() -> dict:
    """加载轮询状态（已处理的消息ID）。"""
    if POLL_STATE_FILE.exists():
        try:
            return json.loads(POLL_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_poll_state(state: dict):
    """保存轮询状态。"""
    POLL_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

def _get_feishu_token_from_env() -> str:
    """获取飞书 access_token（复用 core.py 的缓存，这里做兜底）。"""
    import requests, time as _time
    FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
    FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return ""
    FEISHU_BASE = CFG.get("feishu", {}).get("base_url", "https://open.feishu.cn/open-apis")
    resp = requests.post(
        f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = resp.json()
    return data.get("tenant_access_token", "") if data.get("code") == 0 else ""


def poll_feishu_messages():
    """轮询飞书聊天中的新消息并处理。"""
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    chat_ids = poll_cfg.get("chat_ids", [])
    if not chat_ids:
        return
    
    token = _get_feishu_token_from_env()
    if not token:
        return
    
    import requests as _requests
    headers = {"Authorization": f"Bearer {token}"}
    state = _load_poll_state()
    
    for chat_id in chat_ids:
        try:
            resp = _requests.get(
                f"https://open.feishu.cn/open-apis/im/v1/messages"
                f"?container_id_type=chat&container_id={chat_id}&page_size=5&sort_type=ByCreateTimeDesc",
                headers=headers,
                timeout=10,
            )
            data = resp.json()
            if data.get("code") != 0:
                continue
            
            items = data.get("data", {}).get("items", [])
            for msg in items:
                msg_id = msg.get("message_id", "")
                
                # 跳过自己发的消息和已处理的消息
                sender_type = msg.get("sender", {}).get("sender_type", "")
                if sender_type == "app":
                    continue
                if msg_id in state.get(chat_id, {}):
                    continue
                
                # 标记为已处理
                if chat_id not in state:
                    state[chat_id] = {}
                state[chat_id][msg_id] = time.time()
                _save_poll_state(state)
                
                # ─── 关键修复：轮询API返回的消息格式 ≠ 事件回调格式 ───
                # 轮询API: msg_type + body.content      事件回调: message_type + content
                # 必须做格式归一化，否则 _handle_feishu_event 无法提取文本/图片
                sender_id = msg.get("sender", {}).get("id", "")
                msg_type = msg.get("msg_type", "text")
                content_str = msg.get("body", {}).get("content", "{}")
                normalized_msg = {
                    "message_id": msg_id,
                    "chat_id": chat_id,
                    "message_type": msg_type,
                    "content": content_str,
                }
                event = {
                    "type": "im.message.receive_v1",
                    "sender": {"sender_id": {"open_id": sender_id}},
                    "message": normalized_msg,
                }
                
                print(f"[POLL] 新消息: chat={chat_id[:12]}... sender={sender_id[:12]}... type={msg_type} msg_id={msg_id[:12]}...")
                try:
                    _handle_feishu_event(event)
                except Exception as e:
                    print(f"[POLL] 处理消息出错: {e}")
                    traceback.print_exc()
        
        except Exception as e:
            print(f"[POLL] 轮询 {chat_id[:12]}... 出错: {e}")


# ══════════════════════════════════════════════════════════════════════
# Flask 路由
# ══════════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def index():
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    return jsonify({
        "app": "小肥猫学习助手", "version": "2.0", "status": "running",
        "time": datetime.now().isoformat(),
        "endpoints": {
            "health": "/health",
            "push": "POST /push (手动触发每日出题推送)",
            "poll": "POST /feishu/poll (手动触发消息轮询)",
            "config": "GET/POST /feishu/config (查看/配置轮询聊天)",
        },
        "poll_enabled": poll_cfg.get("enabled", False),
        "poll_chats": len(poll_cfg.get("chat_ids", [])),
    })


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
        "course": {
            "textbook": "北师大版",
            "grade": "三年级下学期",
            "math_subject": "数学",
            "english_subject": "英语KET",
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


@app.route("/feishu/poll", methods=["POST"])
def feishu_poll_trigger():
    """手动触发一次消息轮询（异步，立即返回）。"""
    threading.Thread(target=poll_feishu_messages, daemon=True).start()
    return jsonify({"code": 0, "msg": "已触发轮询"})


@app.route("/push", methods=["POST"])
def manual_push_trigger():
    """手动触发每日推送（异步，立即返回）。"""
    threading.Thread(target=scheduled_daily_push, daemon=True).start()
    return jsonify({"code": 0, "msg": "已触发每日推送，正在后台执行..."})


@app.route("/feishu/config", methods=["GET", "POST"])
def feishu_config():
    """查看/配置轮询的聊天ID。"""
    if request.method == "POST":
        body = request.get_json(force=True, silent=True) or {}
        chat_ids = body.get("chat_ids", [])
        if isinstance(chat_ids, list):
            # 写入 config.toml（简单替换）
            config_path = HOME / "config.toml"
            content = config_path.read_text(encoding="utf-8")
            import re
            new_ids = "chat_ids = " + json.dumps(chat_ids)
            content = re.sub(r'chat_ids\s*=\s*\[.*?\]', new_ids, content)
            config_path.write_text(content, encoding="utf-8")
            # 重新加载 CFG
            import tomli
            CFG.clear()
            CFG.update(tomli.loads(config_path.read_text(encoding="utf-8")))
            return jsonify({"code": 0, "msg": f"已更新，监控 {len(chat_ids)} 个聊天", "chat_ids": chat_ids})
        return jsonify({"code": -1, "msg": "chat_ids 必须是数组"}), 400
    
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    return jsonify({
        "enabled": poll_cfg.get("enabled", False),
        "chat_ids": poll_cfg.get("chat_ids", []),
        "interval_seconds": poll_cfg.get("interval_seconds", 10),
    })


@app.route("/health", methods=["GET"])
def health():
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "poll_enabled": poll_cfg.get("enabled", False),
        "poll_chats": len(poll_cfg.get("chat_ids", [])),
    })


# ══════════════════════════════════════════════════════════════════════
# 调度器自动启动（gunicorn --preload 模式在主进程中执行）
# ══════════════════════════════════════════════════════════════════════

_SCHEDULER_STARTED = False

def _auto_start_scheduler():
    """模块加载时自动启动调度器（gunicorn --preload 在主进程执行，worker 不重复）。"""
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    _SCHEDULER_STARTED = True
    try:
        start_scheduler()
        _log("[INIT] 调度器已自动启动（轮询 + 每日推送）")
    except Exception as e:
        _log(f"[INIT] 调度器启动失败: {e}")

_auto_start_scheduler()


# ══════════════════════════════════════════════════════════════════════
# 主入口（仅 python server.py 直接运行时使用）
# ══════════════════════════════════════════════════════════════════════

def main():
    port = CFG.get("runtime", {}).get("server_port", 8192)
    print(f"\n🐱 小肥猫学习助手 v2.0 启动")
    print(f"   端口: {port}")
    print(f"   飞书事件: http://0.0.0.0:{port}/feishu/event")
    print(f"   管理接口: http://0.0.0.0:{port}/admin/init\n")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
