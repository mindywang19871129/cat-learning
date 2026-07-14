"""
小肥猫学习助手 — LLM原生内核
======================================================================
架构原则：工具极薄，智能全在LLM（由 root.md 系统提示词驱动）。

工具集（10个）：
  通用：read_file, write_file, edit_file, list_dir, bash, ask_user, call_llm, web_search
  专用：ocr_image, send_feishu

Agent Loop 负责：
  - 自主读取数据文件（mastery.json, error_book.json, adjustments.json 等）
  - 自主调用 call_llm 生成题目/批改/模考
  - 自主写入数据、更新掌握度、管理错题本
  - 自主构建飞书消息并发送
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from openai import OpenAI

# ─── 路径常量 ────────────────────────────────────────────────────────

HOME = Path(os.environ.get("CATLEARN_HOME", Path(__file__).parent.resolve()))


# ─── .env 解析 ───────────────────────────────────────────────────────

def _load_dotenv():
    env_file = HOME / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

# ─── 配置加载 ────────────────────────────────────────────────────────

with open(HOME / "config.toml", "rb") as _f:
    CFG = tomllib.load(_f)

MODEL = CFG["api"]["model"]
REASONING_MODEL = CFG["api"].get("reasoning_model", MODEL)
BASE_URL = CFG["api"]["base_url"]
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
if not API_KEY:
    sys.stderr.write("ERROR: DEEPSEEK_API_KEY not set (check .env)\n")
    sys.exit(1)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

WORKSPACE_DIR = HOME / CFG["paths"]["workspace"]
SESSIONS_DIR = HOME / CFG["paths"]["sessions"]
DATA_DIR = HOME / CFG["paths"]["data"]

MAX_ITERATIONS = CFG["runtime"]["max_iterations"]
MAX_TOOL_OUTPUT = CFG["runtime"]["max_tool_output"]

for _d in (WORKSPACE_DIR, SESSIONS_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

CLIENT = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# ─── 中断标志 ────────────────────────────────────────────────────────

INTERRUPTED = threading.Event()


# ─── 路径解析 ────────────────────────────────────────────────────────

def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else HOME / p


# ══════════════════════════════════════════════════════════════════════
# 工具 1-4：文件 I/O
# ══════════════════════════════════════════════════════════════════════

def read_file(path: str) -> str:
    """读取文件，返回带行号的文本。"""
    p = _resolve(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    if p.is_dir():
        return f"ERROR: path is a directory: {path}"
    try:
        text = p.read_text(errors="replace")
    except Exception as e:
        return f"ERROR: could not read {path}: {e}"
    lines = text.splitlines()
    truncated = False
    if len(lines) > 4000:
        lines = lines[:4000]
        truncated = True
    out = "\n".join(f"{i+1:>5}\t{line}" for i, line in enumerate(lines))
    if truncated:
        out += "\n[... truncated]"
    return out


def write_file(path: str, content: str) -> str:
    """写入/覆盖文件（自动创建目录）。"""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"OK: wrote {len(content.encode('utf-8'))} bytes to {path}"


def edit_file(path: str, old: str, new: str) -> str:
    """在文件中精准替换文本。old 必须在文件中唯一出现一次。"""
    p = _resolve(path)
    if not p.exists():
        return f"ERROR: file not found: {path}"
    content = p.read_text(encoding="utf-8")
    count = content.count(old)
    if count == 0:
        return f"ERROR: 'old' string not found in {path}"
    if count > 1:
        return f"ERROR: 'old' appears {count} times, must be unique"
    p.write_text(content.replace(old, new, 1), encoding="utf-8")
    return f"OK: edited {path}"


def list_dir(path: str = ".") -> str:
    """列出目录中所有文件和子目录。"""
    p = _resolve(path)
    if not p.exists():
        return f"ERROR: path not found: {path}"
    if not p.is_dir():
        return f"ERROR: not a directory: {path}"
    items = []
    for item in sorted(p.iterdir()):
        marker = "/" if item.is_dir() else ""
        items.append(f"{item.name}{marker}")
    return "\n".join(items) if items else "(empty)"


# ─── 工具 5：Shell ──────────────────────────────────────────────────

def bash(cmd: str, timeout: int = 30) -> str:
    """执行 Shell 命令，返回 stdout+stderr。"""
    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return f"ERROR: command timed out after {timeout}s"
    rc = proc.returncode
    prefix = "" if rc == 0 else f"[exit {rc}]\n"
    return prefix + (out or "(no output)")


# ─── 工具 6：交互 ───────────────────────────────────────────────────

def ask_user(question: str, _on_ask_user=None) -> str:
    """向用户提问，等待回复。"""
    if _on_ask_user:
        return _on_ask_user(question)
    raise RuntimeError("ask_user called without callback")


# ─── 工具 7：LLM 子调用 ─────────────────────────────────────────────

def call_llm(prompt: str, system: str = "") -> str:
    """隔离的 LLM 子调用，不影响主对话历史。复杂任务用推理模型保证质量。"""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    resp = CLIENT.chat.completions.create(
        model=REASONING_MODEL, messages=msgs, stream=False,
    )
    return resp.choices[0].message.content or ""


# ─── 工具 8：联网搜索 ───────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索最新信息。优先用 LLM 联网能力，降级到 Tavily。"""
    try:
        resp = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": f"请联网搜索以下内容并给出简洁准确的答案：{query}"},
                {"role": "user", "content": f"搜索: {query}。给出搜索结果摘要和来源链接。"},
            ],
            stream=False,
        )
        result = resp.choices[0].message.content or ""
        if result and len(result) > 20:
            return f"[联网搜索]\n{result}"
    except Exception:
        pass

    if not TAVILY_API_KEY:
        return "ERROR: 联网搜索不可用（LLM不支持 + TAVILY_API_KEY未设置）"

    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "include_answer": True,
    }).encode()
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return f"ERROR: web_search failed: {e}"
    parts = []
    if data.get("answer"):
        parts.append(f"[Answer] {data['answer']}\n")
    for i, r in enumerate(data.get("results", []), 1):
        parts.append(f"[{i}] {r.get('title','')}\n    {r.get('url','')}\n    {r.get('content','')}\n")
    return "\n".join(parts) if parts else "(no results)"


# ─── 工具 9：OCR 图片识别（纯云端多引擎）────────────────────────────
# 引擎优先级：飞书OCR → OCR.space → 均失败报错
# 不依赖任何本地能力（Tesseract/Apple Vision），完全适配云端部署。

OCR_SPACE_API_KEY = os.environ.get("OCR_SPACE_API_KEY", "")


