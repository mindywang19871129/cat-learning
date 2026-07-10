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
    ocr_image, enhanced_ocr_image, send_feishu, find_questions,
    call_llm, verify_password, DATA_DIR, CFG, HOME as CORE_HOME, Session
)
import core as _core_mod

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
_FATI_KEYWORDS = ["发题", "新题", "做题", "来一套", "再来一套", "发一套", "再发", "做新题", "出题", "推送题目", "今日题目", "每日一练", "出今日任务", "生成今日任务"]

# ── 学习队列模式 ──
_LEARNING_QUEUE_FILE = DATA_DIR / "learning_queue.json"
_IMAGE_CACHE = {}  # {task_id: [{"path":..., "time":...}]}
_IMAGE_CACHE_TIMEOUT = 120  # 2分钟超时
_IMAGE_CACHE_META = {}  # {task_id: {"first_image_at": float, "last_image_at": float, "auto_submitted": bool}}

# ── 自适应难度追踪 ──
_RECENT_RESULTS = {}  # {topic: {"wrong_count": int, "basic_wrong": bool, "last_task_id": str}}

# 自动提交超时配置（秒）
_AUTO_SUBMIT_TIMEOUT_LEARNING = 300   # 孩子答案图片：5分钟
_AUTO_SUBMIT_TIMEOUT_ERROR = 600      # 家长错题图片：10分钟
_AUTO_SUBMIT_SCAN_INTERVAL = 60       # 扫描间隔：60秒

# 任务类型优先级（数字越小越优先）
# 从 config.toml [[tasks]] 构建优先级和标题映射
_TASK_PRIORITY = {}
_TASK_TITLES = {}
for _t in CFG.get("tasks", []):
    _TASK_PRIORITY[_t["type"]] = _t.get("priority", 99)
    _TASK_TITLES[_t["type"]] = _t.get("title", _t["type"])
# 补充非每日任务类型
_TASK_PRIORITY.setdefault("english", 7)
_TASK_PRIORITY.setdefault("error_review", 8)
_TASK_TITLES.setdefault("english", "📝 英语练习")
_TASK_TITLES.setdefault("error_review", "🔄 错题订正")

def _load_learning_queue() -> dict:
    """加载学习队列，自动清理超过7天的过期任务。"""
    if _LEARNING_QUEUE_FILE.exists():
        try:
            q = json.loads(_LEARNING_QUEUE_FILE.read_text(encoding="utf-8"))
            # 自动清理过期任务（超过7天未完成）
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            old_len = len(q.get("queue", []))
            q["queue"] = [t for t in q.get("queue", []) if t.get("date", "") >= cutoff or t.get("status") == "graded"]
            if len(q.get("queue", [])) < old_len:
                _save_learning_queue(q)
            return q
        except Exception:
            pass
    return {"active_task_id": None, "mode": "idle", "queue": [], "error_upload_mode": {"active": False, "image_paths": []}}

def _save_learning_queue(q: dict):
    """保存学习队列。"""
    _LEARNING_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LEARNING_QUEUE_FILE.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")

_MIGRATION_DONE = False

