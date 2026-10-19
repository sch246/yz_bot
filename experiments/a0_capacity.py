"""Declared provider capacity reservation; no tokenizer or provider-bill proof."""
from decimal import Decimal, ROUND_CEILING

MODEL = "deepseek/deepseek-flash"
WIRE_MODEL = "deepseek-flash"
ENDPOINT = "https://api.deepseek.com"
LIMITS = {"wakes": 3, "attempts": 12, "output": 4096, "allowances": 49152,
          "context": 1048576, "micro_cny": 5000000}
PRICES = {"currency": "CNY", "unit_tokens": 1000000, "input": "2", "cached": "0.04",
          "output": "8", "extra_max_micro_cny": 0, "basis": "conservative-peak-not-provider-bill"}


def declaration(mode):
    if mode not in {"synthetic", "live"}:
        raise ValueError("unknown capacity mode")
    return {"schema": "a0-capacity-contract-v1", "mode": mode, "model": MODEL,
            "wire_model": WIRE_MODEL, "endpoint": ENDPOINT, "response_model": WIRE_MODEL,
            "policy": "provider-context-cap", "limits": dict(LIMITS), "prices": dict(PRICES),
            "capacity_evidence": {"source": "https://api-docs.deepseek.com/api/list-models/",
                                  "checked_at": "2026-10-19", "applicability": "synthetic fixture only",
                                  "status": "documentation-example-not-live-model-query"},
            "price_evidence": {"source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
                               "checked_at": "2026-10-19", "applicability": "offline numeric snapshot"},
            "failure_billing": "Unverified; application reservation is conditional on provider contract compliance.",
            "request": {"max_tokens": 4096, "thinking": {"type": "enabled"}, "reasoning_effort": "high",
                        "tool_choice": "auto", "stream": True, "stream_options": {"include_usage": True}},
            "timeout_seconds": 120, "cancel": "stop-without-repair", "max_retries": 0,
            "pilot_binding": None,
            "code": {}}


def validate_contract(value, mode, code):
    template = declaration(mode)
    if set(value) != set(template):
        raise ValueError("unknown or missing capacity contract fields")
    for key in ("schema", "mode", "model", "wire_model", "endpoint", "policy", "limits", "prices", "request",
                "timeout_seconds", "cancel", "max_retries"):
        if value[key] != template[key]:
            raise ValueError("capacity/price/model/request policy drift")
    if value["code"] != code:
        raise ValueError("contract code drift")
    for name in ("capacity_evidence", "price_evidence"):
        evidence = value[name]
        required = set(template[name])
        if set(evidence) != required or any(not isinstance(item, str) or not item for item in evidence.values()):
            raise ValueError("missing capacity/price source, time or applicability declaration")
    if not isinstance(value["response_model"], str) or not value["response_model"] or not value["failure_billing"]:
        raise ValueError("missing response identity or failure billing disclosure")
    if mode == "live" and value["capacity_evidence"]["status"] != "operator-declared-target-applicability":
        raise ValueError("live contract must declare target applicability; example is insufficient")
    if mode == "live" and (not isinstance(value["pilot_binding"], dict)
                           or set(value["pilot_binding"]) != {"id", "root", "record"}
                           or any(not isinstance(item, str) or not item for item in value["pilot_binding"].values())):
        raise ValueError("live contract must bind one pilot, root and permanent claim record")
    return value


class CapacityPolicy:
    def __init__(self, contract):
        if contract["mode"] not in {"synthetic", "live"}:
            raise ValueError("unknown capacity mode")
        self.contract = contract
        self.usage_source = "synthetic-sdk-usage" if contract["mode"] == "synthetic" else "provider-usage-conservative-price"

    def validate_prices(self, prices):
        if prices != PRICES:
            raise ValueError("unknown/missing price or extra fee")
        return dict(prices)

    def input_bound(self, request):
        return self.contract["limits"]["context"]

    def usage_exceeds(self, usage):
        return usage is not None and usage[0] + usage[2] > self.contract["limits"]["context"]

    def cost(self, prices, prompt, cached, output, *, reserve=False):
        self.validate_prices(prices)
        if any(type(value) is not int or value < 0 for value in (prompt, cached, output)) or cached > prompt:
            raise ValueError("invalid capacity usage")
        ordinary = max(Decimal(prices["input"]), Decimal(prices["cached"])) if reserve else Decimal(prices["input"])
        amount = ((prompt - cached) * ordinary + cached * Decimal(prices["cached"])
                  + output * Decimal(prices["output"])) * 1000000 / prices["unit_tokens"]
        return int(amount.to_integral_value(rounding=ROUND_CEILING)) + prices["extra_max_micro_cny"]