def _recognize_via_feishu(p: Path) -> dict | None:
    """飞书OCR API：JSON base64格式（官方协议）。
    返回 {"raw_lines": [...]} 或 None。
    API文档：POST /optical_char_recognition/v1/image/basic_recognize
            Body: {"image": "<base64>"}
            返回: {"code":0, "data":{"text_list":["行1","行2"]}}
    遇到频率限制(99991400)会自动重试最多5次（10s递增退避）。
    """
    import requests
    token = _get_feishu_token()
    if not token:
        return None

    try:
        with open(str(p), "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
    except Exception:
        return None

    max_retries = 2  # 减少重试次数，避免阻塞轮询
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{FEISHU_BASE}/optical_char_recognition/v1/image/basic_recognize",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json={"image": img_b64},
                timeout=30,
            )

            # 统一解析响应体
            try:
                data = resp.json()
            except Exception:
                data = {}
            code = data.get("code", -1)

            if code == 0:
                raw_lines = data.get("data", {}).get("text_list", [])
                if raw_lines:
                    return {"raw_lines": raw_lines}
                return None

            if code == 99991400 and attempt < max_retries - 1:
                wait = (attempt + 1) * 10  # 10s, 20s, 30s, 40s
                print(f"[OCR] 飞书频率限制，{wait}s后重试({attempt+1}/{max_retries})...", file=sys.stderr)
                time.sleep(wait)
                continue

            # 不可重试错误: 9499=权限不足, 1150101=参数错误 等
            return None

        except Exception:
            if attempt < max_retries - 1:
                time.sleep(5)
                continue
            return None

    return None


def _recognize_via_ocrspace(p: Path) -> dict | None:
    """OCR.space 免费API（每月25000次，支持中文手写体）。"""
    if not OCR_SPACE_API_KEY:
        return None
    import requests

    try:
        with open(str(p), "rb") as img_file:
            resp = requests.post(
                "https://api.ocr.space/parse/image",
                headers={"apikey": OCR_SPACE_API_KEY},
                files={"file": (p.name, img_file)},
                data={
                    "language": "chs",       # 简体中文
                    "OCREngine": 2,          # 引擎2：擅长多语言、自动检测
                    "isTable": "true",       # 逐行返回，适合答题格式
                    "scale": "true",         # 内部放大，提高手写体识别率
                },
                timeout=30,
            )
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("IsErroredOnProcessing", True):
            return None

        results = data.get("ParsedResults", [])
        if not results:
            return None

        # 取第一个结果（单页图片）
        parsed = results[0]
        text = parsed.get("ParsedText", "").strip()
        if text:
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            return {
                "raw_lines": lines,
                "ocr_exit_code": parsed.get("FileParseExitCode", -1),
                "error_details": parsed.get("ErrorMessage", ""),
            }
    except Exception:
        pass

    return None


def ocr_image(image_path: str) -> str:
    """
    识别图片中的文字（纯云端多引擎方案）。
    
    优先级：飞书OCR → OCR.space → 均失败报错
    返回结构化JSON供 LLM Agent 做多轮增强清洗。

    返回格式：
    {
      "engine": "feishu_ocr" | "ocrspace" | "none",
      "text": "逐行合并的原始识别文本",
      "line_count": 行数,
      "confidence_hint": "high"|"medium"|"low",
      "raw_lines": ["行1", "行2", ...],
      "ocr_exit_code": 仅ocrspace有,
      "success": true|false,
      "error": 仅失败时有
    }
    """
    p = _resolve(image_path)
    if not p.exists():
        return json.dumps({"success": False, "error": f"图片文件不存在: {image_path}"}, ensure_ascii=False)

    # 检查图片大小（API限制）
    file_size = p.stat().st_size
    if file_size > 10 * 1024 * 1024:
        return json.dumps({"success": False, "error": "图片超过10MB限制"}, ensure_ascii=False)

    engines_tried = []

    # ── 引擎1：飞书OCR ──
    result = _recognize_via_feishu(p)
    if result:
        engines_tried.append("feishu_ocr")
    else:
        # ── 引擎2：OCR.space ──
        result = _recognize_via_ocrspace(p)
        if result:
            engines_tried.append("ocrspace")

    engine_name = engines_tried[0] if engines_tried else "none"

    if not result:
        # 两个引擎都失败了
        hint = ""
        if not OCR_SPACE_API_KEY:
            hint = " 提示：设置OCR_SPACE_API_KEY环境变量可启用ocr.space免费OCR（https://ocr.space/ocrapi注册获取）"
        return json.dumps({
            "success": False,
            "engine": "none",
            "error": f"所有云端OCR引擎均失败（飞书OCR+OCR.space）。{hint}",
        }, ensure_ascii=False)

    # 格式化结果
    raw_lines = result.get("raw_lines", [])
    text = "\n".join(raw_lines)
    line_count = len(raw_lines)

    # 启发式置信度（考虑到手写体场景，保守评估）
    if line_count >= 2 and all(len(line) >= 2 for line in raw_lines):
        confidence_hint = "medium"  # 手写体最多给medium，让LLM做增强
    elif line_count >= 1 and any(len(line) >= 1 for line in raw_lines):
        confidence_hint = "low"
    else:
        confidence_hint = "low"

    return json.dumps({
        "engine": engine_name,
        "text": text,
        "line_count": line_count,
        "confidence_hint": confidence_hint,
        "raw_lines": raw_lines,
        "success": True,
        **({"ocr_exit_code": result.get("ocr_exit_code"), "error_details": result.get("error_details", "")}
           if engine_name == "ocrspace" else {}),
    }, ensure_ascii=False)


# ─── 工具 10：LLM视觉识别（多API支持）────────────────────────────

# 视觉API配置（按优先级尝试）
VISION_APIS = []

# 1. 硅基流动（免费注册送14元，Qwen3-VL手写识别强，优先）
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
if SILICONFLOW_API_KEY:
    VISION_APIS.append({
        "name": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_API_KEY,
        "model": "Qwen/Qwen3-VL-32B-Instruct",
    })

# 2. 火山方舟（需要 endpoint_id，模型名模式不可用）
VOLC_ENDPOINT_ID = os.environ.get("VOLC_ENDPOINT_ID", "")
if API_KEY and "volces.com" in BASE_URL and VOLC_ENDPOINT_ID:
    VISION_APIS.append({
        "name": "volcengine_vision",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": API_KEY,
        "model": VOLC_ENDPOINT_ID,
    })

