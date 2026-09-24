"""Model selections and the version-controlled default provider catalogue."""

from copy import deepcopy


DEFAULT_MODEL = "deepseek/deepseek-flash"
DEFAULT_VISION_MODEL = "bytecat/gpt-5.4-mini"

# WHY: OpenAI SDK 不给超时就是 600 秒，对聊天来说等于挂死——一次卡住的视觉识别会让
# 那条 link 线程一直占着，而它在 get_msgs -> 请求的同步路径上。这个值是 httpx 的
# 单次操作超时，流式响应每收到一个 chunk 就重新计时，所以长回复不会被它切断；它拦的
# 是"对端不再说话"。可用 llm_system/config 的 request_timeout 覆盖。
DEFAULT_REQUEST_TIMEOUT = 120.0

# 官方价目：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# 2026 放假日期：https://www.gov.cn/zhengce/zhengceku/202511/content_7047091.htm
# WHY: 这是一段普通价格函数源码，随默认配置存入 JSON。设备已有配置不会被默认值覆盖；
# 官方调时段、假期或价格时，改配置中的这一个函数即可，不必扩展计价器的规则字段。
DEEPSEEK_PRICE_FN = """def price_fn(when, prices):
    local = when.astimezone(ZoneInfo('Asia/Shanghai'))
    holidays = {
        '2026-01-01', '2026-01-02',
        '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20', '2026-02-23',
        '2026-04-06', '2026-05-01', '2026-05-04', '2026-05-05', '2026-06-19',
        '2026-09-25', '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07',
    }
    peak = (local.weekday() < 5 and local.date().isoformat() not in holidays
            and (9 <= local.hour < 12 or 14 <= local.hour < 18))
    return {key: value * (1 if peak else 0.5) for key, value in prices.items()}
"""

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

    WHY: 比 `resolve_model` 宽松——要的是"这个供应商的计价函数"（`price_fn`）而不是
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
                "price_fn": DEEPSEEK_PRICE_FN,
                "models": {
                # 价格是人民币元/百万 token，这里写基础（高峰）价；实际单价由 price_fn 返回。
                # prompt_cached_price 是输入中命中缓存那部分的价格，比未命中价低两个数量级——
                # 聊天的 prompt 绝大多数是重复上下文，不区分就会把费用高估一大截。
                # deepseek-flash 就是原 v4-flash 系列（DeepSeek-V4.1-Flash），自带图像理解；
                # 两个旧名仍可调用、按 Flash 计费，保留它们以免存量的模型选择失效。
                # tool_images：图片可以放进 tool 消息（工具结果里带图），2026-09-18 实测
                # deepseek-flash 读对了工具结果里那张图的随机码。同族的两个旧名一起登记；
                # 别的供应商没验过，一律不写（默认 False）。
                "deepseek-flash": {"vision": True, "tool_images": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
                "deepseek-v4-pro": {"vision": False, "function_calling": True, "prompt_price": 9, "prompt_cached_price": 0.30, "completion_price": 27},
                "deepseek-v4-flash": {"vision": True, "tool_images": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
                "deepseek-v4-flash-vision-exp": {"vision": True, "tool_images": True, "function_calling": True, "prompt_price": 2, "prompt_cached_price": 0.04, "completion_price": 8},
            }},
            "bytecat": deepcopy(BYTECAT_PROVIDER_CONFIG),
        },
        "default_model": DEFAULT_MODEL,
        "vision_model": DEFAULT_VISION_MODEL,
        "request_timeout": DEFAULT_REQUEST_TIMEOUT,
    }
