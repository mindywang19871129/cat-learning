# 视觉API配置（按优先级尝试）
# 注意：当前API Key（Volcengine）没有视觉模型权限，视觉API不可用
# 所有OCR依赖飞书OCR + call_llm增强清洗
VISION_APIS = []

# 如果有硅基流动视觉API Key则启用（Qwen3-VL手写识别强）
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
if SILICONFLOW_API_KEY:
    VISION_APIS.append({
        "name": "siliconflow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key": SILICONFLOW_API_KEY,
        "model": "Qwen/Qwen3-VL-32B-Instruct",
    })

# 如果有火山视觉端点ID则启用（需要额外开通）
VOLC_ENDPOINT_ID = os.environ.get("VOLC_ENDPOINT_ID", "")
if API_KEY and "volces.com" in BASE_URL and VOLC_ENDPOINT_ID:
    VISION_APIS.append({
        "name": "volcengine_vision",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key": API_KEY,
        "model": VOLC_ENDPOINT_ID,
    })

# 如果有OpenAI兼容视觉API则启用
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