# 3. OpenAI兼容API（如通义千问、智谱等）
OPENAI_VISION_API_KEY = os.environ.get("OPENAI_VISION_API_KEY", "")
OPENAI_VISION_BASE_URL = os.environ.get("OPENAI_VISION_BASE_URL", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o-mini")
if OPENAI_VISION_API_KEY:
    VISION_APIS.append({
        "name": "openai_compatible",
        "base_url": OPENAI_VISION_BASE_URL or "https://api.openai.com/v1",
        "api_key": OPENAI_VISION_API_KEY,
        "model": OPENAI_VISION_MODEL,
    })


def analyze_image_via_llm(image_path: str, prompt: str = "请识别图片中的文字内容") -> str:
    """
    使用LLM视觉能力直接分析图片。
    支持多种视觉API（硅基流动/火山方舟/OpenAI兼容），按优先级尝试。
    如果所有视觉API都不可用，返回失败（降级到传统OCR+LLM增强）。
    """
    p = _resolve(image_path)
    if not p.exists():
        return json.dumps({"success": False, "error": f"图片文件不存在: {image_path}"}, ensure_ascii=False)
    
    # 检查图片大小
    file_size = p.stat().st_size
    if file_size > 10 * 1024 * 1024:
        return json.dumps({"success": False, "error": "图片超过10MB限制"}, ensure_ascii=False)
    
    # 如果没有可用的视觉API，直接返回失败
    if not VISION_APIS:
        return json.dumps({
            "success": False, 
            "error": "无可用的视觉API（需配置SILICONFLOW_API_KEY），将降级到OCR+LLM增强",
            "engine": "none"
        }, ensure_ascii=False)
    
    try:
        with open(str(p), "rb") as f:
            image_data = f.read()
        image_b64 = base64.b64encode(image_data).decode()
    except Exception as e:
        return json.dumps({"success": False, "error": f"读取图片失败: {e}"}, ensure_ascii=False)
    
    # 按优先级尝试各视觉API
    errors = []
    for api_config in VISION_APIS:
        try:
            vision_client = OpenAI(api_key=api_config["api_key"], base_url=api_config["base_url"])
            # Doubao vision 用低温度提高手写识别准确率
            is_doubao = "doubao" in api_config.get("name", "")
            resp = vision_client.chat.completions.create(
                model=api_config["model"],
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                        ]
                    }
                ],
                max_tokens=2000 if is_doubao else 1000,
                temperature=0.1 if is_doubao else 0.3,
            )
            analysis = resp.choices[0].message.content or ""
            
            return json.dumps({
                "success": True,
                "engine": f"llm_vision_{api_config['name']}",
                "analysis": analysis,
                "text": analysis,
                "confidence_hint": "high",
                "raw_lines": [line.strip() for line in analysis.split("\n") if line.strip()],
            }, ensure_ascii=False)
        except Exception as e:
            print(f"[OCR-VISION] {api_config['name']} 失败: {e}")
            errors.append(f"{api_config['name']}: {e}")
            continue
    
    return json.dumps({
        "success": False, 
        "error": f"所有视觉API均失败: {'; '.join(errors)}",
        "engine": "none"
    }, ensure_ascii=False)


# ─── 图片预处理（长图分割/缩放/手写增强）───────────────────────────────

def _preprocess_image(image_path: str) -> str:
    """预处理图片：长图分割、缩放、手写增强。返回处理后图片路径。"""
    from PIL import Image, ImageEnhance, ImageFilter
    img = Image.open(image_path)
    w, h = img.size
    
    # 如果图片过宽（横屏截图），不做处理
    if w > h * 4:
        return image_path
    
    # 长图检测：高度超过宽度3倍（如滚动截图）
    if h > w * 3:
        segment_max_h = w * 2
        segments = []
        for y in range(0, h, segment_max_h):
            box = (0, y, w, min(y + segment_max_h, h))
            seg = img.crop(box)
            seg_path = image_path.replace('.', f'_seg{y//segment_max_h}.')
            seg.save(seg_path)
            segments.append(seg_path)
        return segments
    
    # 小图放大（文字太小无法识别）
    if w < 800 or h < 600:
        scale = max(2.0, 1200 / w)
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
        scaled_path = image_path.replace('.', '_scaled.')
        img.save(scaled_path)
        return scaled_path
    
    # 手写体增强：提高对比度 + 锐化（帮助视觉模型识别）
    try:
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)  # 对比度提升50%
        enhancer = ImageEnhance.Sharpness(img)
        img = img.filter(ImageFilter.SHARPEN)
        enhanced_path = image_path.replace('.', '_enhanced.')
        img.save(enhanced_path)
        return enhanced_path
    except Exception:
        return image_path