def _migrate_old_tasks_to_queue():
    """扫描历史题目，把未完成任务迁移进学习队列（仅首次调用执行）。"""
    global _MIGRATION_DONE
    q = _load_learning_queue()
    if _MIGRATION_DONE:
        return q
    _MIGRATION_DONE = True
    existing_ids = {t["task_id"] for t in q.get("queue", [])}
    
    # 扫描 questions/ 目录
    questions_dir = DATA_DIR / "questions"
    if questions_dir.exists():
        for f in sorted(questions_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                tid = data.get("test_id", "")
                if not tid or tid in existing_ids:
                    continue
                # 判断任务类型（从 config.toml [[tasks]] 构建映射）
                prefix = tid[0] if tid else ""
                ttype_map = {t.get("prefix", ""): t["type"] for t in CFG.get("tasks", []) if t.get("prefix")}
                ttype = ttype_map.get(prefix, "math")
                # 检查是否已完成（所有题都有score或batch_id）
                qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
                all_done = all("score" in q or "batch_id" in q for q in qs) if qs else False
                if all_done:
                    continue
                # 超过7天的旧任务自动过期，不加入队列
                task_date = data.get("date", "")
                if task_date:
                    try:
                        days_old = (datetime.now() - datetime.strptime(task_date, "%Y-%m-%d")).days
                        if days_old > 7:
                            _log(f"[MIGRATE] 跳过过期任务 {tid}（{task_date}，{days_old}天前）")
                            continue
                    except:
                        pass
                existing_ids.add(tid)
                q.setdefault("queue", []).append({
                    "task_id": tid,
                    "date": data.get("date", ""),
                    "type": ttype,
                    "title": _TASK_TITLES.get(ttype, "综合练习"),
                    "status": "pending",
                    "priority": _TASK_PRIORITY.get(ttype, 99),
                    "questions_file": str(f),
                    "image_paths": [],
                    "submitted_at": None,
                    "graded_at": None,
                })
            except Exception:
                pass
    
    # 也检查 today_questions.json
    today_file = DATA_DIR / "today_questions.json"
    if today_file.exists():
        try:
            data = json.loads(today_file.read_text(encoding="utf-8"))
            tid = data.get("test_id", "")
            if tid and tid not in existing_ids:
                prefix = tid[0] if tid else ""
                ttype_map = {t.get("prefix", ""): t["type"] for t in CFG.get("tasks", []) if t.get("prefix")}
                ttype = ttype_map.get(prefix, "math")
                qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
                all_done = all("score" in q or "batch_id" in q for q in qs) if qs else False
                # 超过7天的旧任务自动过期
                task_date = data.get("date", "")
                expired = False
                if task_date:
                    try:
                        days_old = (datetime.now() - datetime.strptime(task_date, "%Y-%m-%d")).days
                        if days_old > 7:
                            expired = True
                    except:
                        pass
                if not all_done and not expired:
                    existing_ids.add(tid)
                    q.setdefault("queue", []).append({
                        "task_id": tid,
                        "date": data.get("date", ""),
                        "type": ttype,
                        "title": _TASK_TITLES.get(ttype, "综合练习"),
                        "status": "pending",
                        "priority": _TASK_PRIORITY.get(ttype, 99),
                        "questions_file": str(today_file),
                        "image_paths": [],
                        "submitted_at": None,
                        "graded_at": None,
                    })
        except Exception:
            pass
    
    # 按优先级+日期排序
    q["queue"] = sorted(q.get("queue", []), key=lambda t: (t.get("priority", 99), t.get("date", "")))
    _save_learning_queue(q)
    return q

def _get_next_pending_task(q: dict) -> dict | None:
    """获取队列中下一个待处理任务。"""
    for t in q.get("queue", []):
        if t.get("status") in ("pending", "in_progress", "image_received"):
            return t
    return None

def _add_task_to_queue(task_id: str, date_str: str, task_type: str, questions_file: str, difficulty: str = "normal"):
    """将任务加入学习队列（去重）。"""
    q = _load_learning_queue()
    existing_ids = {t["task_id"] for t in q.get("queue", [])}
    if task_id in existing_ids:
        return q
    q.setdefault("queue", []).append({
        "task_id": task_id,
        "date": date_str,
        "type": task_type,
        "title": _TASK_TITLES.get(task_type, "综合练习"),
        "status": "pending",
        "priority": _TASK_PRIORITY.get(task_type, 99),
        "questions_file": questions_file,
        "difficulty": difficulty,
        "image_paths": [],
        "submitted_at": None,
        "graded_at": None,
    })
    q["queue"] = sorted(q["queue"], key=lambda t: (t.get("priority", 99), t.get("date", "")))
    _save_learning_queue(q)
    return q

def _push_first_pending_to_all():
    """推送队列中第一个待处理任务到所有配置聊天（仅当无活跃任务时）。"""
    q = _load_learning_queue()
    _log(f"[PUSH] 队列状态: active={q.get('active_task_id')}, mode={q.get('mode')}, queue_size={len(q.get('queue', []))}")
    if q.get("active_task_id") and q.get("mode") == "answering":
        _log(f"[PUSH] 已有活跃任务，跳过推送")
        return
    task = _get_next_pending_task(q)
    if not task:
        _log(f"[PUSH] 队列中无待处理任务")
        return
    _log(f"[PUSH] 推送任务: {task['task_id']} type={task.get('type')} file={task.get('questions_file')}")
    task["status"] = "in_progress"
    q["active_task_id"] = task["task_id"]
    q["mode"] = "answering"
    _save_learning_queue(q)
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    for chat_id in poll_cfg.get("chat_ids", []):
        _push_task_to_feishu(task, chat_id)

def _push_task_to_feishu(task: dict, reply_target: str):
    """推送单个任务到飞书。"""
    try:
        qf = Path(task["questions_file"])
        if not qf.exists():
            _log(f"[PUSH] ⚠️ 题目文件不存在: {task['questions_file']}")
            send_feishu(receive_id=reply_target, msg_type="text",
                       content=f"🐱 找不到任务 {task['task_id']} 的题目文件，请联系管理员～")
            return
        data = json.loads(qf.read_text(encoding="utf-8"))
        task_type = task.get("type", "")
        
        # 不同任务类型的问题提取
        if task_type == "writing":
            qs = [{"id": "W1", "question": data.get("prompt", "")}]
            scoring = data.get("scoring_points", [])
        else:
            qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
            scoring = []
        
        emoji_map = {"calc": "🔢", "math": "📐", "geometry": "📏", "vocab": "📚", "grammar": "📖", "writing": "✏️", "english": "📝", "error_review": "🔄", "review": "💡"}
        emoji = emoji_map.get(task_type, "📋")
        
        diff_map = {"basic": "（基础）", "normal": "", "hard": "（提升）", "advanced": "（拓展）"}
        diff_hint = diff_map.get(task.get("difficulty", ""), "")
        
        lines = [f"{emoji} {task['title']}{diff_hint} 任务", f"试卷编号：{task['task_id']}"]
        if task_type != "writing":
            lines.append(f"题目数量：{len(qs)}题")
        lines.append("")
        for i, q in enumerate(qs, 1):
            qid = q.get("id", f"Q{i}")
            qtext = q.get("question", "")[:200]
            lines.append(f"第{i}题 [{qid}]：{qtext}")
        
        if scoring:
            lines.append("")
            lines.append("评分要点：")
            for sp in scoring:
                lines.append(f"  • {sp}")
        
        lines.append("")
        if task_type == "geometry":
            lines.append("⚠️ 几何题配有图形，请查看飞书消息中的图片。")
        lines.append("请在纸上写下答案，拍照后直接发给我。")
        lines.append("发完所有图片后回复「提交」开始批改。")
        lines.append("🐱 加油！")
        
        send_feishu(receive_id=reply_target, msg_type="text", content="\n".join(lines))
        _log(f"[QUEUE] 推送任务 {task['task_id']} 到 {reply_target[:16]}...")
    except Exception as e:
        _log(f"[QUEUE] 推送任务失败: {e}")
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"🐱 推送任务 {task['task_id']} 时出错了：{e}")

