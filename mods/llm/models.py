"""Model selections and the version-controlled default provider catalogue."""

from copy import deepcopy


DEFAULT_MODEL = "deepseek/deepseek-flash"
DEFAULT_VISION_MODEL = "bytecat/gpt-5.4-mini"

# WHY: OpenAI SDK 不给超时就是 600 秒，对聊天来说等于挂死——一次卡住的视觉识别会让
# 那条 link 线程一直占着，而它在 get_msgs -> 请求的同步路径上。这个值是 httpx 的
# 单次操作超时，流式响应每收到一个 chunk 就重新计时，所以长回复不会被它切断；它拦的
# 是"对端不再说话"。可用 llm_system/config 的 request_timeout 覆盖。
DEFAULT_REQUEST_TIMEOUT = 120.0

BYTECAT_PROVIDER_CONFIG = {
    "base_url": "BYTECAT_BASE_URL",
    "api_key": "BYTECAT_API_KEY",
    "models": {
        "gpt-5.4-mini": {"vision": True, "function_calling": True, "prompt_price": 0.075, "completion_price": 0.45},
        "gpt-5.4-openai-compact": {"vision": True, "function_calling": True, "prompt_price": 0.5, "completion_price": 3},
        "gpt-5.5": {"vision": True, "function_calling": True, "prompt_price": 0.5, "completion_price": 3},
        "gpt-5.5-openai-compact": {"vision": True, "function_calling": True, "prompt_price": 1, "completion_price": 8},
        "gpt-5.6-luna": {"vision": True, "function_calling": True, "prompt_price": 0.1, "completion_price": 0.6},
        "gpt-5.6-sol": {"vision": True, "function_calling": True, "prompt_price": 1, "completion_price": 6},
        "gpt-5.6-terra": {"vision": True, "function_calling": True, "prompt_price": 0.25, "completion_price": 1.5},
        "gpt-5.3-codex-spark": {"vision": True, "function_calling": True, "prompt_price": 0.7, "completion_price": 5.6},
    },
}


def split_model_selection(selection: str) -> tuple[str, str]:
    if not isinstance(selection, str):
        raise ValueError("模型必须使用 provider/model 格式")
    provider, separator, model = selection.partition("/")
    if not separator or not provider or not model:
        raise ValueError("模型必须使用 provider/model 格式")
    return provider, model


# WHY: 未登记模型的默认能力——vision 与 function_calling 都当 True，价格当 0。
# 本地 models 不是白名单：config 里的 providers 描述的是"怎么连上谁"（base_url/api_key）
# 和已登记的元数据，模型是否存在由对端决定。认不出来就照原样下传；能力标志只影响本地
# 怎么拼请求（是否把图片转成 data URI、是否附带 tools），不影响对端是否接受。
# 这样换模型不必先改配置；代价是打错的模型名不再被本地拦下，而是换来一条供应商的报错，
# 这是接受的取舍。价格三项**不写**：读价格的地方都用 .get(..., 0)，所以这一轮按 0 计费
# （宁可少算也不因缺元数据而算错），而 `#model` 会把缺失的项显示成 `-`，如实表示"未登记"。
# 唯一的例外是 prompt_cached_price：只缺它一项时按 prompt_price 算，不按 0 算——见 llm.pricing。
UNKNOWN_MODEL_CAPABILITIES = {
    "vision": True,
    "function_calling": True,
}


def resolve_model(config: dict, selection: str) -> tuple[str, str, dict]:
    """把 ``provider/model`` 解析成 ``(provider, api_model, capabilities)``。

    未登记的模型沿用 UNKNOWN_MODEL_CAPABILITIES；未配置的供应商仍然报错，
    因为没有 base_url/api_key 就不存在可调用的对象。
    """
    provider, model = split_model_selection(selection)
    provider_config = config.get("providers", {}).get(provider)
    if not isinstance(provider_config, dict):
        raise ValueError(f"未找到供应商: {provider}")
    models = provider_config.get("models")
    if not isinstance(models, dict):
        models = {}
    capabilities = models.get(model)
    if not isinstance(capabilities, dict):
        capabilities = dict(UNKNOWN_MODEL_CAPABILITIES)
    return provider, model, capabilities


def provider_config(config: dict, selection: str) -> dict:
    """取 *selection* 所属 provider 的配置字典；供应商未配置或格式不对时返回 ``{}``。

    WHY: 比 `resolve_model` 宽松——要的是"这个供应商的计费规则"（`off_peak`）而不是
    "能不能调用"，所以拿不到就当没有规则，不抛错。计费发生在一次响应之后，那里再抛一个
    配置异常，只会把一次成功的调用变成一条聊天里的报错。
    """
    try:
        provider = split_model_selection(selection)[0]
    except ValueError:
        return {}
    value = config.get("providers", {}).get(provider) if isinstance(config, dict) else None
    return value if isinstance(value, dict) else {}


def default_config() -> dict:
    return {
        "providers": {
            "openai": {"base_url": "OPENAI_BASE_URL", "api_key": "OPENAI_API_KEY", "models": {
                "gpt-4o-mini": {"vision": True, "function_calling": True},
                "gpt-4o": {"vision": True, "function_calling": True},
                "gpt-3.5-turbo": {"vision": False, "function_calling": True},
            }},
            "deepseek": {"base_url": "DEEPSEEK_BASE_URL", "api_key": "DEEPSEEK_API_KEY",
                # 官方价目：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
                # 高峰是北京时间周一至周五 9:00-12:00、14:00-18:00，其余空闲且空闲价 = 高峰 × 0.5。
                # 规则写在这里而不是写进每个模型的价格：模型价只有一套数字，峰谷表变动改这一处。
                "off_peak": {
                    "timezone": "Asia/Shanghai",
                    "days": [0, 1, 2, 3, 4],
                    "windows": [["09:00", "12:00"], ["14:00", "18:00"]],
                    "ratio": 0.5,
                },
                "models": {
                # 价格是人民币元/百万 token，一律写**高峰**价：空闲价由上面的 off_peak.ratio 推出。
                # prompt_cached_price 是输入中命中缓存那部分的价格，比未命中价低两个数量级——
                # 聊天的 prompt 绝大多数是重复上下文，不区分就会把费用高估一大截。
                # deepseek-flash 就是原 v4-flash 系列（DeepSeek-V4.1-Flash），自带图像理解；
                # 两个旧名仍可调用、按 Flash 计费，保留它们以免存量的模型选择失效。
                "deepseek-flash": {"vision": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
                "deepseek-v4-pro": {"vision": False, "function_calling": True, "prompt_price": 9, "prompt_cached_price": 0.30, "completion_price": 27},
                "deepseek-v4-flash": {"vision": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
                "deepseek-v4-flash-vision-exp": {"vision": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
            }},
            "bytecat": deepcopy(BYTECAT_PROVIDER_CONFIG),
        },
        "default_model": DEFAULT_MODEL,
        "vision_model": DEFAULT_VISION_MODEL,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
    }