# ─── 增强的图片识别函数（整合多引擎+LLM视觉+LLM增强）────────────────────────────
def enhanced_ocr_image(image_path: str, use_llm_vision: bool = True) -> str:
    """
    增强的图片识别：预处理+LLM视觉+传统OCR三管齐下。
    支持长图分割、手写识别优化。
    """
    # 0. 预处理：长图分割、小图放大
    processed = _preprocess_image(image_path)
    
    if isinstance(processed, list):
        # 长图分割成多段，每段分别OCR后合并
        all_texts = []
        for seg_path in processed:
            r = json.loads(enhanced_ocr_image(seg_path, use_llm_vision))
            if r.get("success"):
                all_texts.append(r.get("text", ""))
            Path(seg_path).unlink(missing_ok=True)  # 清理临时文件
        if all_texts:
            merged = "\n".join(all_texts)
            return json.dumps({"success": True, "engine": "merged_segments", "text": merged, "confidence_hint": "high"}, ensure_ascii=False)
        return json.dumps({"success": False, "error": "长图OCR失败"}, ensure_ascii=False)
    
    processed_path = processed if isinstance(processed, str) else image_path
    
    # 1. 优先使用LLM视觉（如果可用）
    if use_llm_vision and VISION_APIS:
        engine_name = VISION_APIS[0]["name"]
        if "doubao" in engine_name:
            vision_prompt = (
                "你是一个专业的手写体识别助手。请仔细识别图片中所有手写文字和数字。\n\n"
                "这是小学生手写的数学/英语/词汇作业答案。\n\n"
                "⚠️ 输出格式（严格逐题）：\n"
                "1. 174\n"
                "2. 140米\n"
                "3. C\n"
                "4. 58×3=174\n"
                "5. apple\n\n"
                "铁则：\n"
                "- 逐题输出，每行一个答案，格式：题号. 答案\n"
                "- 数学答案：准确识别数字和运算符号（+ - × ÷ =）\n"
                "- 英语答案：准确识别字母大小写、单词拼写\n"
                "- 词汇答案：识别选项字母（A/B/C/D）或单词\n"
                "- 题号可能是 1. 2. 3. 或 ① ② ③ 或 Q000001\n"
                "- 字迹潦草也要尽力辨认，无法辨认标注[?]\n"
                "- 不要合并不同题目的答案，保持逐题格式\n"
                "- 只输出识别结果，不要解释\n"
                "- ⚠️ 特别注意数字混淆：4↔9, 7↔1, 2↔7, 8↔6, 5↔3"
            )
        else:
            vision_prompt = (
                "请仔细识别图片中的所有手写文字和数字。这是小学生手写的数学/英语/词汇答案。\n"
                "⚠️ 铁则：\n"
                "- 逐题输出，每行一个答案，格式：题号 + 答案内容\n"
                "  例：1. 174    或   1. C    或   1. 58×3=174\n"
                "- 数学答案：准确识别数字和运算符号（+ - × ÷ =）\n"
                "- 英语答案：准确识别字母大小写、单词拼写\n"
                "- 词汇答案：识别选项字母（A/B/C/D）或单词\n"
                "- 题号可能是 1.  2.  3.  或 .  ①  ②  ③  或 Q000001\n"
                "- 字迹潦草也要尽力辨认，无法辨认标注[?]\n"
                "- 不要合并不同题目的答案，保持逐题格式\n"
                "- ⚠️ 特别注意数字混淆：4↔9, 7↔1, 2↔7, 8↔6, 5↔3"
            )
        llm_result = analyze_image_via_llm(processed_path, vision_prompt)
        llm_data = json.loads(llm_result)
        if llm_data.get("success"):
            if processed_path != image_path:
                Path(processed_path).unlink(missing_ok=True)
            print(f"[OCR-VISION] ✅ 识别成功: {llm_data.get('text','')[:50]}...")
            return llm_result
        print(f"[OCR-VISION] ❌ 识别失败: {llm_data.get('error','未知错误')[:100]}")
        if llm_data.get("engine") == "none":
            print(f"[OCR-VISION] 视觉API不可用，降级到传统OCR")
    
    # 2. 降级到传统OCR + 强化LLM增强清洗
    ocr_result = ocr_image(processed_path)
    ocr_data = json.loads(ocr_result)
    if processed_path != image_path:
        Path(processed_path).unlink(missing_ok=True)
    
    if not ocr_data.get("success"):
        return ocr_result  # OCR也失败了，直接返回
    
    # 3. 对OCR结果做强化LLM增强清洗（在ocr_image返回前就做一轮）
    ocr_text = ocr_data.get("text", "")
    raw_lines = ocr_data.get("raw_lines", [])
    
    if ocr_text and len(ocr_text) > 5:
        try:
            # 第0轮：LLM智能修正OCR错误
            correction_prompt = (
                "你是小学生手写答案OCR修正专家。请修正以下OCR识别结果中的错误。\n\n"
                "⚠️ 手写数字常见混淆（很重要！）：\n"
                "- 7常被误识别为1或2（如72→12, 57→51）\n"
                "- 2常被误识别为1或7（如282→181, 282→787）\n"
                "- 8常被误识别为6或0（如282→262, 180→160）\n"
                "- 5常被误识别为3或S（如45→43）\n"
                "- 4常被误识别为9（如24→29）\n"
                "- 0常被误识别为O或6（如120→12O）\n"
                "- 3常被误识别为8（如23→28）\n"
                "- 6常被误识别为0或G（如36→30）\n\n"
                "修正原则：\n"
                "- 这是数学计算题答案，应该以数字为主\n"
                "- 如果结果看起来不合理（如72变成12），优先修正为更合理的数字\n"
                "- 多位数中每个数字都要检查，不能只看第一位\n"
                "- 运算符：×↔x, ÷↔/或+, =↔-或—\n\n"
                f"原始OCR结果：\n{ocr_text}\n\n"
                "请逐行输出修正后的文本（只输出修正结果，不要解释）："
            )
            corrected = call_llm(correction_prompt)
            if corrected and len(corrected) > 3:
                corrected_lines = [line.strip() for line in corrected.split("\n") if line.strip()]
                ocr_data["text"] = corrected
                ocr_data["raw_lines"] = corrected_lines
                ocr_data["line_count"] = len(corrected_lines)
                ocr_data["llm_corrected"] = True
                ocr_data["original_ocr_text"] = ocr_text
                # 提升置信度
                ocr_data["confidence_hint"] = "medium"
        except Exception:
            pass  # LLM增强失败，使用原始OCR结果
    
    return json.dumps(ocr_data, ensure_ascii=False)


# ─── 工具 10：按日期查找历史题目 ───────────────────────────────────