def _submit_active_task(sender_id: str, chat_id: str, reply_target: str, is_auto: bool = False) -> bool:
    """提交当前活跃任务进行批改。is_auto=True 表示自动提交。"""
    q = _load_learning_queue()
    active_id = q.get("active_task_id")
    if not active_id or q.get("mode") != "answering":
        if not is_auto:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 当前没有正在进行的任务哦～请先回复「开始学习」")
        return True

    # 检查是否已自动提交过
    meta = _IMAGE_CACHE_META.get(active_id, {})
    if is_auto and meta.get("auto_submitted"):
        return True

    # 找到活跃任务
    task = None
    for t in q.get("queue", []):
        if t["task_id"] == active_id:
            task = t
            break

    if not task:
        if not is_auto:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 找不到当前任务，请重新「开始学习」")
        q["active_task_id"] = None
        q["mode"] = "idle"
        _save_learning_queue(q)
        return True

    # 合并图片缓存
    cached = _IMAGE_CACHE.get(active_id, [])
    task["image_paths"].extend([c["path"] for c in cached])
    _IMAGE_CACHE.pop(active_id, None)
    _IMAGE_CACHE_META.pop(active_id, None)

    if not task["image_paths"]:
        if not is_auto:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 还没有收到答案图片哦～请拍照后发给我，再回复「提交」")
        return True

    task["status"] = "submitted"
    task["submitted_at"] = datetime.now().isoformat()
    _save_learning_queue(q)

    # 异步批改
    def _grade_task():
        try:
            session = _get_or_create_session(sender_id, chat_id)
            img_paths = task["image_paths"]
            qf = Path(task["questions_file"])
            if not qf.exists():
                send_feishu(receive_id=reply_target, msg_type="text", content="🐱 题目文件丢失了...")
                return
            data = json.loads(qf.read_text(encoding="utf-8"))
            qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
            qs_summary = "\n".join([f"  {q['id']}: {q['question'][:100]}... | 答案:{q.get('answer','')}" for q in qs])

            # OCR所有图片
            all_ocr = []
            for img_path in img_paths:
                if not Path(img_path).exists():
                    _log(f"[QUEUE] ⚠️ 图片文件不存在，跳过: {img_path}")
                    continue
                try:
                    ocr_result = json.loads(enhanced_ocr_image(img_path))
                    if ocr_result.get("success"):
                        all_ocr.append(ocr_result.get("text", ""))
                except Exception as e:
                    _log(f"[QUEUE] ⚠️ OCR失败: {img_path} - {e}")
                try:
                    Path(img_path).unlink(missing_ok=True)
                except Exception:
                    pass

            ocr_text = "\n---\n".join(all_ocr)

            # ── OCR结果校验：仅检查格式合理性，不比对标准答案（学生可能真的答错了）──
            if ocr_text and qs:
                try:
                    validate_prompt = (
                        "你是OCR手写识别校验专家。请仅修正OCR的识别错误，不要修正学生的真实答案。\n\n"
                        "⚠️ 铁则：学生可能真的答错了，所以不能把OCR结果改成标准答案！\n"
                        "你只能修正OCR技术层面的识别错误（如'Z1'应该是'21'，'l2'应该是'12'）\n\n"
                        "修正规则：\n"
                        "1. 数字/字母混淆：0↔O, 1↔l↔I, 2↔Z, 5↔S, 6↔G, 7↔1, 8↔B\n"
                        "2. 运算符混淆：×↔x, ÷↔+, =↔-\n"
                        "3. 如果OCR结果看起来是合理的数字答案（即使可能是错的），保留原样\n"
                        "4. 如果无法确定，保留原始OCR结果\n"
                        "5. 逐题输出，格式：题号|原始OCR|修正后（或'保留'）\n\n"
                        f"原始OCR结果：\n{ocr_text}\n\n"
                        "请输出修正后的答案（逐题，格式：题号|修正后答案）："
                    )
                    validated = call_llm(validate_prompt)
                    if validated and len(validated) > 3 and "|" in validated:
                        _log(f"[OCR-VALIDATE] 校验修正: {validated[:100]}")
                        ocr_text = validated
                except Exception as e:
                    _log(f"[OCR-VALIDATE] 校验失败: {e}")

            auto_hint = "\n⚠️ 这是系统自动提交的（学生超时未手动提交），请在批改结果开头说明。\n" if is_auto else ""
            task_type_hint = task.get("type", "")
            task_topic = ""
            if task_type_hint == "calc": task_topic = "计算"
            elif task_type_hint == "math": task_topic = "数学"
            elif task_type_hint == "geometry": task_topic = "几何"
            elif task_type_hint == "vocab": task_topic = "KET词汇"
            elif task_type_hint == "grammar": task_topic = "英语语法"
            elif task_type_hint == "writing": task_topic = "英语写作"
            elif task_type_hint == "english": task_topic = "英语"
            elif task_type_hint == "review": task_topic = "基础复习"
            
            result = run(
                f"[系统上下文：飞书用户 sender_open_id={sender_id}, 回复目标ID={reply_target}]\n"
                f"学生提交了任务 {task['task_id']} 的手写答案图片，OCR识别结果如下：\n\n"
                f"{ocr_text}\n\n"
                f"试卷题目和标准答案：\n{qs_summary}\n\n"
                f"{auto_hint}"
                f"请逐题批改（✅/❌ + 解析），更新mastery/error_book。\n\n"
                f"⚠️ 自适应难度规则（root.md第9节）：\n"
                f"- 如果基础题错了（score<50的知识点）→ 批改结果最后必须加上一行：\n"
                f"  [NEEDS_REVIEW:{task_topic}]\n"
                f"- 如果有1道以上错题 → 加一行：[ERROR_COUNT:{{错误的题数}}]\n"
                f"- 如果全部答对 → 加一行：[ALL_CORRECT]\n\n"
                f"然后用 send_feishu(receive_id=\"{reply_target}\") 发送批改结果。\n"
                f"如果全对，在消息末尾自然地提示已自动进入下一项，不用写「回复继续」。\n"
                f"如果有错题，在消息末尾写「回复「继续」做下一项，回复「任务清单」查看进度🐱」",
                session,
            )

            # ── 更新任务状态 ──
            q2 = _load_learning_queue()
            need_review = False
            review_topic = ""
            all_correct = False
            for t in q2.get("queue", []):
                if t["task_id"] == task["task_id"]:
                    t["status"] = "graded"
                    t["graded_at"] = datetime.now().isoformat()
                    t["image_paths"] = []
                    break
            
            # ── 自适应难度：检测是否需要基础复习（仅非复习任务触发）──
            if result and "[NEEDS_REVIEW:" in result and task.get("type") != "review":
                import re
                m = re.search(r'\[NEEDS_REVIEW:([^\]]+)\]', str(result))
                if m:
                    review_topic = m.group(1).strip() or "基础概念"
                    need_review = True
                    _log(f"[ADAPTIVE] 检测到基础概念薄弱，需要复习: {review_topic}")

            # ── 检测全对，自动推下一个任务 ──
            if result and "[ALL_CORRECT]" in str(result):
                all_correct = True
                _log(f"[QUEUE] 全部答对，准备自动推送下一个任务")

            _save_learning_queue(q2)
            _log(f"[QUEUE] 批改完成: {task['task_id']}")

            # ── 批改完成，清理活跃任务标记 ──
            q2["active_task_id"] = None
            q2["mode"] = "idle"
            _save_learning_queue(q2)

            # ── 全对时自动推送下一个任务 ──
            if all_correct:
                _log(f"[QUEUE] 全对自动推送下一个任务")
                q3 = _load_learning_queue()
                next_task = _get_next_pending_task(q3)
                if next_task:
                    q3["active_task_id"] = next_task["task_id"]
                    q3["mode"] = "answering"
                    next_task["status"] = "in_progress"
                    _save_learning_queue(q3)
                    _IMAGE_CACHE_META.pop(next_task["task_id"], None)
                    _push_task_to_feishu(next_task, reply_target)
                    _log(f"[QUEUE] 自动推送: {next_task['task_id']}")
                else:
                    _save_learning_queue(q3)
                    send_feishu(receive_id=reply_target, msg_type="text",
                               content="🐱 所有任务都完成啦！今天表现太棒了！🎉")

            # ── 自动生成基础复习任务 ──
            if need_review and review_topic:
                _log(f"[ADAPTIVE] 生成基础复习任务: {review_topic}")
                review_session = init_new_session()
                review_test_id = _gen_test_id('R')
                review_file = str(DATA_DIR / "questions" / f"review_{review_test_id}.json")
                review_result = run(
                    f"请为学生生成一个基础概念复习任务。\n\n"
                    f"═══════════════════════════════════════\n"
                    f"第零步：生成编号\n"
                    f"═══════════════════════════════════════\n"
                    f"test_id = \"{review_test_id}\"\n"
                    f"每道题用 _gen_question_id() 生成全局唯一编号\n\n"
                    f"═══════════════════════════════════════\n"
                    f"复习主题：{review_topic}\n"
                    f"═══════════════════════════════════════\n\n"
                    f"⚠️ 学生在基础题上犯了错误。请执行：\n"
                    f"1. 先阅读 data/mastery.json，找到 score<50 的知识点\n"
                    f"2. 用最简单易懂的方式讲解该基础概念（用生活例子，像老师在讲新课一样）\n"
                    f"3. 出 3 道基础级别的练习题（比原来的题目更简单）\n"
                    f"4. 每道题给出详细解题步骤\n\n"
                    f"═══════════════════════════════════════\n"
                    f"存储\n"
                    f"═══════════════════════════════════════\n"
                    f"用 write_file 存入 {review_file}\n"
                    f"格式：{{\"test_id\":\"{review_test_id}\",\"date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"type\":\"review\",\"topic\":\"{review_topic}\","
                    f"\"introduction\":\"概念讲解内容\",\"questions\":[{{id,question,answer,hint}}]}}\n\n"
                    "⚠️ 注意：只存储，不要调用 send_feishu 推送！系统会自动从学习队列推送。",
                    review_session,
                )
                # 插入复习任务到队列下一个位置
                q3 = _load_learning_queue()
                pending_idx = None
                for i, t in enumerate(q3.get("queue", [])):
                    if t.get("status") in ("pending", "in_progress", "image_received"):
                        pending_idx = i
                        break
                review_task = {
                    "task_id": review_test_id,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "type": "review",
                    "title": f"🔄 {review_topic}·基础复习",
                    "status": "pending",
                    "priority": 0,  # 最高优先级，插到最前面
                    "questions_file": review_file,
                    "difficulty": "basic",
                    "image_paths": [],
                    "submitted_at": None,
                    "graded_at": None,
                }
                # 去重：不插入已存在的任务ID
                existing_ids = {t["task_id"] for t in q3.get("queue", [])}
                if review_test_id not in existing_ids:
                    if pending_idx is not None:
                        q3["queue"].insert(pending_idx, review_task)
                    else:
                        q3.setdefault("queue", []).append(review_task)
                _save_learning_queue(q3)
                _log(f"[ADAPTIVE] 基础复习任务 {review_test_id} 已插入队列")
                send_feishu(receive_id=reply_target, msg_type="text",
                           content=f"🐱 我注意到有些基础概念还不太熟，已经帮你插入了「{review_topic}·基础复习」任务。\n回复「继续」先复习巩固一下，再往下做～")

        except Exception as e:
            _log(f"[QUEUE] 批改异常: {e}")
            send_feishu(receive_id=reply_target, msg_type="text",
                       content=f"🐱 批改时出错了：{e}")

    threading.Thread(target=_grade_task, daemon=True).start()
    if is_auto:
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"🐱 我看你有一会儿没继续发图片啦，已经先帮你自动提交批改了。\n如果还有漏拍的题，等这项批改完可以再告诉我～")
    else:
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"🐱 收到 {task['task_id']} 的 {len(task['image_paths'])} 张答案图片，正在批改中...")
    return True

