"""Model selections and the version-controlled default provider catalogue."""

from copy import deepcopy


DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_VISION_MODEL = "bytecat/gpt-5.4-mini"

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


def resolve_model(config: dict, selection: str) -> tuple[str, str, dict]:
    provider, model = split_model_selection(selection)
    provider_config = config.get("providers", {}).get(provider)
    if not isinstance(provider_config, dict):
        raise ValueError(f"未找到供应商: {provider}")
    models = provider_config.get("models", {})
    if not isinstance(models, dict) or model not in models:
        raise ValueError(f"供应商 {provider} 下未找到模型: {model}")
    return provider, model, models[model]


def default_config() -> dict:
    return {
        "providers": {
            "openai": {"base_url": "OPENAI_BASE_URL", "api_key": "OPENAI_API_KEY", "models": {
                "gpt-4o-mini": {"vision": True, "function_calling": True},
                "gpt-4o": {"vision": True, "function_calling": True},
                "gpt-3.5-turbo": {"vision": False, "function_calling": True},
            }},
            "deepseek": {"base_url": "DEEPSEEK_BASE_URL", "api_key": "DEEPSEEK_API_KEY", "models": {
                "deepseek-chat": {"vision": False, "function_calling": True},
                "deepseek-reasoner": {"vision": False, "function_calling": True},
                "deepseek-v4-flash": {"vision": False, "function_calling": True},
            }},
            "bytecat": deepcopy(BYTECAT_PROVIDER_CONFIG),
        },
        "default_model": DEFAULT_MODEL,
        "vision_model": DEFAULT_VISION_MODEL,
    }