def find_questions(date_hint: str = "") -> str:
    """按日期或试卷编号查找题目。支持 '2026-05-29'/'29号'/'0603'/'T0609A'/'today'。"""
    import re
    questions_dir = DATA_DIR / "questions"
    questions_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    files = sorted(questions_dir.glob("questions_*.json"), reverse=True)

    # ── 先检查是否是试卷编号（如 T0609A、V0609B、C0609A）──
    if date_hint and re.match(r'^[TVWC]\d{4}[A-Z]$', date_hint.strip()):
        test_id = date_hint.strip()
        # 搜索所有存档文件
        all_files = list(questions_dir.glob("*.json"))
        today_file = DATA_DIR / "today_questions.json"
        if today_file.exists():
            all_files.append(today_file)
        for f in all_files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("test_id") == test_id:
                    qs = data.get("questions", data.get("math", []) + data.get("english", []) + data.get("vocab", []))
                    return json.dumps({
                        "success": True, "test_id": test_id,
                        "date": data.get("date", ""), "file": str(f),
                        "questions": qs,
                    }, ensure_ascii=False)
            except: pass
        return json.dumps({"success": False, "error": f"未找到试卷 {test_id}"}, ensure_ascii=False)

    if not files and not (DATA_DIR / "today_questions.json").exists():
        return json.dumps({"success": False, "error": "没有找到任何题目存档"}, ensure_ascii=False)

    # 列出所有存档
    all_archives = []
    for f in files:
        all_archives.append(f.stem.replace("questions_", ""))

    # 如果没有传 date_hint，返回存档列表
    if not date_hint:
        result = {"success": True, "archives": all_archives, "today": today}
        if (DATA_DIR / "today_questions.json").exists():
            result["today_file"] = str(DATA_DIR / "today_questions.json")
        return json.dumps(result, ensure_ascii=False)

    # 解析用户输入的日期提示
    target_date = None
    hint = date_hint.strip().lower()

    if hint == "today" or hint == "今天":
        target_date = today
    elif hint.startswith("202") and len(hint) >= 10:
        # 直接是日期格式 2026-05-29
        target_date = hint[:10]
    elif re.match(r'^\d{4}$', hint) and len(hint) == 4:
        # "0603" 纯数字MMDD格式
        month, day = int(hint[:2]), int(hint[2:])
        year = datetime.now().year
        if month > datetime.now().month:
            year -= 1
        target_date = f"{year}-{month:02d}-{day:02d}"
    else:
        # "29号" / "5月29日" / "5.29" 等
        m = re.search(r'(\d{1,2})\s*[月.\-]\s*(\d{1,2})\s*[日号]?', hint)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year = datetime.now().year
            # 如果月份比当前月份大，可能是去年
            if month > datetime.now().month:
                year -= 1
            target_date = f"{year}-{month:02d}-{day:02d}"
        else:
            # 只匹配了日期数字，如 "29"
            m = re.search(r'(\d{1,2})\s*[日号]', hint)
            if m:
                day = int(m.group(1))
                now = datetime.now()
                month, year = now.month, now.year
                # 如果日子在今天之后，说明是上个月（如6月1日说"29号"→5月29日）
                if day > now.day:
                    month -= 1
                    if month == 0:
                        month = 12
                        year -= 1
                target_date = f"{year}-{month:02d}-{day:02d}"

    if not target_date:
        return json.dumps({
            "success": False,
            "error": f"无法解析日期提示 '{date_hint}'，请用 '5月29日' 或 '2026-05-29' 格式",
            "archives": all_archives,
        }, ensure_ascii=False)

    # 查找匹配的档案
    target_file = questions_dir / f"questions_{target_date}.json"
    if target_file.exists():
        content = target_file.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
            return json.dumps({
                "success": True,
                "date": target_date,
                "file": str(target_file),
                "questions": data.get("questions", data if isinstance(data, list) else []),
            }, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps({"success": False, "error": f"题目文件损坏: {target_file}"}, ensure_ascii=False)

    # 没找到，列出相近的
    nearby = [a for a in all_archives if target_date[:7] in a or target_date[5:7] in a]
    return json.dumps({
        "success": False,
        "date": target_date,
        "error": f"未找到 {target_date} 的题目存档",
        "nearby_archives": nearby[:5] if nearby else all_archives[:5],
    }, ensure_ascii=False)


# ─── 工具 11：飞书消息发送 ──────────────────────────────────────────

# 飞书 access_token 缓存
_feishu_token = {"token": "", "expires_at": 0.0}
FEISHU_BASE = CFG.get("feishu", {}).get("base_url", "https://open.feishu.cn/open-apis")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")


def _get_feishu_token() -> str:
    """获取飞书 tenant_access_token（带缓存）。"""
    global _feishu_token
    if _feishu_token["token"] and time.time() < _feishu_token["expires_at"] - 60:
        return _feishu_token["token"]
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return ""
    import requests
    resp = requests.post(
        f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") == 0:
        _feishu_token = {
            "token": data["tenant_access_token"],
            "expires_at": time.time() + data.get("expire", 7200),
        }
        return _feishu_token["token"]
    return ""


# ── 全局开关：定时出题期间阻止 LLM 调用 send_feishu ──
_BLOCK_SEND_FEISHU = False


def send_feishu(receive_id: str, msg_type: str, content: str) -> str:
    """
    发送飞书消息，自动识别 open_id / chat_id / user_id。

    Args:
        receive_id: 接收者 ID（ou_xxx, oc_xxx, on_xxx）
        msg_type: "text"（纯文本）、"interactive"（卡片消息）或 "image"（图片）
        content: 消息内容。image 类型时为本地图片路径
    """
    global _BLOCK_SEND_FEISHU
    if _BLOCK_SEND_FEISHU:
        return "BLOCKED: send_feishu is disabled during scheduled task. Do NOT call this function, system will push from queue instead."
    
    import requests
    token = _get_feishu_token()
    if not token:
        return "ERROR: 飞书未配置（FEISHU_APP_ID/FEISHU_APP_SECRET 未设置）"

    # 自动识别 receive_id 类型
    if receive_id.startswith("oc_"):
        id_type = "chat_id"
    elif receive_id.startswith("ou_"):
        id_type = "open_id"
    elif receive_id.startswith("on_"):
        id_type = "union_id"
    else:
        id_type = "open_id"  # fallback

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if msg_type == "interactive":
        body = {
            "receive_id": receive_id,
            "msg_type": "interactive",
            "content": content,
        }
    elif msg_type == "image":
        # 飞书图片消息：先上传图片获取 image_key，再发送
        img_path = _resolve(content)
        if not img_path.exists():
            return f"ERROR: 图片文件不存在: {content}"
        try:
            # 上传图片
            with open(str(img_path), "rb") as f:
                upload_resp = requests.post(
                    f"{FEISHU_BASE}/im/v1/images",
                    headers={"Authorization": f"Bearer {token}"},
                    files={"image": (img_path.name, f, "image/png")},
                    data={"image_type": "message"},
                    timeout=30,
                )
            upload_data = upload_resp.json()
            if upload_data.get("code") != 0:
                return f"ERROR: 图片上传失败 code={upload_data.get('code')} msg={upload_data.get('msg','')}"
            image_key = upload_data["data"]["image_key"]
            # 发送图片消息
            body = {
                "receive_id": receive_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}),
            }
        except Exception as e:
            return f"ERROR: 图片上传异常: {e}"
    else:
        body = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}),
        }

    try:
        resp = requests.post(
            f"{FEISHU_BASE}/im/v1/messages?receive_id_type={id_type}",
            headers=headers,
            json=body,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            msg_id = data.get('data',{}).get('message_id','')
            print(f"[FEISHU_SEND] ✅ 消息已发送: receive_id={receive_id[:16]}... msg_id={msg_id[:16]}...")
            return f"OK: 飞书消息已发送 (message_id={msg_id})"
        print(f"[FEISHU_SEND] ❌ 发送失败: code={data.get('code')} msg={data.get('msg','')}")
        return f"ERROR: 飞书发送失败 code={data.get('code')} msg={data.get('msg','')}"
    except Exception as e:
        print(f"[FEISHU_SEND] ❌ 异常: {e}")
        return f"ERROR: 飞书发送异常: {e}"


# ─── 飞书图片下载（供 server 使用） ──────────────────────────────────

def download_feishu_image(message_id: str, image_key: str) -> Optional[str]:
    """从飞书下载图片，返回本地路径。"""
    import requests
    token = _get_feishu_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{image_key}",
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "image"},
            timeout=30,
            stream=True,
        )
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("Content-Type", "image/png")
        ext = "png"
        if "jpeg" in content_type or "jpg" in content_type:
            ext = "jpg"
        elif "gif" in content_type:
            ext = "gif"
        save_dir = DATA_DIR / "images"
        save_dir.mkdir(parents=True, exist_ok=True)
        save_path = save_dir / f"{image_key}.{ext}"
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return str(save_path)
    except Exception:
        return None


def download_feishu_file(message_id: str, file_key: str, file_name: str = "") -> Optional[str]:
    """从飞书下载文件（PDF/文档等），返回本地路径。"""
    import requests
    token = _get_feishu_token()
    if not token:
        return None
    try:
        resp = requests.get(
            f"{FEISHU_BASE}/im/v1/messages/{message_id}/resources/{file_key}",
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "file"},
            timeout=60,
        )
        if resp.status_code != 200:
            return None
        save_dir = DATA_DIR / "files"
        save_dir.mkdir(parents=True, exist_ok=True)
        # 保留原始文件名
        save_name = file_name if file_name else f"{file_key}.bin"
        save_path = save_dir / save_name
        save_path.write_bytes(resp.content)
        return str(save_path)
    except Exception:
        return None


