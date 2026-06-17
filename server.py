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
    ocr_image, send_feishu, find_questions,
    verify_password, DATA_DIR, CFG, HOME as CORE_HOME, Session
)

app = Flask(__name__)

FEISHU_VERIFY_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")

# ─── 会话缓存（保持上下文记忆）────────────────────────────
# 按用户ID缓存会话，避免每次重新初始化
SESSION_CACHE = {}
SESSION_TIMEOUT = 3600  # 1小时超时
SESSION_MAX_SIZE = 100  # 最大缓存会话数

def _get_or_create_session(sender_id: str, chat_id: str = "") -> Session:
    """获取或创建用户会话，保持对话历史。"""
    global SESSION_CACHE
    
    # 使用 sender_id + chat_id 作为会话键（群聊和私聊分开）
    session_key = f"{sender_id}:{chat_id}" if chat_id else sender_id
    
    # 清理过期会话
    current_time = time.time()
    expired_keys = []
    for key, (session, last_active) in list(SESSION_CACHE.items()):
        if current_time - last_active > SESSION_TIMEOUT:
            expired_keys.append(key)
    
    for key in expired_keys:
        if key in SESSION_CACHE:
            del SESSION_CACHE[key]
    
    # 限制缓存大小
    if len(SESSION_CACHE) >= SESSION_MAX_SIZE:
        # 删除最旧的会话
        oldest_key = min(SESSION_CACHE.keys(), key=lambda k: SESSION_CACHE[k][1])
        del SESSION_CACHE[oldest_key]
    
    # 获取或创建会话
    if session_key in SESSION_CACHE:
        session, _ = SESSION_CACHE[session_key]
        SESSION_CACHE[session_key] = (session, current_time)
        return session
    else:
        # 尝试从文件加载历史会话
        session_file = DATA_DIR / "sessions" / f"{session_key.replace(':', '_')}.jsonl"
        if session_file.exists():
            session = Session(session_file)
        else:
            session = init_new_session()
        
        SESSION_CACHE[session_key] = (session, current_time)
        return session

def _save_session_to_file(session_key: str, session: Session):
    """将会话保存到文件（可选，用于持久化）。"""
    try:
        # 确保sessions目录存在
        sessions_dir = DATA_DIR / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存会话到文件
        filename = f"{session_key.replace(':', '_')}.jsonl"
        session.path = sessions_dir / filename
        
        # 如果会话文件已存在，先备份
        if session.path.exists():
            backup_path = session.path.with_suffix('.jsonl.backup')
            session.path.rename(backup_path)
        
        # 写入当前会话历史
        with open(session.path, 'w', encoding='utf-8') as f:
            for msg in session.history:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[WARN] 保存会话失败: {e}")


# ══════════════════════════════════════════════════════════════════════
# 飞书事件处理
# ══════════════════════════════════════════════════════════════════════

# ── 发题/新题 触发关键词（仅用于Python层快速拦截，不用于意图识别）──
_FATI_KEYWORDS = ["发题", "新题", "做题", "来一套", "再来一套", "发一套", "再发", "做新题", "出题", "推送题目", "今日题目", "每日一练"]

# ── 试卷编号生成 ──
_test_id_counter = {}  # 按日期+前缀计数，防止重复