def _grade_task_recheck(sender_id: str, chat_id: str, reply_target: str, task: dict):
    """重新批改：使用更严格的OCR校验再次批改。"""
    try:
        session = _get_or_create_session(sender_id, chat_id)
        qf = Path(task["questions_file"])
        if not qf.exists():
            send_feishu(receive_id=reply_target, msg_type="text", content="🐱 题目文件丢失了...")
            return
        data = json.loads(qf.read_text(encoding="utf-8"))
        qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
        qs_summary = "\n".join([f"  {q['id']}: {q['question'][:100]}... | 答案:{q.get('answer','')}" for q in qs])

        result = run(
            f"[系统上下文：飞书用户 sender_open_id={sender_id}, 回复目标ID={reply_target}]\n"
            f"⚠️ 这是重新批改请求！学生认为之前的批改结果有误（可能是OCR识别错误）。\n"
            f"请重新检查之前的OCR识别结果，特别注意数字识别错误。\n\n"
            f"试卷题目和标准答案：\n{qs_summary}\n\n"
            f"请执行：\n"
            f"1. 检查会话历史中的OCR识别结果\n"
            f"2. ⚠️ 重点核查数字：7→1, 2→7, 8→6, 5→3 等常见手写混淆\n"
            f"3. 对比标准答案格式，判断OCR结果是否合理\n"
            f"4. 逐题重新批改（✅/❌ + 解析）\n"
            f"5. 更新mastery/error_book\n"
            f"6. 用 send_feishu(receive_id=\"{reply_target}\") 发送重新批改结果\n"
            f"⚠️ 必须调用send_feishu！",
            session,
        )
        _log(f"[RECHECK] 重新批改完成: {task['task_id']}")
    except Exception as e:
        _log(f"[RECHECK] 重新批改异常: {e}")
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"🐱 重新批改时出错了：{e}")


def _organize_error_upload(sender_id: str, chat_id: str, reply_target: str, is_auto: bool = False) -> bool:
    """整理错题上传图片。is_auto=True 表示自动整理。"""
    q = _load_learning_queue()
    if not q.get("error_upload_mode", {}).get("active"):
        if not is_auto:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 当前不在错题整理模式哦～请先回复「上传错题」")
        return True

    # 检查是否已自动整理过
    meta = _IMAGE_CACHE_META.get("__error_upload__", {})
    if is_auto and meta.get("auto_submitted"):
        return True

    img_paths = q["error_upload_mode"].get("image_paths", [])
    if not img_paths:
        if not is_auto:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 还没有收到错题图片哦～请先发送图片")
        return True

    q["error_upload_mode"]["active"] = False
    q["mode"] = "idle"
    _save_learning_queue(q)
    _IMAGE_CACHE_META.pop("__error_upload__", None)

    # 异步处理错题
    def _process_errors():
        try:
            session = _get_or_create_session(sender_id, chat_id)
            all_ocr = []
            for img_path in img_paths:
                if not Path(img_path).exists():
                    continue
                try:
                    ocr_result = json.loads(enhanced_ocr_image(img_path))
                    if ocr_result.get("success"):
                        all_ocr.append(ocr_result.get("text", ""))
                except Exception:
                    pass
                try:
                    Path(img_path).unlink(missing_ok=True)
                except Exception:
                    pass

            ocr_text = "\n---\n".join(all_ocr)
            auto_hint = "\n⚠️ 这是系统自动整理的（家长超时未手动整理）。\n" if is_auto else ""
            result = run(
                f"[系统上下文：飞书用户 sender_open_id={sender_id}, 回复目标ID={reply_target}]\n"
                f"家长上传了学校错题图片，OCR识别结果：\n\n{ocr_text}\n\n"
                f"{auto_hint}"
                f"请执行错题分析流程（详见root.md第5节）：\n"
                f"1. 提取每道错题的原题、错误答案、正确答案\n"
                f"2. 分类错误类型（计算粗心/概念不清/审题偏差/方法错误）\n"
                f"3. 分析根源原因\n"
                f"4. 生成同类变式题\n"
                f"5. 用 append_error_book 存入 error_book.json（不要用write_file！会覆盖已有数据！）\n"
                f"6. 用 send_feishu(receive_id=\"{reply_target}\") 发送分析结果\n"
                f"⚠️ 必须调用send_feishu！",
                session,
            )
            _log(f"[QUEUE] 错题整理完成")
        except Exception as e:
            _log(f"[QUEUE] 错题整理异常: {e}")

    threading.Thread(target=_process_errors, daemon=True).start()
    if is_auto:
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"📝 已自动开始整理刚才上传的错题图片。\n如果还有遗漏图片，可以稍后再次发送「上传错题」继续补充。")
    else:
        send_feishu(receive_id=reply_target, msg_type="text",
                   content=f"🐱 收到 {len(img_paths)} 张错题图片，正在分析中...")
    return True