def extract_text_from_pdf(pdf_path: str) -> str:
    """从PDF文件提取文本内容。返回提取的文本或错误信息。"""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(pdf_path)
        texts = []
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text and page_text.strip():
                texts.append(f"--- 第{i+1}页 ---\n{page_text.strip()}")
        if texts:
            return "\n\n".join(texts)
        return "(PDF内容为空或为扫描件，请尝试拍照上传)"
    except ImportError:
        return "ERROR: PyPDF2 未安装，请在 requirements.txt 中添加 PyPDF2>=3.0.0"
    except Exception as e:
        return f"ERROR: PDF解析失败: {e}"


# ─── 工具 12：几何图形绘制 ──────────────────────────────────────────

def draw_geometry(description: str, output_path: str = "") -> str:
    """
    用matplotlib绘制几何图形（三角形、正方形、长方形、圆、轴对称、平移等）。
    返回生成的图片路径，供 send_feishu 发送。
    
    Args:
        description: 图形描述，JSON格式。支持以下类型：
            - rect: {"type":"rect","width":8,"height":5,"label":"长8cm宽5cm的长方形","grid":true}
            - square: {"type":"square","side":6,"label":"边长6cm的正方形","grid":true}
            - triangle: {"type":"triangle","points":[[0,0],[6,0],[3,5]],"label":"三角形","grid":true}
            - circle: {"type":"circle","radius":4,"label":"半径4cm的圆","grid":true}
            - symmetry: {"type":"symmetry","shape":"triangle","points":[[0,0],[4,0],[2,3]],"axis":"vertical","label":"轴对称图形"}
            - translation: {"type":"translation","shape":"rect","points":[[0,0],[3,2]],"dx":5,"dy":2,"label":"平移"}
            - grid: {"type":"grid","rows":5,"cols":5,"label":"5×5方格纸"}
            - custom: {"type":"custom","code":"matplotlib代码片段"}
        output_path: 输出图片路径（可选，默认自动生成）
    """
    import matplotlib
    matplotlib.use('Agg')
    # 配置中文字体（优先使用系统自带中文字体）
    import matplotlib.pyplot as plt
    try:
        matplotlib.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'SimHei', 'DejaVu Sans']
        matplotlib.rcParams['axes.unicode_minus'] = False
    except:
        pass
    from matplotlib.patches import Rectangle, Circle, Polygon, FancyArrowPatch
    import matplotlib.patches as mpatches
    import numpy as np
    
    try:
        spec = json.loads(description) if isinstance(description, str) else description
    except json.JSONDecodeError:
        return f"ERROR: 无法解析图形描述JSON: {description[:100]}"
    
    fig, ax = plt.subplots(figsize=(8, 6))
    ax.set_aspect('equal')
    shape_type = spec.get("type", "rect")
    label = spec.get("label", "")
    show_grid = spec.get("grid", False)
    
    if shape_type == "rect":
        w, h = spec.get("width", 6), spec.get("height", 4)
        rect = Rectangle((0, 0), w, h, fill=False, linewidth=2, edgecolor='#2196F3')
        ax.add_patch(rect)
        # 标注边长
        ax.annotate(f'{w}cm', xy=(w/2, -0.5), ha='center', fontsize=11, color='#555')
        ax.annotate(f'{h}cm', xy=(-0.8, h/2), ha='center', va='center', fontsize=11, color='#555', rotation=90)
        ax.set_xlim(-1.5, w + 1.5)
        ax.set_ylim(-1.5, h + 1.5)
        
    elif shape_type == "square":
        s = spec.get("side", 5)
        square = Rectangle((0, 0), s, s, fill=False, linewidth=2, edgecolor='#2196F3')
        ax.add_patch(square)
        ax.annotate(f'{s}cm', xy=(s/2, -0.5), ha='center', fontsize=11, color='#555')
        ax.set_xlim(-1.5, s + 1.5)
        ax.set_ylim(-1.5, s + 1.5)
        
    elif shape_type == "triangle":
        pts = spec.get("points", [[0, 0], [6, 0], [3, 5]])
        tri = Polygon(pts, fill=False, linewidth=2, edgecolor='#2196F3')
        ax.add_patch(tri)
        # 标注顶点
        for i, (x, y) in enumerate(pts):
            ax.annotate(f'{chr(65+i)}', xy=(x, y), xytext=(x+0.2, y+0.2), fontsize=12, fontweight='bold')
        all_x = [p[0] for p in pts]
        all_y = [p[1] for p in pts]
        ax.set_xlim(min(all_x) - 1.5, max(all_x) + 1.5)
        ax.set_ylim(min(all_y) - 1.5, max(all_y) + 1.5)
        
    elif shape_type == "circle":
        r = spec.get("radius", 4)
        circle = Circle((r + 0.5, r + 0.5), r, fill=False, linewidth=2, edgecolor='#2196F3')
        ax.add_patch(circle)
        # 标注半径
        ax.annotate(f'r={r}cm', xy=(r + 0.5, r + 0.5), ha='center', va='center', fontsize=11, color='#555')
        ax.set_xlim(-0.5, 2 * r + 1.5)
        ax.set_ylim(-0.5, 2 * r + 1.5)
        
    elif shape_type == "symmetry":
        pts = spec.get("points", [[0, 0], [4, 0], [2, 3]])
        axis = spec.get("axis", "vertical")
        # 原图形
        tri = Polygon(pts, fill=True, alpha=0.3, facecolor='#2196F3', edgecolor='#2196F3', linewidth=2)
        ax.add_patch(tri)
        # 对称轴
        if axis == "vertical":
            ax_center = max(p[0] for p in pts) + 0.5
            ax.axvline(x=ax_center, color='red', linestyle='--', linewidth=1.5, label='对称轴')
            # 对称图形
            mirror_pts = [[2 * ax_center - p[0], p[1]] for p in pts]
            mirror_tri = Polygon(mirror_pts, fill=True, alpha=0.3, facecolor='#FF9800', edgecolor='#FF9800', linewidth=2)
            ax.add_patch(mirror_tri)
            ax.set_xlim(min(p[0] for p in pts) - 1, max(p[0] for p in mirror_pts) + 1)
        else:
            ax_center = max(p[1] for p in pts) + 0.5
            ax.axhline(y=ax_center, color='red', linestyle='--', linewidth=1.5, label='对称轴')
            mirror_pts = [[p[0], 2 * ax_center - p[1]] for p in pts]
            mirror_tri = Polygon(mirror_pts, fill=True, alpha=0.3, facecolor='#FF9800', edgecolor='#FF9800', linewidth=2)
            ax.add_patch(mirror_tri)
            ax.set_ylim(min(p[1] for p in pts) - 1, max(p[1] for p in mirror_pts) + 1)
        ax.legend(fontsize=9)
        all_y = [p[1] for p in pts] + [p[1] for p in mirror_pts]
        ax.set_ylim(min(all_y) - 1.5, max(all_y) + 1.5)
        
    elif shape_type == "translation":
        pts = spec.get("points", [[0, 0], [3, 2]])
        dx, dy = spec.get("dx", 4), spec.get("dy", 2)
        # 原图形（用矩形表示）
        x0, y0 = pts[0]
        w, h = pts[1][0] - x0, pts[1][1] - y0
        orig = Rectangle((x0, y0), w, h, fill=True, alpha=0.3, facecolor='#2196F3', edgecolor='#2196F3', linewidth=2)
        ax.add_patch(orig)
        ax.annotate('原图', xy=(x0 + w/2, y0 - 0.5), ha='center', fontsize=10, color='#2196F3')
        # 平移后
        trans = Rectangle((x0 + dx, y0 + dy), w, h, fill=True, alpha=0.3, facecolor='#FF9800', edgecolor='#FF9800', linewidth=2)
        ax.add_patch(trans)
        ax.annotate(f'平移({dx},{dy})', xy=(x0 + dx + w/2, y0 + dy - 0.5), ha='center', fontsize=10, color='#FF9800')
        # 箭头
        arrow = FancyArrowPatch((x0 + w/2, y0 + h/2), (x0 + dx + w/2, y0 + dy + h/2),
                                arrowstyle='->', mutation_scale=20, linewidth=2, color='red')
        ax.add_patch(arrow)
        ax.set_xlim(min(x0, x0 + dx) - 1.5, max(x0 + w, x0 + dx + w) + 1.5)
        ax.set_ylim(min(y0, y0 + dy) - 1.5, max(y0 + h, y0 + dy + h) + 1.5)
        
    elif shape_type == "grid":
        rows, cols = spec.get("rows", 5), spec.get("cols", 5)
        for i in range(rows + 1):
            ax.axhline(y=i, color='#ccc', linewidth=0.5)
        for j in range(cols + 1):
            ax.axvline(x=j, color='#ccc', linewidth=0.5)
        ax.set_xlim(-0.5, cols + 0.5)
        ax.set_ylim(-0.5, rows + 0.5)
        
    elif shape_type == "custom":
        code = spec.get("code", "")
        exec(code, {"ax": ax, "plt": plt, "np": np, "mpatches": mpatches})
        
    else:
        return f"ERROR: 不支持的图形类型: {shape_type}"
    
    if label:
        ax.set_title(label, fontsize=14, fontweight='bold', pad=15)
    
    if show_grid:
        ax.grid(True, alpha=0.3, linestyle='--')
    
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)
    
    # 保存图片
    if not output_path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(DATA_DIR / "images" / f"geometry_{ts}.png")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=120, bbox_inches='tight', pad_inches=0.3)
    plt.close()
    
    return json.dumps({
        "success": True,
        "image_path": output_path,
        "description": label or shape_type,
    }, ensure_ascii=False)


