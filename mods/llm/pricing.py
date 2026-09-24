"""LLM 费用：模型基础单价 + 可选的配置函数。

`llm_system/config` 的 provider 或 model 可写 `price_fn`，值为定义
`price_fn(when, prices)` 的 Python 源码。`when` 是带时区的请求发起时刻，`prices`
是三项基础单价（元/百万 token）的字典；函数返回同形状的实际单价。
模型自己的 `price_fn` 优先；设为 null 则不继承 provider 的函数。

配置本来属于 Bot 的宿主机信任域；这里直接执行函数源码，不建立另一套规则语言。
"""

from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from zoneinfo import ZoneInfo


PRICE_KEYS = ("prompt_price", "prompt_cached_price", "completion_price")
UNIT = 1_000_000


def _number(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def unit_prices(provider, capabilities, when: datetime | None = None) -> dict:
    """返回本次请求的三项单价；没有函数时直接返回模型基础单价。

    WHY: 缺失的缓存命中价跟随普通输入价，显式的 0 才表示免费。重复上下文的
    缓存命中通常占多数，默认成 0 会把费用静默少算一大截。
    """
    provider = provider if isinstance(provider, dict) else {}
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    prices = {key: _number(capabilities.get(key)) for key in PRICE_KEYS}
    cached_price = capabilities.get("prompt_cached_price")
    if not isinstance(cached_price, (int, float)) or isinstance(cached_price, bool):
        prices["prompt_cached_price"] = prices["prompt_price"]

    source = capabilities["price_fn"] if "price_fn" in capabilities else provider.get("price_fn")
    if source is None:
        assert "off_peak" not in provider and "off_peak" not in capabilities, "旧 off_peak 已停用，请改用 price_fn"
        return prices
    assert isinstance(source, str) and source.strip(), "price_fn 必须是非空 Python 函数源码"
    moment = when if when is not None else datetime.now(timezone.utc)
    assert moment.tzinfo is not None and moment.utcoffset() is not None, "price_fn 的 when 必须带时区"
    namespace = {"ZoneInfo": ZoneInfo}
    exec(source, namespace)
    function = namespace.get("price_fn")
    assert callable(function), "price_fn 源码必须定义同名函数"
    result = function(moment, prices.copy())
    assert isinstance(result, dict) and result.keys() == prices.keys(), "price_fn 必须返回全部三项单价"
    assert all(isinstance(value, (int, float)) and not isinstance(value, bool)
               and isfinite(value) and value >= 0 for value in result.values()), "price_fn 单价必须是有限非负数"
    return {key: float(result[key]) for key in PRICE_KEYS}


def token_cost(provider, capabilities, prompt_tokens: int = 0, completion_tokens: int = 0,
               cached_tokens: int = 0, when: datetime | None = None) -> float:
    """一次调用的费用（元）。缓存命中数是 prompt 总数的一部分。"""
    prompt = max(int(prompt_tokens or 0), 0)
    completion = max(int(completion_tokens or 0), 0)
    # WHY: 供应商偶尔给出不一致的命中数；夹到 [0, prompt]，避免负费用。
    cached = min(max(int(cached_tokens or 0), 0), prompt)
    prices = unit_prices(provider, capabilities, when)
    return ((prompt - cached) * prices["prompt_price"]
            + cached * prices["prompt_cached_price"]
            + completion * prices["completion_price"]) / UNIT


def format_prices(prices: dict) -> str:
    """输入未命中 / 输入命中 / 输出，供 `#model` 显示。"""
    return " / ".join(f"{prices.get(key, 0):.6f}".rstrip("0").rstrip(".") or "0" for key in PRICE_KEYS)