def _gen_test_id(prefix="T"):
    """生成唯一试卷编号，如 T0609A、V0609B。同一天同前缀不会重复。"""
    today = datetime.now().strftime("%m%d")
    key = f"{prefix}{today}"
    count = _test_id_counter.get(key, 0)
    _test_id_counter[key] = count + 1
    if count < 26:
        suffix = chr(ord('A') + count)
    else:
        suffix = chr(ord('A') + count // 26 - 1) + chr(ord('A') + count % 26)
    return f"{prefix}{today}{suffix}"

# ── 全局题号生成（永不重复，跨重启持久化）──
_QUESTION_COUNTER_FILE = DATA_DIR / ".question_counter"

def _gen_question_id():
    """生成全局唯一题号，如 Q000001。跨服务器重启不重复。"""
    count = 1
    if _QUESTION_COUNTER_FILE.exists():
        try:
            count = int(_QUESTION_COUNTER_FILE.read_text().strip()) + 1
        except:
            count = 1
    _QUESTION_COUNTER_FILE.write_text(str(count))
    return f"Q{count:06d}"


def _check_today_questions_completed():
    """Python层快速检查 today_questions.json 完成状态（不依赖LLM）。
    用于手动发题请求：
    - 今天已有题目（不管是否完成）→ 拦截，不出新题
    - 今天没有题目 → 放行
    - 前一天错题未订正 → 拦截，不出新题
    返回 (can_generate: bool, reason: str)
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_file = DATA_DIR / "today_questions.json"
    
    # ── 检查前一天错题是否已订正 ──
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    error_file = DATA_DIR / "error_book.json"
    if error_file.exists():
        try:
            errors = json.loads(error_file.read_text(encoding="utf-8"))
            # 检查昨天是否有未订正的错题（无reviewed_date或reviewed_date为空）
            yesterday_errors = [e for e in errors if e.get("date", "") == yesterday and not e.get("reviewed_date")]
            if yesterday_errors:
                return False, f"昨天还有 {len(yesterday_errors)} 道错题未订正，请先完成错题复习再出新题"
        except Exception:
            pass
    
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

    # 日期不是今天 → 旧题作废，允许出新题
    if file_date and file_date != today_str:
        return True, f"旧题目（{file_date}）已过期"

    if total == 0:
        return True, "今天题目为空"

    # 今天已有题目 → 检查是否全部完成
    completed = sum(1 for q in all_q if "score" in q or "batch_id" in q)
    if completed < total:
        return False, f"今天还有 {total - completed}/{total} 道题未完成"

    return False, "今天题目已全部完成，明天再来吧"


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

    # 使用会话缓存，保持对话历史
    session = _get_or_create_session(sender_id, chat_id)
    
    # 在会话历史中添加上下文提示
    context_prompt = f"[系统上下文：飞书用户 sender_open_id={sender_id}, {target_desc}]"
    
    # 检测文本中的日期提示（"29号"、"5月29日"、"0603"等），预加载对应题目
    import re
    date_match = None
    date_hint = None
    test_id_in_text = None
    if text:
        # ⚠️ 优先检测试卷编号（如 T0612A、V0611A）— 最精确
        tid_m = re.search(r'(?<![A-Za-z0-9])([TVWC]\d{4}[A-Z])(?![A-Za-z0-9])', text)
        if tid_m:
            test_id_in_text = tid_m.group(1)
            date_hint = test_id_in_text
            date_match = tid_m
        else:
            # 匹配 "0603" / "6月3日" / "6.3" / "29号" / "2026-06-03" 等格式
            m = re.search(r'(\d{1,2})\s*[月.\-]\s*(\d{1,2})\s*[日号]?', text)
            if m:
                date_hint = f"{m.group(1)}月{m.group(2)}日"
                date_match = m
            else:
                # 匹配 "0603" 纯数字格式（4位MMDD，后面可能跟中文）
                m = re.search(r'(?<!\d)(\d{2})(\d{2})(?!\d)', text)
                if m:
                    month, day = int(m.group(1)), int(m.group(2))
                    if 1 <= month <= 12 and 1 <= day <= 31:
                        date_hint = f"{month}月{day}日"
                        date_match = m
                else:
                    # 匹配 "29号" / "3日" 等
                    m = re.search(r'(\d{1,2})\s*[日号]', text)
                    if m:
                        date_hint = m.group(0)
                        date_match = m
    
    # 检测答题相关关键词（含"答案"、"的答案"、试卷编号等）
    has_answer = text and ("答案" in text or "的答案" in text or "第" in text or re.search(r'Q\d{6}', text))
    has_answer_keyword = has_answer
    
    # 如果有试卷编号，直接按编号找题（不管是否有答案关键词，先注入题目）
    if test_id_in_text:
        try:
            questions_data = json.loads(find_questions(date_hint=test_id_in_text))
            if questions_data.get("success"):
                qs = questions_data.get("questions", [])
                qs_summary = "\n".join([f"  {q['id']}: {q['question'][:100]}... | 答案:{q.get('answer','')}" for q in qs])
                context_prompt += f"\n[📋 试卷 {test_id_in_text}（{questions_data.get('date','')}）：]\n{qs_summary}"
            else:
                context_prompt += f"\n[⚠️ 未找到试卷 {test_id_in_text}]"
        except Exception:
            pass
    elif date_match and has_answer_keyword:
        try:
            questions_data = json.loads(find_questions(date_hint=date_hint))
            if questions_data.get("success"):
                qs = questions_data.get("questions", [])
                qs_summary = "\n".join([f"  {q['id']}: {q['question'][:100]}... | 答案:{q['answer']}" for q in qs])
                context_prompt += f"\n[📅 {questions_data['date']} 历史题目：]\n{qs_summary}"
            else:
                context_prompt += f"\n[⚠️ 未找到 {date_hint} 的题目存档]"
        except Exception:
            pass
    
    # 通用答题批改上下文（无日期/编号时）
    if (test_id_in_text or date_match) and has_answer:
        pass  # 已在上方处理
    elif has_answer:
        try:
            today_file = DATA_DIR / "today_questions.json"
            if today_file.exists():
                today_data = json.loads(today_file.read_text(encoding="utf-8"))
                questions_summary = "\n".join([f"第{q['id'][1:]}题: {q['question'][:100]}..." for q in today_data.get("questions", [])])
                context_prompt += f"\n[今日题目摘要（{today_data.get('date', '')}）：]\n{questions_summary}"
        except Exception as e:
            print(f"[INFO] 读取今日题目失败: {e}")

    # ── 统一答案匹配（文本/图片都适用）──
    # ⚠️ 只有明确含答案内容时才走批改流程；仅有试卷编号无答案内容 → 走正常意图识别
    is_answering = bool(has_answer or "📋 试卷" in context_prompt or "历史题目" in context_prompt)
    # 有试卷编号但无答案内容 → 注入题目到上下文，走正常LLM意图识别
    if test_id_in_text and not has_answer:
        is_answering = False

    if is_answering and msg_type != "image":
        session = _get_or_create_session(sender_id, chat_id)
        result = run(
            f"{context_prompt}\n"
            f"学生提交答案：{text}\n\n"
            f"上方已给出试卷的题目和标准答案。请直接匹配批改。\n"
            f"答案格式支持：\n"
            f"- 全局题号+答案: Q000001 174\n"
            f"- 题号+答案: 第1题174 / 1.174 / 1)174\n"
            f"- 逗号/空格分隔: 174, 140, 21\n"
            f"- 混合: 1.174 2.140 3.21\n\n"
            f"逐题批改→更新mastery/error_book→send_feishu(receive_id=\"{reply_target}\")",
            session,
        )
        _log(f"[INFO] 答案匹配完成: {result[:200] if result else 'None'}")
        return

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

        # 检测是否为批量图片（session历史中已有同一试卷的信息）
        is_batch = bool("📋 试卷" in context_prompt and session and len(session.history) > 2)
        batch_hint = "（第2+张图片，与前一张同一套试卷）" if is_batch else ""
        
        result = run(
            f"{context_prompt}\n"
            f"学生发来一张图片（手写答案）{batch_hint}，已保存到 {local_path}。\n\n"
            f"步骤1：用 enhanced_ocr_image 识别图片文字（支持长图分割）\n"
            f"步骤2：上下文已有试卷题目和标准答案，直接按题号逐题匹配\n"
            f"步骤3：如果有多张图片（{batch_hint}），合并所有图片的识别结果一起批改\n"
            f"步骤4：逐题批改（✅/❌ + 解析）\n"
            f"步骤5：更新mastery/error_book\n"
            f"步骤6：send_feishu(receive_id=\"{reply_target}\")发送批改结果\n\n"
            f"铁则：按题号匹配，不按顺序猜！enhanced_ocr_image只调用1次！",
            session,
        )
        print(f"[INFO] 图片处理完成: {result[:100] if result else 'None'}")
        return
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
        return  # ⚠️ 文件处理完必须返回
    else:
        # ── Python层快速预检：发题/新题意图（仅拦截，不识别）──
        if _is_request_new_questions(text) and not _is_parent_adjust(text):
            can_gen, reason = _check_today_questions_completed()
            _log(f"[PRE-CHECK] 发题意图检测: can_gen={can_gen}, reason={reason}")
            if not can_gen:
                reply = f"🐱 {reason}哦～先把今天的题目完成，我会等你！回复时写「第X题答案是...」就可以啦～"
                send_feishu(receive_id=reply_target, msg_type="text", content=reply)
                _log(f"[INFO] 发题拦截: {reason} → 已直接回复飞书")
                return
            _log(f"[PRE-CHECK] 题目已完成/无题目，交给LLM生成新题")

        session = _get_or_create_session(sender_id, chat_id)
        
        # ── 先检测试卷编号（如 T0609A、V0609B）→ 直接匹配 ──
        import re
        test_id_match = re.search(r'\b([TVWC]\d{4}[A-Z])\b', text) if text else None
        if test_id_match:
            test_id = test_id_match.group(1)
            # 查找所有试卷存档
            all_tests = {}
            questions_dir = DATA_DIR / "questions"
            if questions_dir.exists():
                for f in questions_dir.glob("*.json"):
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        tid = data.get("test_id", "")
                        if tid:
                            all_tests[tid] = {"file": str(f), "date": data.get("date", ""), "questions": data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))}
                    except: pass
            # 也检查 today_questions.json
            today_file = DATA_DIR / "today_questions.json"
            if today_file.exists():
                try:
                    data = json.loads(today_file.read_text(encoding="utf-8"))
                    tid = data.get("test_id", "")
                    if tid:
                        all_tests[tid] = {"file": str(today_file), "date": data.get("date", ""), "questions": data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))}
                except: pass
            
            if test_id in all_tests:
                t = all_tests[test_id]
                qs = t["questions"]
                qs_summary = "\n".join([f"  {q['id']}: {q['question'][:100]}... | 答案:{q.get('answer','')}" for q in qs])
                context_prompt += f"\n[📋 试卷 {test_id}（{t['date']}）：]\n{qs_summary}"
                
                # ── 如果消息中同时有试卷编号和答案内容 → 直接批改，跳过意图识别 ──
                has_answer_content = bool(re.search(r'[A-Da-d]\b|答案是|答案[：:]', text))
                if has_answer_content:
                    _log(f"[MATCH] 试卷{test_id}已找到+含答案，直接批改")
                    result = run(
                        f"{context_prompt}\n"
                        f"学生提交了试卷 {test_id} 的答案：{text}\n\n"
                        f"请直接批改：\n"
                        f"1. 上面已经注入了试卷{test_id}的题目和标准答案\n"
                        f"2. 按顺序匹配学生答案到对应题目\n"
                        f"3. 逐题批改（✅/❌ + 解析）\n"
                        f"4. 更新mastery/error_book\n"
                        f"5. send_feishu(receive_id=\"{reply_target}\")发送批改结果\n"
                        f"⚠️ 必须调用send_feishu！",
                        session,
                    )
                    return
            else:
                context_prompt += f"\n[⚠️ 未找到试卷 {test_id}，请确认编号是否正确]"

        # ── LLM意图识别（替代关键词枚举）──
        intent_prompt = f"""请分析学生消息的意图，只回复一个JSON：
{{"intent":"意图类型","detail":"补充说明"}}

意图类型：
- new_questions: 要出新题/发题/做题
- grade_answer: 提交答案/批改（含第X题/答案是/选B/答案是apple等）
- fix_questions: 题目有问题/不全/需要修正
- weekly_test: 综合测试/周测
- vocab_train: 词汇训练/背单词/单词测试/生词
- parent_adjust: 家长调参（含密码）
- chat: 普通对话/问候/其他

学生消息：{text}
{"⚠️ 上下文已注入试卷题目，如果学生说'再看一下'/'重新批改'/'前面给过图片'等，意图应为grade_answer（重新批改之前的图片答案）" if test_id_in_text else ""}

只回复JSON，不要其他内容："""

        try:
            intent_resp = call_llm(intent_prompt)
            intent_data = json.loads(intent_resp.strip().replace("```json","").replace("```","").strip())
            intent = intent_data.get("intent", "chat")
        except:
            intent = "chat"
        
        _log(f"[INTENT] LLM识别意图: {intent} ← '{text[:60]}'")

        # ── 根据意图分发 ──
        if intent == "new_questions":
            # ⚠️ 出题前检查：今天已有未完成题目则拦截
            can_gen, reason = _check_today_questions_completed()
            if not can_gen:
                reply = f"🐱 {reason}哦～先把今天的题目完成，我会等你！回复时写「第X题答案是...」或试卷编号就可以啦～"
                send_feishu(receive_id=reply_target, msg_type="text", content=reply)
                _log(f"[INFO] 发题拦截(LLM意图): {reason}")
                return
            result = run(
                f"{context_prompt}\n"
                f"学生要出新题：{text}\n\n"
                f"请执行完整出题流程：\n"
                f"1. 读adjustments/mastery/error_book/knowledge_map\n"
                f"2. 读root.md「KET题型格式模板」+「KET词汇题格式」\n"
                f"3. 读data/ket_vocabulary.json，生成KET风格词汇题（英英释义+语境填空，10题）\n"
                f"4. 生成唯一试卷编号：test_id = \"{_gen_test_id('T')}\"\n"
                f"5. 每道题用 _gen_question_id() 生成全局唯一编号（Q000001, Q000002...）\n"
                f"6. 出题：数学4题+英语4题+词汇10题\n"
                f"7. write_file存today_questions.json（含test_id和每道题的全局编号）\n"
                f"8. write_file存data/questions/questions_{datetime.now().strftime('%Y-%m-%d')}.json归档\n"
                f"9. send_feishu推送，卡片标题含「📋 试卷：{_gen_test_id('T')}」\n"
                f"⚠️ 每道题显示全局编号（如Q000001），学生回复编号即可精准匹配！",
                session,
            )
        elif intent == "grade_answer":
            # 检测是否是"再看一下图片"类请求（无新答案内容，要求重新批改之前的图片）
            is_recheck = not has_answer and test_id_in_text and ("再看" in text or "重新" in text or "图片" in text or "前面" in text)
            if is_recheck:
                result = run(
                    f"{context_prompt}\n"
                    f"学生说：{text}\n\n"
                    f"⚠️ 学生之前发过图片答案（手写），现在要求重新批改。\n"
                    f"上下文已注入试卷 {test_id_in_text} 的题目和标准答案。\n"
                    f"请执行：\n"
                    f"1. 检查会话历史中是否有之前的OCR识别结果（enhanced_ocr_image的输出）\n"
                    f"2. 如果有→直接用之前的识别结果匹配批改\n"
                    f"3. 如果没有→send_feishu告知学生'请重新发送图片，我来批改'\n"
                    f"4. 逐题批改（✅/❌ + 解析）\n"
                    f"5. 更新mastery/error_book\n"
                    f"6. send_feishu(receive_id=\"{reply_target}\")发送批改结果\n"
                    f"⚠️ 必须调用send_feishu！",
                    session,
                )
            else:
                result = run(
                    f"{context_prompt}\n"
                    f"学生提交答案：{text}\n\n"
                    f"请执行批改流程：\n"
                    f"1. ⚠️ 先看上下文中有没有全局题号（如Q000001）或试卷编号（如T0609A）\n"
                    f"2. 有全局题号→直接匹配题目；有试卷编号→读对应文件；都没有→读today_questions.json\n"
                    f"3. 全局题号格式：Q+6位数字（如Q000001），跨所有试卷唯一\n"
                    f"4. ⚠️ 如果答案与题目明显不符，不要直接判错！先send_feishu问是哪套题\n"
                    f"5. call_llm批改→更新mastery/error_book\n"
                    f"6. ⚠️ 错题存入error_book.json时，必须包含试卷编号字段：\n"
                    f"   {{\"id\":\"E{datetime.now().strftime('%m%d')}01\",\"test_id\":\"原试卷编号\",\"date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"question\":\"...\",\"student_answer\":\"...\",\"correct_answer\":\"...\",\"error_type\":\"...\",\"reviewed_date\":null}}\n"
                    f"   错题编号格式：E+MMDD+序号，如E060901\n"
                    f"7. send_feishu(receive_id=\"{reply_target}\")回复批改结果\n"
                    f"⚠️ 铁则：必须调用send_feishu发送结果！",
                    session,
                )
        elif intent == "fix_questions":
            result = run(
                f"{context_prompt}\n"
                f"学生反馈题目有问题：{text}\n\n"
                f"1. 读today_questions.json检查完整性\n"
                f"2. call_llm逐题检查（完形/填空/改错必须有原文+选项）\n"
                f"3. 不完整的重新生成→write_file更新\n"
                f"4. send_feishu(receive_id=\"{reply_target}\")发送修正结果\n"
                f"⚠️ 必须调用send_feishu！",
                session,
            )
        elif intent == "weekly_test":
            result = run(
                f"{context_prompt}\n"
                f"学生请求综合测试：{text}\n\n"
                f"1. 读error_book/mastery/KET备考计划\n"
                f"2. 生成唯一编号：test_id = \"{_gen_test_id('W')}\"\n"
                f"3. 出题：数学4题+英语6题+词汇10题\n"
                f"4. write_file存储（含test_id）\n"
                f"5. send_feishu(receive_id=\"{reply_target}\")推送，标题含「📋 编号：{_gen_test_id('W')}」",
                session,
            )
        elif intent == "vocab_train":
            result = run(
                f"{context_prompt}\n"
                f"学生请求词汇训练：{text}\n\n"
                f"1. 读data/ket_vocabulary.json\n"
                f"2. 生成唯一编号：test_id = \"{_gen_test_id('V')}\"\n"
                f"3. 出题：英英释义匹配+语境填空（全英文，禁止中文）\n"
                f"4. write_file存储（含test_id）\n"
                f"5. send_feishu(receive_id=\"{reply_target}\")推送，标题含「📋 编号：{_gen_test_id('V')}」",
                session,
            )
        elif intent == "parent_adjust":
            result = run(
                f"{context_prompt}\n"
                f"家长调参请求：{text}\n\n"
                f"1. 验证密码→读adjustments.json\n"
                f"2. 执行调整→write_file更新\n"
                f"3. send_feishu(receive_id=\"{reply_target}\")确认",
                session,
            )
        else:
            result = run(
                f"{context_prompt}\n"
                f"学生发来消息：{text}\n\n"
                f"请友好回复。如果是答题/出题/词汇等请求，自行判断并处理。\n"
                f"⚠️ 必须调用 send_feishu(receive_id=\"{reply_target}\", ...) 发送结果！",
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
            "第零步：生成编号\n"
            "═══════════════════════════════════════\n"
            f"test_id = \"{_gen_test_id('T')}\"\n"
            "每道题用 _gen_question_id() 生成全局唯一编号（Q000001, Q000002...）\n"
            "编号跨所有试卷永不重复，学生回复编号即可精准匹配\n"
            "在卡片标题中显示「📋 试卷：{test_id}」\n\n"
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
            "数学题要求（难度=hard，7单元均衡分布）：\n"
            "- ⚠️ 7个单元均衡出题，禁止集中在一个单元！\n"
            "- 单元分布：乘法1题 + 除法1题 + 周长1题 + 图形运动/数据/实践活动轮换1题\n"
            "- 第五单元（关系与规律）最多1题，不能超过25%！\n"
            "- ⚠️ 只出「提升」和「拓展」难度，禁止出基础题\n"
            "- ⚠️ 禁用具体水果/物品名称，改用「圆形」「三角形」「正方形」或纯文字\n"
            "- 每道题至少需要2步以上推理才能完成\n"
            "- 应用题从实际场景中抽象数学模型（购物找零、出行时间、分物余数等）\n"
            "- 图形题结合测量、估算、空间想象\n"
            "- 包含：计算应用40% + 周长/图形30% + 单位换算20% + 规律推理10%\n"
            "- 每道题给出完整标准答案和详细解题思路\n\n"
            "英语KET题要求（难度=KET A2标准，不能太简单）：\n"
            "- ⚠️ 先读 KET备考计划.md 确认当前备考阶段和语法范围\n"
            "- ⚠️ 再读 root.md「KET题型格式模板」确认每种题型的格式要求\n"
            "- ⚠️ 填空/改错/完形填空必须包含完整原文！\n"
            "- ⚠️ 难度对齐KET真题：阅读短文≥80词，完形≥5空，写作≥35词\n"
            "- ⚠️ 重点倾斜：写作35% + 词汇25% + 语法20% + 阅读10% + 听力口语各5%\n"
            "- 写作题：≥35词短文（如写邮件、描述图片、讲故事），给范文+评分要点\n"
            "- 词汇题：同义词辨析、短语搭配、语境选词，不只考拼写\n"
            "- 语法题：时态填空、改错、句型转换，标注KET语法点编号\n"
            "- 阅读题：信息匹配、阅读理解（≥80词短文+3个问题）\n"
            "- 包含：短文写作+阅读理解+语法填空+词汇选择+完形填空\n"
            "- 每道题给出完整标准答案、纠错提示和知识点链接\n\n"
            "═══════════════════════════════════════\n"
            "第三步：KET词汇学习（每日必做，KET风格）\n"
            "═══════════════════════════════════════\n"
            "- ⚠️ 先读取 data/ket_vocabulary.json 获取已学词和生词\n"
            "- 从生词中选5个新词，用KET风格出题：\n"
            "  【英英释义匹配】给出英文释义，让学生选对应的单词\n"
            "  例：This is a fruit. It is red or green. You can eat it. → 答案：apple\n"
            "  【语境填空】给出英文句子，用英文提示让学生填词\n"
            "  例：I drink _____ every morning. (a white drink from cows) → 答案：milk\n"
            "- 从已学词中选5个复习（英英释义+语境填空）\n"
            "- 将新词写入 data/ket_vocabulary.json（与单词训练营共享同一文件）\n"
            "- 格式：[{{\"word\":\"apple\",\"chinese\":\"苹果\",\"pos\":\"n.\",\"example\":\"I eat an apple.\",\"learned_date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"review_dates\":[],\"mastered\":false}}]\n\n"
            "═══════════════════════════════════════\n"
            "第四步：存储 → 存入 data/today_questions.json\n"
            "═══════════════════════════════════════\n"
            f"格式：{{\"test_id\":\"{_gen_test_id('T')}\",\"date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"math\":[{{id,question,answer,hint,difficulty,topic_id}}],\"english\":[{{id,question,answer,hint,topic_id}}],\"vocab\":[{{id,question,answer,hint}}]}}\n\n"
            "═══════════════════════════════════════\n"
            "第五步：推送 → 用 send_feishu 发卡片到每个 chat\n"
            "═══════════════════════════════════════\n"
            f"推送目标聊天ID：{chat_ids_str}\n"
            "⚠️ 必须发两张卡片：\n"
            "  卡片1：数学+英语题目\n"
            "  卡片2：KET词汇测试（英英释义+语境填空，10题）\n"
            "卡片消息格式要求：\n"
            f"- ⚠️ 日期必须是今天：{datetime.now().strftime('%Y-%m-%d')}（{['周一','周二','周三','周四','周五','周六','周日'][datetime.now().weekday()]}），禁止用其他日期\n"
            "- 使用 interactive 卡片，header 用 orange 色\n"
            f"- 标题必须用「🐱 小肥猫今日学习任务（{datetime.now().strftime('%-m月%-d日')} {['周一','周二','周三','周四','周五','周六','周日'][datetime.now().weekday()]}）」\n"
            "- 词汇卡片标题用「📖 今日KET词汇（{datetime.now().strftime('%-m月%-d日')}）」\n"
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


def scheduled_weekly_test():
    """每周日综合测试：复习本周错题+新内容+词汇检测。"""
    _log(f"[SCHEDULER] 执行每周综合测试: {datetime.now()}")
    
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    target_chat_ids = poll_cfg.get("chat_ids", [])
    if not target_chat_ids:
        return
    
    chat_ids_str = json.dumps(target_chat_ids, ensure_ascii=False)
    
    try:
        session = init_new_session()
        result = run(
            "请执行每周综合测试推送。\n\n"
            "═══════════════════════════════════════\n"
            "第零步：生成编号\n"
            "═══════════════════════════════════════\n"
            f"test_id = \"{_gen_test_id('W')}\"\n"
            "每道题用 _gen_question_id() 生成全局唯一编号\n"
            "在卡片标题中显示「📋 试卷：{test_id}」\n\n"
            "═══════════════════════════════════════\n"
            "第一步：读取本周数据\n"
            "═══════════════════════════════════════\n"
            "1. 读取 data/error_book.json → 筛选本周错题（date在本周范围内）\n"
            "2. 读取 data/mastery.json → 找到本周练习过的知识点\n"
            "3. 读取 data/knowledge_map.json → 确认知识范围\n"
            "4. 读取 KET备考计划.md → 确认当前阶段\n\n"
            "═══════════════════════════════════════\n"
            "第二步：出题（综合测试）\n"
            "═══════════════════════════════════════\n"
            "数学（4题）：\n"
            "- 2题来自本周错题（变式题，同知识点不同题目）\n"
            "- 2题新内容（本周未覆盖的知识点）\n"
            "- 禁用水果/物品名，用图形或纯文字\n\n"
            "英语KET（6题）：\n"
            "- 2题来自本周错题（变式题）\n"
            "- 2题新语法/词汇\n"
            "- 2题词汇检测（本周学过的KET词汇，英译中+中译英+选词填空）\n"
            "- ⚠️ 必须读 root.md「KET题型格式模板」！完形/填空/改错必须含完整原文和选项！\n\n"
            "═══════════════════════════════════════\n"
            "第三步：词汇复习\n"
            "═══════════════════════════════════════\n"
            "- 从本周题目中提取KET核心词汇（至少10个）\n"
            "- 生成词汇表：英文+中文+例句\n"
            "- 用 send_feishu 推送词汇卡片\n\n"
            "═══════════════════════════════════════\n"
            "第四步：推送\n"
            "═══════════════════════════════════════\n"
            f"推送目标：{chat_ids_str}\n"
            f"标题：🐱 小肥猫每周综合测试（{datetime.now().strftime('%-m月%-d日')} 周日）\n"
            "用 send_feishu 发送卡片，包含数学+英语+词汇表\n"
            "⚠️ 必须调用 send_feishu 发送结果！",
            session,
        )
        _log(f"[SCHEDULER] 每周测试完成: {result[:300] if result else 'None'}")
    except Exception as e:
        _log(f"[SCHEDULER] 每周测试失败: {e}")
        _log(traceback.format_exc())


def scheduled_daily_vocab():
    """每日KET词汇推送：英英释义+语境填空，与单词训练营共享词库。"""
    _log(f"[SCHEDULER] 执行每日词汇推送: {datetime.now()}")
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    target_chat_ids = poll_cfg.get("chat_ids", [])
    if not target_chat_ids:
        return
    chat_ids_str = json.dumps(target_chat_ids, ensure_ascii=False)
    try:
        session = init_new_session()
        result = run(
            "请执行每日KET词汇推送。\n\n"
            "═══════════════════════════════════════\n"
            "第零步：生成编号\n"
            "═══════════════════════════════════════\n"
            f"test_id = \"{_gen_test_id('V')}\"\n"
            "每道题用 _gen_question_id() 生成全局唯一编号\n"
            "在卡片标题中显示「📋 试卷：{test_id}」\n\n"
            "═══════════════════════════════════════\n"
            "第一步：读取词库\n"
            "═══════════════════════════════════════\n"
            "1. 读取 data/ket_vocabulary.json（如不存在则用 call_llm 从KET词表创建，至少50词）\n"
            "2. 筛选：status='new'的选5个新词，status='learning'的选5个复习词\n\n"
            "═══════════════════════════════════════\n"
            "第二步：出题（KET风格，英英释义）\n"
            "═══════════════════════════════════════\n"
            "⚠️ 必须用英语解释英语，禁止出现中文！\n"
            "新词5题（英英释义匹配）：\n"
            "- 格式：给出英文释义，4个选项（同主题词），选正确单词\n"
            "- 例：This is a fruit. It is red or green. You can eat it.\n"
            "      A. bread  B. apple  C. chicken  D. rice\n"
            "复习词5题（语境填空）：\n"
            "- 格式：英文句子+英文提示，填单词\n"
            "- 例：I drink _____ every morning. (a white drink from cows)\n\n"
            "═══════════════════════════════════════\n"
            "第三步：更新词库\n"
            "═══════════════════════════════════════\n"
            "- 将新词status改为'learning'，learned_date设为今天\n"
            "- 将复习词review_count+1，如>=3则status改为'mastered'\n"
            "- 用 write_file 更新 data/ket_vocabulary.json\n\n"
            "═══════════════════════════════════════\n"
            "第四步：推送\n"
            "═══════════════════════════════════════\n"
            f"推送目标：{chat_ids_str}\n"
            f"标题：📖 今日KET词汇（{datetime.now().strftime('%-m月%-d日')}）\n"
            "用 send_feishu 发送词汇卡片（10题：5新词+5复习）\n"
            "⚠️ 必须调用 send_feishu 发送结果！",
            session,
        )
        _log(f"[SCHEDULER] 词汇推送完成: {result[:200] if result else 'None'}")
    except Exception as e:
        _log(f"[SCHEDULER] 词汇推送失败: {e}")
        _log(traceback.format_exc())


def scheduled_daily_calc():
    """每日数学计算专项：加减乘除+四则混合运算，重点练速度和准确度。"""
    _log(f"[SCHEDULER] 执行每日计算专项: {datetime.now()}")
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    target_chat_ids = poll_cfg.get("chat_ids", [])
    if not target_chat_ids:
        return
    chat_ids_str = json.dumps(target_chat_ids, ensure_ascii=False)
    try:
        session = init_new_session()
        result = run(
            "请执行每日数学计算专项推送。\n\n"
            "═══════════════════════════════════════\n"
            "第零步：生成编号\n"
            "═══════════════════════════════════════\n"
            f"test_id = \"{_gen_test_id('C')}\"\n"
            "每道题用 _gen_question_id() 生成全局唯一编号\n"
            "在卡片标题中显示「📋 试卷：{test_id}」\n\n"
            "═══════════════════════════════════════\n"
            "出题要求（纯计算，10题，适合小学生）：\n"
            "═══════════════════════════════════════\n"
            "- 两位数×一位数：2题（如 47×6=）\n"
            "- 三位数÷一位数：2题（如 258÷3=）\n"
            "- 三位数加减法：2题（如 456+278=, 803-267=）\n"
            "- 四则混合运算：2题（如 25+36÷6=, (45-18)×3=）\n"
            "- 连乘/连除：2题（如 12×3×2=, 96÷4÷2=）\n"
            "- ⚠️ 纯计算题，不需要应用题，不需要图形\n"
            "- 每道题给出标准答案\n"
            "- 卡片标题：「🧮 今日计算专项（{datetime.now().strftime('%-m月%-d日')}）」\n"
            "- 提示学生：限时10分钟完成，记录用时\n\n"
            "═══════════════════════════════════════\n"
            "存储\n"
            "═══════════════════════════════════════\n"
            f"用 write_file 存入 data/questions/questions_{datetime.now().strftime('%Y-%m-%d')}_calc.json\n"
            f"格式：{{\"test_id\":\"{_gen_test_id('C')}\",\"date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"type\":\"calc\",\"questions\":[{{id,question,answer}}]}}\n\n"
            "═══════════════════════════════════════\n"
            "推送\n"
            "═══════════════════════════════════════\n"
            f"推送目标：{chat_ids_str}\n"
            "用 send_feishu 发送计算卡片\n"
            "⚠️ 必须调用 send_feishu 发送结果！",
            session,
        )
        _log(f"[SCHEDULER] 计算专项完成: {result[:200] if result else 'None'}")
    except Exception as e:
        _log(f"[SCHEDULER] 计算专项失败: {e}")
        _log(traceback.format_exc())


def start_scheduler():
    push_time = CFG.get("education", {}).get("push_time", "09:00")
    hour, minute = map(int, push_time.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_daily_push, 'cron', hour=hour, minute=minute, id='daily_push')
    
    # 每日词汇推送（比主推送晚5分钟）
    vocab_minute = (minute + 5) % 60
    vocab_hour = hour + (1 if minute + 5 >= 60 else 0)
    scheduler.add_job(scheduled_daily_vocab, 'cron', hour=vocab_hour, minute=vocab_minute, id='daily_vocab')
    
    # 每日计算专项（比词汇推送晚5分钟）
    calc_minute = (vocab_minute + 5) % 60
    calc_hour = vocab_hour + (1 if vocab_minute + 5 >= 60 else 0)
    scheduler.add_job(scheduled_daily_calc, 'cron', hour=calc_hour, minute=calc_minute, id='daily_calc')
    
    # 每周日综合测试
    scheduler.add_job(scheduled_weekly_test, 'cron', day_of_week='sun', hour=hour, minute=minute, id='weekly_test')
    
    # 飞书消息轮询
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    if poll_cfg.get("enabled") and poll_cfg.get("chat_ids"):
        interval = poll_cfg.get("interval_seconds", 10)
        scheduler.add_job(poll_feishu_messages, 'interval', seconds=interval, id='feishu_poll')
        print(f"[POLL] 飞书消息轮询已启动，间隔 {interval}s，监控 {len(poll_cfg['chat_ids'])} 个聊天")
    
    scheduler.start()
    print(f"[SCHEDULER] 已启动，每日 {push_time} 推送 + 词汇 + 周日综合测试")


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


@app.route("/test_push", methods=["GET"])
def test_push():
    """测试：直接发送一条飞书消息到轮询聊天，验证飞书通道。"""
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    chat_ids = poll_cfg.get("chat_ids", [])
    if not chat_ids:
        return jsonify({"code": -1, "msg": "未配置 chat_ids"})
    results = []
    for cid in chat_ids:
        r = send_feishu(receive_id=cid, msg_type="text", content="🐱 小肥猫测试消息：飞书通道正常！如果你看到这条消息，说明服务运行正常。")
        results.append({"chat_id": cid, "result": r})
    return jsonify({"code": 0, "results": results})


@app.route("/clear_today", methods=["POST"])
def clear_today():
    """清理 today_questions.json（解决旧题阻塞问题）。"""
    today_file = DATA_DIR / "today_questions.json"
    if today_file.exists():
        today_file.unlink()
        return jsonify({"code": 0, "msg": "已清理 today_questions.json"})
    return jsonify({"code": 0, "msg": "文件不存在，无需清理"})


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


@app.route("/debug/logs", methods=["GET"])
def debug_logs():
    """查看最近的服务日志（调试用）。"""
    lines = request.args.get("lines", 50, type=int)
    try:
        result = bash(f"journalctl -u cat-learning --no-pager -n {lines} 2>/dev/null || echo 'journalctl not available'")
        return jsonify({"code": 0, "logs": result})
    except Exception as e:
        return jsonify({"code": -1, "error": str(e)})


@app.route("/debug/session", methods=["GET"])
def debug_session():
    """查看当前活跃的会话缓存（调试用）。"""
    sender_id = request.args.get("sender_id", "")
    chat_id = request.args.get("chat_id", "")
    if sender_id:
        session_key = f"{sender_id}:{chat_id}" if chat_id else sender_id
        if session_key in SESSION_CACHE:
            session, last_active = SESSION_CACHE[session_key]
            history_summary = [{"role": m.get("role"), "content": str(m.get("content",""))[:200]} for m in session.history[-10:]]
            return jsonify({
                "code": 0, "found": True,
                "session_key": session_key,
                "last_active": datetime.fromtimestamp(last_active).isoformat(),
                "history_length": len(session.history),
                "recent_history": history_summary,
            })
        return jsonify({"code": 0, "found": False, "session_key": session_key})
    # 列出所有活跃会话
    sessions = {}
    for key, (session, last_active) in SESSION_CACHE.items():
        sessions[key] = {
            "last_active": datetime.fromtimestamp(last_active).isoformat(),
            "history_length": len(session.history),
        }
    return jsonify({"code": 0, "active_sessions": len(sessions), "sessions": sessions})


@app.route("/debug/test_poll", methods=["POST"])
def debug_test_poll():
    """手动触发一次轮询并返回结果（调试用，同步等待）。"""
    try:
        poll_feishu_messages()
        return jsonify({"code": 0, "msg": "轮询完成"})
    except Exception as e:
        return jsonify({"code": -1, "error": str(e)})


# ══════════════════════════════════════════════════════════════════════
# 调度器自动启动（gunicorn --preload 模式在主进程中执行）
# ══════════════════════════════════════════════════════════════════════

_SCHEDULER_STARTED = False

def _auto_start_scheduler():
    """模块加载时自动启动调度器。
    使用文件锁防止gunicorn多worker重复启动（每个worker独立进程，全局变量无法跨进程共享）。
    """
    global _SCHEDULER_STARTED
    if _SCHEDULER_STARTED:
        return
    
    # 文件锁：只有第一个获取锁的worker启动调度器
    lock_file = DATA_DIR / ".scheduler.lock"
    import fcntl
    try:
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        # 锁已被其他worker持有，跳过
        _SCHEDULER_STARTED = True
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