# ─── 工具：追加错题（Python层处理，避免LLM覆盖写入）───────────────────

def append_error_book(errors_json: str) -> str:
    """向 error_book.json 追加一条或多条错题记录（Python层处理追加，不会覆盖已有数据）。
    errors_json 是 JSON 数组字符串，如 [{"error_id":"E0629001","test_id":"T0629A","date":"2026-06-29","question":"...","student_answer":"...","correct_answer":"...","error_type":"...","reviewed_date":null}]
    """
    error_file = DATA_DIR / "error_book.json"
    try:
        new_errors = json.loads(errors_json)
        if not isinstance(new_errors, list):
            new_errors = [new_errors]
    except Exception as e:
        return f"ERROR: 无法解析错题JSON: {e}"
    
    existing = []
    if error_file.exists():
        try:
            existing = json.loads(error_file.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    
    existing.extend(new_errors)
    error_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"OK: 已追加 {len(new_errors)} 条错题，当前共 {len(existing)} 条"


# ══════════════════════════════════════════════════════════════════════
# 工具注册表 + JSON Schema
# ══════════════════════════════════════════════════════════════════════

TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "list_dir": list_dir,
    "bash": bash,
    "ask_user": ask_user,
    "call_llm": call_llm,
    "web_search": web_search,
    "ocr_image": enhanced_ocr_image,  # 使用增强版
    "find_questions": find_questions,
    "draw_geometry": draw_geometry,
    "send_feishu": send_feishu,
    "append_error_book": append_error_book,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "读取文件，返回带行号的文本。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "写入/覆盖文件，自动创建目录。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "edit_file", "description": "在文件中精准替换文本。old 必须唯一出现一次。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"},
        }, "required": ["path", "old", "new"]},
    }},
    {"type": "function", "function": {
        "name": "list_dir", "description": "列出目录中的文件和子目录。",
        "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "bash", "description": "执行 Shell 命令。",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"}, "timeout": {"type": "integer", "default": 30},
        }, "required": ["cmd"]},
    }},
    {"type": "function", "function": {
        "name": "ask_user", "description": "向用户提问并等待回复。",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    }},
    {"type": "function", "function": {
        "name": "call_llm", "description": "隔离的 LLM 子调用，用于生成内容、分析数据、格式化输出等子任务。主对话历史不受影响。",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "要发给LLM的完整指令"},
            "system": {"type": "string", "description": "可选的系统提示词", "default": ""},
        }, "required": ["prompt"]},
    }},
    {"type": "function", "function": {
        "name": "web_search", "description": "联网搜索最新信息（题库、教育方法、知识点等）。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "max_results": {"type": "integer", "default": 5},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "ocr_image",
        "description": "【增强版】识别图片中的文字（优先使用LLM视觉能力，降级到传统OCR）。自动使用LLM视觉分析图片内容，返回更准确的结果。对于手写体答案识别，准确率大幅提升。返回JSON：engine(llm_vision/feishu_ocr/ocrspace)/analysis/text/confidence_hint。务必用call_llm做3轮增强清洗（清理噪音→结构化提取→交叉验证）。只调用一次即可，不要重复调用。",
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string", "description": "本地图片文件路径"},
        }, "required": ["image_path"]},
    }},
    {"type": "function", "function": {
        "name": "find_questions",
        "description": "【按日期查找历史题目】用户说'29号第2题答案'时调用此工具找对应日期的题目。date_hint支持：'2026-05-29'/'29号'/'5月29日'/'5.29'/'today'，不传返回所有存档列表。返回JSON含date/file/questions字段。⚠️ 批改前必须先调用此工具找到正确的题目！",
        "parameters": {"type": "object", "properties": {
            "date_hint": {"type": "string", "description": "日期提示，如'29号'/'5月29日'/'2026-05-29'/'today'，不传返回存档列表"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "draw_geometry",
        "description": "【绘制几何图形】用matplotlib绘制三角形、正方形、长方形、圆、轴对称、平移等几何图形，生成PNG图片。description为JSON：{\"type\":\"rect\"|\"square\"|\"triangle\"|\"circle\"|\"symmetry\"|\"translation\"|\"grid\"|\"custom\", ...}。返回图片路径，配合send_feishu发送。",
        "parameters": {"type": "object", "properties": {
            "description": {"type": "string", "description": "图形描述JSON，如{\"type\":\"rect\",\"width\":8,\"height\":5,\"label\":\"长8cm宽5cm的长方形\"}"},
            "output_path": {"type": "string", "description": "输出图片路径（可选，默认自动生成）"},
        }, "required": ["description"]},
    }},
    {"type": "function", "function": {
        "name": "send_feishu",
        "description": "发送飞书消息。receive_id 支持 ou_（用户open_id）、oc_（群聊chat_id）、on_（union_id），系统自动识别。msg_type='text' 为纯文本，msg_type='interactive' 为卡片消息（content 需为完整的飞书卡片 JSON 对象字符串），msg_type='image' 发送图片（content 为本地图片路径）。",
        "parameters": {"type": "object", "properties": {
            "receive_id": {"type": "string", "description": "接收者ID：ou_xxx=用户私聊, oc_xxx=群聊, on_xxx=union_id"},
            "msg_type": {"type": "string", "description": "消息类型：text、interactive 或 image"},
            "content": {"type": "string", "description": "消息内容。interactive 类型时需为完整的飞书卡片 JSON 字符串，image 类型时为本地图片路径"},
        }, "required": ["receive_id", "msg_type", "content"]},
    }},
    {"type": "function", "function": {
        "name": "append_error_book",
        "description": "【追加错题】向 error_book.json 追加一条或多条错题记录。Python层处理追加，不会覆盖已有数据。批改发现错题时，必须用此工具存入错题本，不要用 write_file 直接写！",
        "parameters": {"type": "object", "properties": {
            "errors_json": {"type": "string", "description": "JSON数组字符串，如 [{\"error_id\":\"E0629001\",\"test_id\":\"T0629A\",\"date\":\"2026-06-29\",\"question\":\"...\",\"student_answer\":\"...\",\"correct_answer\":\"...\",\"error_type\":\"...\",\"reviewed_date\":null}]"},
        }, "required": ["errors_json"]},
    }},
]


# ─── 工具分发 ────────────────────────────────────────────────────────

def dispatch(name: str, args_json: str, on_ask_user=None) -> str:
    try:
        args = json.loads(args_json) if args_json else {}
    except json.JSONDecodeError as e:
        return f"ERROR: invalid JSON in arguments: {e}"
    fn = TOOLS.get(name)
    if not fn:
        return f"ERROR: unknown tool: {name}"
    if name == "ask_user":
        args["_on_ask_user"] = on_ask_user
    try:
        result = fn(**args)
    except TypeError as e:
        return f"ERROR: bad arguments to {name}: {e}"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    result_str = str(result) if result is not None else ""
    if len(result_str) > MAX_TOOL_OUTPUT:
        result_str = result_str[:MAX_TOOL_OUTPUT] + f"\n[... truncated, original {len(result_str)} chars]"
    return result_str


# ─── LLM 流式调用 ───────────────────────────────────────────────────

def llm_call_streaming(messages, tools, on_chunk=None):
    response = CLIENT.chat.completions.create(
        model=MODEL, messages=messages, tools=tools, stream=True,
        # 日常对话用快速模型，复杂子任务由 call_llm 用推理模型处理
    )
    content_parts = []
    reasoning_parts = []
    tc_acc: dict[int, dict] = {}

    for chunk in response:
        if INTERRUPTED.is_set():
            response.close()
            break
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        rc = getattr(delta, "reasoning_content", None)
        if rc:
            reasoning_parts.append(rc)
            if on_chunk:
                on_chunk("thinking", rc)

        if delta.content:
            content_parts.append(delta.content)
            if on_chunk:
                on_chunk("content", delta.content)

        if delta.tool_calls:
            for tcd in delta.tool_calls:
                idx = tcd.index
                slot = tc_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tcd.id:
                    slot["id"] = tcd.id
                if tcd.function:
                    if tcd.function.name:
                        slot["name"] += tcd.function.name
                    if tcd.function.arguments:
                        slot["arguments"] += tcd.function.arguments

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    msg = {"role": "assistant", "content": content if content else ""}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tc_acc:
        msg["tool_calls"] = [{
            "id": tc_acc[i]["id"], "type": "function",
            "function": {"name": tc_acc[i]["name"], "arguments": tc_acc[i]["arguments"]},
        } for i in sorted(tc_acc.keys())]
    return msg


# ─── Session 持久化 ──────────────────────────────────────────────────

class Session:
    def __init__(self, path: Path | None = None):
        if path is None:
            ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            self.path = SESSIONS_DIR / f"{ts}.jsonl"
            self.history: list[dict] = []
        else:
            self.path = Path(path)
            self.history = self._load()

    def _load(self) -> list[dict]:
        h: list[dict] = []
        if not self.path.exists():
            return h
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    h.append(json.loads(line))
        return h

    def append(self, msg: dict) -> None:
        self.history.append(msg)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


# ─── Bootstrap ──────────────────────────────────────────────────────

def bootstrap_root() -> str:
    root_file = HOME / "root.md"
    if root_file.exists():
        return root_file.read_text(encoding="utf-8")
    return "你是小肥猫学习助手。"


def init_new_session() -> Session:
    s = Session()
    s.append({"role": "system", "content": bootstrap_root()})
    return s


# ─── Agent Loop ──────────────────────────────────────────────────────

def run(user_msg: str, session: Session, on_chunk=None, on_ask_user=None):
    """执行一个 user turn。LLM 自主决定调哪些工具、怎么处理数据。"""
    INTERRUPTED.clear()
    session.append({"role": "user", "content": user_msg})

    for _ in range(MAX_ITERATIONS):
        if INTERRUPTED.is_set():
            return None

        resp = llm_call_streaming(session.history, TOOL_SCHEMAS, on_chunk)
        session.append(resp)

        if INTERRUPTED.is_set():
            return None

        if not resp.get("tool_calls"):
            return resp.get("content")

        for i, tc in enumerate(resp["tool_calls"]):
            if INTERRUPTED.is_set():
                for remaining_tc in resp["tool_calls"][i:]:
                    session.append({"role": "tool", "tool_call_id": remaining_tc["id"], "content": "ERROR: interrupted"})
                return None

            name = tc["function"]["name"]
            args = tc["function"]["arguments"]
            if on_chunk:
                on_chunk("tool_call", {"name": name, "args": args})
            result = dispatch(name, args, on_ask_user)
            if on_chunk:
                on_chunk("tool_result", {"name": name, "result": result})
            session.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    return None


# ─── 辅助：密码验证（供 server 使用） ────────────────────────────────

def verify_password(password: str) -> bool:
    """验证家长管理密码（从 adjustments.json 读取）。"""
    try:
        adj_file = DATA_DIR / "adjustments.json"
        if adj_file.exists():
            adjustments = json.loads(adj_file.read_text(encoding="utf-8"))
            stored_hash = adjustments.get("admin_password", "")
            if stored_hash and hashlib.sha256(password.encode()).hexdigest() == stored_hash:
                return True
    except Exception:
        pass
    return False
