"""一次 LLM 调用的计费：输入命中/未命中与输出分开计价，并按峰谷时段取价。

价格写在模型能力里（`models.py` 的默认目录或 storage 里的 `llm_system/config`），单位是
人民币元/百万 token，一律写**高峰时段**价；空闲时段的价格由 provider 的 `off_peak` 规则
推出（默认 ×0.5）。于是峰谷表变动只需要改一处，模型价不必跟着翻倍或减半。

`off_peak` 描述的是"高峰在什么时候"，不是"什么时候打折"：

    "off_peak": {"timezone": "Asia/Shanghai",
                 "days": [0, 1, 2, 3, 4],                        # 0=周一，与 datetime.weekday() 同序
                 "windows": [["09:00", "12:00"], ["14:00", "18:00"]],   # 左闭右开
                 "ratio": 0.5}                                    # 空闲价 = 高峰价 × ratio

没有 `off_peak` 的 provider 全天按高峰价。`days` 缺省是每天、`windows` 缺省是"没有高峰
窗口"（于是整天空闲），所以写规则时该写的都要写出来，别指望默认值替配置说话。
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


# 三个价格键的顺序就是 `#model` 里的显示顺序：输入未命中 / 输入命中 / 输出。
PRICE_KEYS = ("prompt_price", "prompt_cached_price", "completion_price")
# 价格的单位：元/(百万 token)。
UNIT = 1_000_000
DEFAULT_OFF_PEAK_RATIO = 0.5
_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value) -> float:
    return float(value) if _is_number(value) else 0.0


def _zone(name) -> timezone | ZoneInfo:
    """配置里的时区名 → tzinfo；认不出就用 UTC。

    WHY: 时区名打错是配置错误，而这里是每次调用都会走的计费路径——为它抛异常等于让
    一次配置笔误掐断聊天。宁可把时段判错一档，并在 `#models` 里把规则原样显示出来让人
    自己看见。设备时区不用：计费口径要跟着供应商的公告走，而不是跟着宿主机跑。
    """
    if isinstance(name, str) and name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def _rule(provider) -> dict | None:
    """provider 配置里的 `off_peak` 规则；没有或形状不对就当没有。"""
    value = provider.get("off_peak") if isinstance(provider, dict) else None
    return value if isinstance(value, dict) else None


def _clock(value) -> time | None:
    """``"HH:MM"`` / ``"HH:MM:SS"`` → `datetime.time`；认不出返回 None。"""
    if not isinstance(value, str):
        return None
    try:
        numbers = [int(part) for part in value.strip().split(":")]
    except ValueError:
        return None
    if len(numbers) == 2:
        numbers.append(0)
    if len(numbers) != 3:
        return None
    hour, minute, second = numbers
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        return None
    return time(hour, minute, second)


def off_peak_ratio(provider) -> float:
    """空闲时段的价格乘数。没有规则时也返回它，但不会被用到。"""
    rule = _rule(provider)
    value = rule.get("ratio") if rule else None
    if not _is_number(value) or value < 0:
        return DEFAULT_OFF_PEAK_RATIO
    return float(value)


def is_off_peak(provider, when: datetime | None = None) -> bool:
    """*when*（默认此刻）是否落在 provider 的空闲时段。

    判据只有一条：不在 `days` 的 `windows` 之内就是空闲，所以规则写的是高峰时段本身。
    naive 的 *when* 按规则里的时区理解，aware 的先换算过去。
    """
    rule = _rule(provider)
    if rule is None:
        return False
    zone = _zone(rule.get("timezone"))
    moment = when if when is not None else datetime.now(zone)
    moment = moment.replace(tzinfo=zone) if moment.tzinfo is None else moment.astimezone(zone)
    days = rule.get("days")
    if isinstance(days, list) and days and moment.weekday() not in [day for day in days if isinstance(day, int)]:
        return True
    windows = rule.get("windows")
    for window in windows if isinstance(windows, list) else []:
        if not (isinstance(window, (list, tuple)) and len(window) == 2):
            continue
        start, end = _clock(window[0]), _clock(window[1])
        if start is None or end is None:
            continue
        if start <= moment.time() < end:
            return False
    return True


def unit_prices(provider, capabilities, when: datetime | None = None) -> dict:
    """三项单价（元/百万 token），键名就是 `PRICE_KEYS`。

    `prompt_cached_price` 缺省跟随 `prompt_price`，而不是缺省成 0：整条模型没登记时
    三项都是 0（少算），但只登记了输入/输出价时，把命中缓存当成免费会在长上下文里静默
    少算一大截——聊天的 prompt 绝大多数是重复上下文，正是命中那部分。反过来，显式写 0
    （真有供应商缓存命中不计费）就照 0 算，两者不能混为一谈。
    """
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    prices = {key: _number(capabilities.get(key)) for key in PRICE_KEYS}
    if not _is_number(capabilities.get("prompt_cached_price")):
        prices["prompt_cached_price"] = prices["prompt_price"]
    if is_off_peak(provider, when):
        factor = off_peak_ratio(provider)
        prices = {key: value * factor for key, value in prices.items()}
    return prices


def token_cost(provider, capabilities, prompt_tokens: int = 0, completion_tokens: int = 0, cached_tokens: int = 0, when: datetime | None = None) -> float:
    """一次调用的费用（元）。`cached_tokens` 是 prompt 里命中缓存的那部分。

    WHY: 命中数按 prompt 总数夹一次——供应商偶尔给出对不上的命中数，不夹的话"未命中"
    会变成负数，把这一笔记成负费用。
    """
    prompt = max(int(prompt_tokens or 0), 0)
    completion = max(int(completion_tokens or 0), 0)
    cached = min(max(int(cached_tokens or 0), 0), prompt)
    prices = unit_prices(provider, capabilities, when)
    return (
        (prompt - cached) * prices["prompt_price"]
        + cached * prices["prompt_cached_price"]
        + completion * prices["completion_price"]
    ) / UNIT


def _text(value: float) -> str:
    """价格显示：去掉多余的零，而不是科学计数法（配置里的价格都是可读的小数）。"""
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def format_prices(prices: dict) -> str:
    """三个价格排成 `#model` 里的一格：输入未命中 / 输入命中 / 输出。"""
    return " / ".join(_text(prices.get(key, 0.0)) for key in PRICE_KEYS)


def _days_text(days) -> str:
    ordered = sorted({day for day in days if isinstance(day, int) and 0 <= day <= 6}) if isinstance(days, list) else []
    if len(ordered) in (0, 7):
        return "每天"
    runs: list[list[int]] = []
    for day in ordered:
        if runs and day == runs[-1][-1] + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    texts = []
    for run in runs:
        if len(run) == 1:
            texts.append(_WEEKDAYS[run[0]])
        elif len(run) == 2:
            texts.extend((_WEEKDAYS[run[0]], _WEEKDAYS[run[1]]))
        else:
            texts.append(f"{_WEEKDAYS[run[0]]}至{_WEEKDAYS[run[-1]]}")
    return "、".join(texts)


def describe_off_peak(provider, when: datetime | None = None) -> str | None:
    """`#models` 末尾那句峰谷说明；provider 没有 `off_peak` 规则时返回 None。

    显示规则本身（时段与乘数）和**此刻**落在哪一档，前者用来核对配置，后者用来解释
    眼前这一笔为什么按这个价算。
    """
    rule = _rule(provider)
    if rule is None:
        return None
    windows = rule.get("windows")
    clocks = []
    for window in windows if isinstance(windows, list) else []:
        if not (isinstance(window, (list, tuple)) and len(window) == 2):
            continue
        start, end = _clock(window[0]), _clock(window[1])
        if start is not None and end is not None:
            clocks.append(f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
    window_text = "、".join(clocks) if clocks else "（未配 windows，整天空闲）"
    state = "空闲" if is_off_peak(provider, when) else "高峰"
    return (
        f"峰谷：{_days_text(rule.get('days'))} {window_text} 为高峰"
        f"（{rule.get('timezone') or 'UTC'}），空闲价 = 高峰 ×{_text(off_peak_ratio(provider))}；"
        f"当前按{state}价计"
    )


__all__ = [
    "PRICE_KEYS",
    "UNIT",
    "DEFAULT_OFF_PEAK_RATIO",
    "is_off_peak",
    "unit_prices",
    "token_cost",
    "format_prices",
    "describe_off_peak",
    "off_peak_ratio",
]
