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
    """隔离的 LLM 子调用，不影响主对话历史。"""
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    resp = CLIENT.chat.completions.create(
        model=MODEL, messages=msgs, stream=False,
    )
    return resp.choices[0].message.content or ""


# ─── 工具 8：联网搜索 ───────────────────────────────────────────────

def web_search(query: str, max_results: int = 5) -> str:
    """联网搜索最新信息。优先用 DeepSeek 联网能力，降级到 Tavily。"""
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
        return "ERROR: 联网搜索不可用（DeepSeek不支持 + TAVILY_API_KEY未设置）"

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


# ─── 工具 9：OCR 图片识别 ───────────────────────────────────────────

def ocr_image(image_path: str) -> str:
    """
    识别图片中的文字（支持手写和印刷体）。
    优先 Tesseract，降级到 DeepSeek Vision。
    """
    p = _resolve(image_path)
    if not p.exists():
        return f"ERROR: 图片文件不存在: {image_path}"

    # 尝试 Tesseract
    try:
        import pytesseract
        from PIL import Image
        lang = CFG.get("ocr", {}).get("tesseract_lang", "chi_sim+eng")
        img = Image.open(str(p))
        text = pytesseract.image_to_string(img, lang=lang)
        if text.strip():
            return json.dumps({"engine": "tesseract", "text": text.strip(), "success": True}, ensure_ascii=False)
    except ImportError:
        pass

    # 降级到 DeepSeek Vision
    try:
        with open(str(p), "rb") as img_file:
            img_base64 = base64.b64encode(img_file.read()).decode()
        resp = CLIENT.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "请识别图片中的所有文字，只输出文字内容，不要添加额外说明。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_base64}"}},
                ],
            }],
            stream=False,
        )
        text = resp.choices[0].message.content or ""
        return json.dumps({"engine": "deepseek_vision", "text": text.strip(), "success": bool(text.strip())}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ─── 工具 10：飞书消息发送 ──────────────────────────────────────────

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


def send_feishu(receive_id: str, msg_type: str, content: str) -> str:
    """
    发送飞书消息。

    Args:
        receive_id: 接收者 open_id（或 chat_id）
        msg_type: "text"（纯文本）或 "interactive"（卡片消息，content 为卡片 JSON 字符串）
        content: 消息内容
    """
    import requests
    token = _get_feishu_token()
    if not token:
        return "ERROR: 飞书未配置（FEISHU_APP_ID/FEISHU_APP_SECRET 未设置）"

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
    else:
        body = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": content}),
        }

    try:
        resp = requests.post(
            f"{FEISHU_BASE}/im/v1/messages?receive_id_type=open_id",
            headers=headers,
            json=body,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return f"OK: 飞书消息已发送 (message_id={data.get('data',{}).get('message_id','')})"
        return f"ERROR: 飞书发送失败 code={data.get('code')} msg={data.get('msg','')}"
    except Exception as e:
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
    "ocr_image": ocr_image,
    "send_feishu": send_feishu,
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
        "name": "ocr_image", "description": "识别图片中的文字（支持手写和印刷体），返回识别的文本。",
        "parameters": {"type": "object", "properties": {
            "image_path": {"type": "string", "description": "本地图片文件路径"},
        }, "required": ["image_path"]},
    }},
    {"type": "function", "function": {
        "name": "send_feishu",
        "description": "发送飞书消息给指定用户。msg_type='text' 为纯文本，msg_type='interactive' 为卡片消息（content 需为完整的飞书卡片 JSON 对象字符串）。",
        "parameters": {"type": "object", "properties": {
            "receive_id": {"type": "string", "description": "接收者的 open_id"},
            "msg_type": {"type": "string", "description": "消息类型：text 或 interactive"},
            "content": {"type": "string", "description": "消息内容。interactive 类型时需为完整的飞书卡片 JSON 字符串"},
        }, "required": ["receive_id", "msg_type", "content"]},
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