def _handle_queue_command(text: str, sender_id: str, chat_id: str, reply_target: str) -> bool:
    """处理学习队列相关指令。返回True表示已拦截处理。"""
    text_clean = text.strip().replace(" ", "").replace("\u3000", "")  # 去空格，兼容"出今日 任务"
    
    # ── 开始学习 ──
    if text_clean in ("开始学习", "开始"):
        q = _migrate_old_tasks_to_queue()
        task = _get_next_pending_task(q)
        if not task:
            # 队列为空，自动触发每日出题
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 当前没有待完成的学习任务，正在为你生成今日任务，请稍等...")
            threading.Thread(target=scheduled_daily_push, daemon=True).start()
            return True
        
        # 设置当前活跃任务
        q["active_task_id"] = task["task_id"]
        q["mode"] = "answering"
        task["status"] = "in_progress"
        _save_learning_queue(q)
        
        # 重置自动提交标记
        _IMAGE_CACHE_META.pop(task["task_id"], None)
        
        # 推送任务
        _push_task_to_feishu(task, reply_target)
        _log(f"[QUEUE] 开始学习: active={task['task_id']}")
        return True
    
    # ── 提交 ──
    if text_clean == "提交":
        return _submit_active_task(sender_id, chat_id, reply_target, is_auto=False)
    
    # ── 继续 ──
    if text_clean == "继续":
        q = _load_learning_queue()
        active_id = q.get("active_task_id")
        if active_id:
            for t in q.get("queue", []):
                if t["task_id"] == active_id:
                    st = t.get("status", "")
                    if st == "submitted":
                        # 检查是否超时（超过2分钟还没批改完，可能线程异常）
                        submitted_at = t.get("submitted_at", "")
                        if submitted_at:
                            try:
                                elapsed = (datetime.now() - datetime.fromisoformat(submitted_at)).total_seconds()
                                if elapsed > 120:
                                    _log(f"[QUEUE] 批改超时 {elapsed}s，强制标记为已批改")
                                    t["status"] = "graded"
                                    _save_learning_queue(q)
                                    break  # 继续往下走，推下一个任务
                            except:
                                pass
                        send_feishu(receive_id=reply_target, msg_type="text",
                                   content="🐱 正在批改中，请稍等几秒...")
                        return True
                    if st == "in_progress":
                        send_feishu(receive_id=reply_target, msg_type="text",
                                   content=f"🐱 当前任务 {active_id} 正在进行中，请完成后说「提交」")
                        return True
                    if st == "graded":
                        # 已批改完成，清理状态继续推下一个任务
                        q["active_task_id"] = None
                        q["mode"] = "idle"
                        _save_learning_queue(q)
                    break
        
        # 找下一个待处理任务
        task = _get_next_pending_task(q)
        if not task:
            q["active_task_id"] = None
            q["mode"] = "idle"
            _save_learning_queue(q)
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 所有任务都完成啦！今天表现太棒了！🎉")
            return True
        
        q["active_task_id"] = task["task_id"]
        q["mode"] = "answering"
        task["status"] = "in_progress"
        _save_learning_queue(q)
        _IMAGE_CACHE_META.pop(task["task_id"], None)
        _push_task_to_feishu(task, reply_target)
        return True
    
    # ── 任务清单 ──
    if text_clean in ("任务清单", "今日任务", "进度", "今日进度"):
        q = _migrate_old_tasks_to_queue()
        if not q.get("queue"):
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 当前没有学习任务记录。")
            return True
        
        emoji_map = {"calc": "🔢", "math": "📐", "geometry": "📏", "vocab": "📚", "grammar": "📖", "writing": "✏️", "english": "📝", "error_review": "🔄", "review": "💡"}
        status_map = {"pending": "❌ 未开始", "in_progress": "⏳ 进行中", "image_received": "📷 已收到图片",
                      "submitted": "⏳ 待批改", "graded": "✅ 已完成", "skipped": "⏭️ 已跳过"}
        
        lines = ["🐱 当前学习队列", ""]
        active_id = q.get("active_task_id", "")
        for t in q["queue"]:
            emoji = emoji_map.get(t.get("type", ""), "📋")
            status = status_map.get(t.get("status", ""), "❓")
            marker = " 👈 当前" if t["task_id"] == active_id else ""
            lines.append(f"{emoji} {t['title']} {t['task_id']}：{status}{marker}")
        
        pending_count = sum(1 for t in q["queue"] if t.get("status") in ("pending", "in_progress", "image_received", "submitted"))
        if pending_count > 0:
            lines.append("")
            lines.append(f"还有 {pending_count} 项任务待完成。")
            if q.get("mode") == "answering" and active_id:
                lines.append(f"当前正在做：{active_id}，完成后回复「提交」")
            else:
                lines.append("回复「开始学习」继续～")
        
        send_feishu(receive_id=reply_target, msg_type="text", content="\n".join(lines))
        return True
    
    # ── 上传错题 ──
    if text_clean in ("上传错题", "学校错题"):
        q = _load_learning_queue()
        q["error_upload_mode"] = {"active": True, "image_paths": []}
        q["mode"] = "error_upload"
        _save_learning_queue(q)
        _IMAGE_CACHE_META.pop("__error_upload__", None)
        send_feishu(receive_id=reply_target, msg_type="text",
                   content="📝 已进入学校错题整理模式\n\n请连续发送学校错题图片。\n发完后回复「整理」开始分析。")
        return True
    
    # ── 整理（错题）──
    if text_clean == "整理":
        return _organize_error_upload(sender_id, chat_id, reply_target, is_auto=False)
    
    # ── 重新检查（重新批改当前任务）──
    if text_clean in ("重新检查", "重新批改"):
        q = _load_learning_queue()
        active_id = q.get("active_task_id")
        if not active_id:
            send_feishu(receive_id=reply_target, msg_type="text",
                       content="🐱 当前没有正在进行的任务哦～")
            return True
        # 找到当前任务，重新提交批改
        for t in q.get("queue", []):
            if t["task_id"] == active_id and t.get("status") == "graded":
                t["status"] = "submitted"
                _save_learning_queue(q)
                send_feishu(receive_id=reply_target, msg_type="text",
                           content=f"🐱 正在重新批改 {active_id}，请稍等...")
                # 重新触发批改（复用已有图片路径）
                threading.Thread(target=lambda: _grade_task_recheck(sender_id, chat_id, reply_target, t), daemon=True).start()
                return True
        send_feishu(receive_id=reply_target, msg_type="text",
                   content="🐱 当前任务还没批改完，或者还没有提交哦～")
        return True

    # ── 手动触发今日学习任务 ──
    if text_clean in ("今日学习", "出今日任务", "生成今日任务"):
        send_feishu(receive_id=reply_target, msg_type="text",
                   content="🐱 正在生成今日学习任务，请稍等...")
        threading.Thread(target=scheduled_daily_push, daemon=True).start()
        return True
    
    return False

# ── 试卷编号生成（跨重启持久化）──
_TEST_ID_COUNTER_FILE = DATA_DIR / ".test_id_counter"

def _gen_test_id(prefix="T"):
    """生成唯一试卷编号，如 T0609A、V0609B。跨服务器重启不重复。"""
    today = datetime.now().strftime("%m%d")
    key = f"{prefix}{today}"
    # 从持久化文件读取计数器
    counter = {}
    if _TEST_ID_COUNTER_FILE.exists():
        try:
            counter = json.loads(_TEST_ID_COUNTER_FILE.read_text())
        except:
            counter = {}
    count = counter.get(key, 0)
    counter[key] = count + 1
    _TEST_ID_COUNTER_FILE.write_text(json.dumps(counter))
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
    - 学习队列中有未完成任务 → 不推送（等孩子做完）
    - 文件不存在 → 允许推送
    - 文件日期 = 今天 → 今天已推送，不重复
    - 文件日期 = 昨天且未完成 → 前一天未批改，不出后一天
    - 文件日期更早 → 旧题作废，允许推送
    返回 (can_push: bool, reason: str)
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    # ── 检查学习队列：有未完成任务就不出新题 ──
    q = _load_learning_queue()
    pending = [t for t in q.get("queue", []) if t.get("status") in ("pending", "in_progress", "image_received", "submitted")]
    if pending:
        return False, f"学习队列中还有 {len(pending)} 项任务未完成，先完成再出新题"

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
    
    # ⚠️ 飞书群聊消息默认是 post（富文本）类型，text 字段为空
    # post 格式: {"title":"...","content":[[{"tag":"text","text":"..."},...]]}
    if not text and msg_type == "post":
        post_content = content.get("content", [])
        if isinstance(post_content, list):
            text_parts = []
            for paragraph in post_content:
                if isinstance(paragraph, list):
                    for elem in paragraph:
                        if isinstance(elem, dict) and elem.get("tag") == "text":
                            text_parts.append(elem.get("text", ""))
            text = "".join(text_parts).strip()
        # 也尝试 title
        if not text:
            text = content.get("title", "").strip()
    
    if not text and msg_type != "image":
        return

    # 回复目标：群聊消息回群聊，私聊消息回私聊
    reply_target = chat_id if chat_id else sender_id
    target_desc = f"回复目标ID={reply_target}（群聊→发到群，私聊→发到用户）"

    print(f"[INFO] 飞书消息: sender={sender_id[:12]}... type={msg_type} text={text[:80]} reply_to={reply_target[:16]}...")

    # ── 直接拦截"出今日任务"（最高优先级，不依赖队列指令匹配）──
    text_nospace = text.replace(" ", "").replace("\u3000", "") if text else ""
    if text_nospace in ("出今日任务", "生成今日任务"):
        _log(f"[DIRECT] 直接拦截出今日任务: text={text[:60]}")
        send_feishu(receive_id=reply_target, msg_type="text",
                   content="🐱 正在生成今日学习任务，请稍等...")
        threading.Thread(target=scheduled_daily_push, daemon=True).start()
        return

    # ── 学习队列指令拦截 ──
    if text and _handle_queue_command(text, sender_id, chat_id, reply_target):
        _log(f"[QUEUE] 队列指令已处理: {text[:60]}")
        return

    # ── 图片缓存：如果当前处于学习队列模式，图片自动绑定active_task ──
    if msg_type == "image":
        q = _load_learning_queue()
        active_id = q.get("active_task_id")
        error_upload = q.get("error_upload_mode", {})
        
        # 错题上传模式
        if error_upload.get("active"):
            image_key = content.get("image_key", "")
            if image_key:
                local_path = download_feishu_image(message_id, image_key)
                if local_path:
                    error_upload.setdefault("image_paths", []).append(local_path)
                    now = time.time()
                    meta = _IMAGE_CACHE_META.setdefault("__error_upload__", {"first_image_at": now, "last_image_at": now, "auto_submitted": False})
                    meta["last_image_at"] = now
                    _save_learning_queue(q)
                    send_feishu(receive_id=reply_target, msg_type="text",
                               content=f"📝 收到第 {len(error_upload['image_paths'])} 张错题图片。\n发完后回复「整理」开始分析。")
            return
        
        # 学习队列模式：图片绑定当前任务
        if active_id and q.get("mode") == "answering":
            image_key = content.get("image_key", "")
            if image_key:
                local_path = download_feishu_image(message_id, image_key)
                if local_path:
                    cache = _IMAGE_CACHE.setdefault(active_id, [])
                    cache.append({"path": local_path, "time": time.time()})
                    now = time.time()
                    meta = _IMAGE_CACHE_META.setdefault(active_id, {"first_image_at": now, "last_image_at": now, "auto_submitted": False})
                    meta["last_image_at"] = now
                    send_feishu(receive_id=reply_target, msg_type="text",
                               content=f"🐱 收到 {active_id} 的第 {len(cache)} 张答案图片。\n如果还有图片，继续发。\n发完请回复「提交」开始批改。")
            return

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
    
    # ⚠️ 排除"假答案"：消息含"答案"但实际是要求重新批改图片（非提交答案）
    # 如："V0616A的答案前面也已经给过图片了，你再看一下"
    _recheck_kw = ["再看", "重新", "前面", "已经给过", "图片"]
    _is_recheck_only = has_answer and test_id_in_text and any(kw in text for kw in _recheck_kw)
    # 检查是否真的包含答案内容（排除试卷编号后的数字、字母选项、Q编号等）
    _text_no_testid = text.replace(test_id_in_text, "") if test_id_in_text else text
    _has_real_answer_content = bool(re.search(r'(?<!\d)\d{2,}(?!\d)|[A-Da-d]\b|Q\d{6}', _text_no_testid))
    if _is_recheck_only and not _has_real_answer_content:
        _log(f"[DETECT] 假答案检测: has_answer降级, text={text[:60]}, test_id={test_id_in_text}")
        has_answer = False  # 降级为非答案消息，走正常意图识别
    
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
    # ⚠️ 排除"再看一下图片"类消息：有试卷编号+题目已注入，但无实际答案内容
    if _is_recheck_only and not _has_real_answer_content:
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
        # ⚠️ 图片处理异步执行，不阻塞轮询
        def _process_image_async(sender_id, chat_id, message_id, image_key, reply_target, context_prompt, session):
            try:
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
                _log(f"[INFO] 图片处理完成: {result[:100] if result else 'None'}")
            except Exception as e:
                _log(f"[ERROR] 图片处理异常: {e}")
        
        threading.Thread(target=_process_image_async, args=(sender_id, chat_id, message_id, image_key, reply_target, context_prompt, session), daemon=True).start()
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
            # "出今日任务"走学习队列模式
            text_nospace = text.replace(" ", "").replace("\u3000", "")
            if text_nospace in ("出今日任务", "生成今日任务"):
                _log(f"[PRE-CHECK] 检测到出今日任务，走学习队列")
                send_feishu(receive_id=reply_target, msg_type="text",
                           content="🐱 正在生成今日学习任务，请稍等...")
                threading.Thread(target=scheduled_daily_push, daemon=True).start()
                return
            
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
        # ⚠️ 复用早期检测的 test_id_in_text（已用正确正则匹配），不用 \b（Python3中中文是单词字符）
        if test_id_in_text:
            test_id = test_id_in_text
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
            
            _log(f"[DEBUG] test_id={test_id}, found_in_archives={test_id in all_tests}, all_keys={list(all_tests.keys())[:10]}")
            
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
                
                # ── recheck场景：试卷找到 + 要求重新批改图片 → 直接处理，跳过LLM意图识别 ──
                if _is_recheck_only:
                    _log(f"[RECHECK] 试卷{test_id}已找到，recheck场景直接处理")
                    result = run(
                        f"{context_prompt}\n"
                        f"学生说：{text}\n\n"
                        f"⚠️ 学生之前发过图片答案（手写），现在要求重新批改试卷 {test_id}。\n"
                        f"上下文已注入试卷 {test_id} 的题目和标准答案。\n"
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
                    _log(f"[RECHECK] 处理完成: {result[:200] if result else 'None'}")
                    return
            else:
                _log(f"[WARN] 试卷{test_id}未找到，可用: {list(all_tests.keys())[:10]}")
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
- geometry_practice: 几何图形强化练习（含"几何强化"/"图形强化"/"错题强化 图形"/"根据几何错题"等）
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
                f"6. ⚠️ 错题用 append_error_book 工具存入error_book.json（不要用write_file！会覆盖已有数据！），必须包含试卷编号字段：\n"
                f"   {{\"error_id\":\"E{datetime.now().strftime('%m%d')}01\",\"test_id\":\"原试卷编号\",\"date\":\"{datetime.now().strftime('%Y-%m-%d')}\",\"question\":\"...\",\"student_answer\":\"...\",\"correct_answer\":\"...\",\"error_type\":\"...\",\"reviewed_date\":null}}\n"
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
        elif intent == "geometry_practice":
            result = run(
                f"{context_prompt}\n"
                f"学生请求几何图形强化练习：{text}\n\n"
                f"请执行几何强化出题流程（详见root.md「几何强化练习」）：\n"
                f"1. 读 error_book.json → 筛选几何错题（topic_id含math-2图形运动/math-3周长）\n"
                f"2. 读 mastery.json → 确认几何知识点掌握度\n"
                f"3. 生成唯一编号：test_id = \"{_gen_test_id('G')}\"\n"
                f"4. 对每个薄弱几何知识点，用 call_llm 生成「拓展」难度变式题（3-5题）\n"
                f"5. ⚠️ 每道题必须用 draw_geometry 绘制配套图形\n"
                f"6. 用 send_feishu(msg_type=\"image\") 先发图形\n"
                f"7. 用 send_feishu(msg_type=\"text\") 再发题目文字\n"
                f"8. write_file 存入 today_questions.json（含test_id）\n"
                f"9. 如果无几何错题→从mastery找score<70的几何知识点\n"
                f"10. 如果也没有薄弱几何知识点→send_feishu告知'当前几何掌握不错🐱'\n"
                f"⚠️ 不是简单重复错题，是更高难度的变式题！每道题必须配图！",
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


_SCHEDULED_PUSH_LOCK = threading.Lock()

def scheduled_daily_push():
    """定时每日推送：从 config.toml [[tasks]] 读取任务配置，逐个生成，全部入队列，只推送第一个。"""
    if not _SCHEDULED_PUSH_LOCK.acquire(blocking=False):
        _log("[SCHEDULER] ⛔ 已有推送任务正在执行，跳过重复调用")
        return
    try:
        _log(f"[SCHEDULER] ====== 开始执行每日推送 ======")
        _log(f"[SCHEDULER] 时间: {datetime.now()}")

        can_push, reason = _can_scheduled_push_today()
        _log(f"[SCHEDULER] 推送前置检查: can_push={can_push}, reason={reason}")
        if not can_push:
            _log(f"[SCHEDULER] ⛔ 跳过推送: {reason}")
            return

        poll_cfg = CFG.get("feishu", {}).get("poll", {})
        if not poll_cfg.get("chat_ids"):
            _log("[SCHEDULER] 未配置推送目标 chat_ids，跳过")
            return

        today_str = datetime.now().strftime('%Y-%m-%d')
        tasks_cfg = CFG.get("tasks", [])
        if not tasks_cfg:
            _log("[SCHEDULER] ⛔ config.toml 中未配置 [[tasks]]，跳过")
            return

        # 临时从工具列表中移除 send_feishu，LLM 根本调不到
        _saved_send_feishu = _core_mod.TOOLS.pop("send_feishu", None)
        _saved_schema = None
        for i, s in enumerate(_core_mod.TOOL_SCHEMAS):
            if s.get("function", {}).get("name") == "send_feishu":
                _saved_schema = _core_mod.TOOL_SCHEMAS.pop(i)
                break

        # 生成所有 test_id
        test_ids = {}
        for task_cfg in sorted(tasks_cfg, key=lambda t: t.get("priority", 99)):
            prefix = task_cfg.get("prefix", "X")
            test_ids[task_cfg["type"]] = _gen_test_id(prefix)

        # 一次 LLM 调用生成所有任务
        combined_prompt = (HOME / "prompts" / "daily_all.md").read_text(encoding="utf-8")
        prompt = combined_prompt.format(
            today_str=today_str,
            test_id_c=test_ids.get("calc", ""),
            test_id_v=test_ids.get("vocab", ""),
            test_id_g=test_ids.get("grammar", ""),
            test_id_p=test_ids.get("writing", ""),
            test_id_m=test_ids.get("math", ""),
            test_id_geo=test_ids.get("geometry", ""),
        )
        _log(f"[SCHEDULER] 开始一次性生成全部 {len(tasks_cfg)} 个任务...")
        session = init_new_session()
        run(prompt, session)
        _log(f"[SCHEDULER] LLM 出题完成")

        # 全部加入队列
        for task_cfg in sorted(tasks_cfg, key=lambda t: t.get("priority", 99)):
            ttype = task_cfg["type"]
            test_id = test_ids.get(ttype, "")
            output_file = task_cfg.get("output_file", "").replace("{today_str}", today_str)
            _add_task_to_queue(test_id, today_str, ttype, str(DATA_DIR / output_file.replace("data/", "")))

        # 恢复 send_feishu 工具
        if _saved_send_feishu:
            _core_mod.TOOLS["send_feishu"] = _saved_send_feishu
        if _saved_schema:
            _core_mod.TOOL_SCHEMAS.append(_saved_schema)

        _push_first_pending_to_all()
        _log(f"[SCHEDULER] ====== 每日推送完成，共 {len(tasks_cfg)} 个任务 ======")
    except Exception as e:
        if _saved_send_feishu:
            _core_mod.TOOLS["send_feishu"] = _saved_send_feishu
        if _saved_schema:
            _core_mod.TOOL_SCHEMAS.append(_saved_schema)
        _log(f"[SCHEDULER] ====== 每日推送失败: {e} ======")
        _log(traceback.format_exc())
    finally:
        _SCHEDULED_PUSH_LOCK.release()


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


# scheduled_daily_vocab 和 scheduled_daily_calc 已合并到 scheduled_daily_push（v3.6）

def scheduled_auto_submit_uploads():
    """自动提交长时间未结束的图片上传任务。
    - 学习任务图片缓存超过5分钟未更新 → 自动提交
    - 错题上传图片超过10分钟未更新 → 自动整理
    """
    now = time.time()
    q = _load_learning_queue()

    # ── 检查学习任务图片缓存 ──
    active_id = q.get("active_task_id")
    if active_id and q.get("mode") == "answering":
        meta = _IMAGE_CACHE_META.get(active_id, {})
        if meta and not meta.get("auto_submitted"):
            last_at = meta.get("last_image_at", 0)
            if last_at > 0 and (now - last_at) > _AUTO_SUBMIT_TIMEOUT_LEARNING:
                # 检查是否有图片缓存
                cached = _IMAGE_CACHE.get(active_id, [])
                if cached:
                    _log(f"[AUTO-SUBMIT] 学习任务 {active_id} 超时 {int(now - last_at)}s，自动提交")
                    meta["auto_submitted"] = True
                    _IMAGE_CACHE_META[active_id] = meta
                    # 需要知道 reply_target，从队列配置中获取
                    poll_cfg = CFG.get("feishu", {}).get("poll", {})
                    chat_ids = poll_cfg.get("chat_ids", [])
                    if chat_ids:
                        _submit_active_task("auto", chat_ids[0], chat_ids[0], is_auto=True)

    # ── 检查错题上传图片缓存 ──
    error_upload = q.get("error_upload_mode", {})
    if error_upload.get("active"):
        meta = _IMAGE_CACHE_META.get("__error_upload__", {})
        if meta and not meta.get("auto_submitted"):
            last_at = meta.get("last_image_at", 0)
            if last_at > 0 and (now - last_at) > _AUTO_SUBMIT_TIMEOUT_ERROR:
                img_paths = error_upload.get("image_paths", [])
                if img_paths:
                    _log(f"[AUTO-SUBMIT] 错题上传超时 {int(now - last_at)}s，自动整理")
                    meta["auto_submitted"] = True
                    _IMAGE_CACHE_META["__error_upload__"] = meta
                    poll_cfg = CFG.get("feishu", {}).get("poll", {})
                    chat_ids = poll_cfg.get("chat_ids", [])
                    if chat_ids:
                        _organize_error_upload("auto", chat_ids[0], chat_ids[0], is_auto=True)


def scheduled_image_cleanup():
    """每日清理超过1天的图片，释放服务器空间。"""
    _log(f"[CLEANUP] 执行图片清理: {datetime.now()}")
    images_dir = DATA_DIR / "images"
    if not images_dir.exists():
        return
    
    cutoff = time.time() - 86400  # 1天前
    deleted = 0
    freed = 0
    for f in images_dir.iterdir():
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif"):
            try:
                mtime = f.stat().st_mtime
                if mtime < cutoff:
                    size = f.stat().st_size
                    f.unlink()
                    deleted += 1
                    freed += size
            except Exception:
                pass
    
    if deleted > 0:
        _log(f"[CLEANUP] 已清理 {deleted} 张图片，释放 {freed / 1024 / 1024:.1f}MB")
    else:
        _log(f"[CLEANUP] 无需清理的图片")


def start_scheduler():
    push_time = CFG.get("education", {}).get("push_time", "09:00")
    hour, minute = map(int, push_time.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(scheduled_daily_push, 'cron', hour=hour, minute=minute, id='daily_push')
    
    # 每周日综合测试
    scheduler.add_job(scheduled_weekly_test, 'cron', day_of_week='sun', hour=hour, minute=minute, id='weekly_test')
    
    # 每日凌晨3点清理超过1天的图片
    scheduler.add_job(scheduled_image_cleanup, 'cron', hour=3, minute=0, id='image_cleanup')
    
    # 自动提交扫描：每60秒检查一次超时未提交的图片上传
    scheduler.add_job(scheduled_auto_submit_uploads, 'interval', seconds=_AUTO_SUBMIT_SCAN_INTERVAL, id='auto_submit_scan')
    
    # 飞书消息轮询
    poll_cfg = CFG.get("feishu", {}).get("poll", {})
    if poll_cfg.get("enabled") and poll_cfg.get("chat_ids"):
        interval = poll_cfg.get("interval_seconds", 10)
        scheduler.add_job(poll_feishu_messages, 'interval', seconds=interval, id='feishu_poll')
        print(f"[POLL] 飞书消息轮询已启动，间隔 {interval}s，监控 {len(poll_cfg['chat_ids'])} 个聊天")
    
    scheduler.start()
    print(f"[SCHEDULER] 已启动，每日 {push_time} 推送（含综合+词汇+计算）+ 周日综合测试 + 图片清理")


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
