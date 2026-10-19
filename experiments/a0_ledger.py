"""Durable, single-flight synthetic calibration accounting; never a real quote."""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import time


LIMITS = {"wakes": 3, "attempts": 12, "output": 4096, "allowances": 49152,
          "context": 262144, "micro_cny": 5000000}
# WHY: integer micro-CNY avoids floating-point boundary credit; these invented
# rates exercise the guard, and must never be mistaken for provider pricing.
PRICES = {"currency": "CNY", "unit_tokens": 1000000, "input": "1",
          "cached": "0.25", "output": "2", "extra_max_micro_cny": 0,
          "synthetic": True}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def durable(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".new")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(canonical(value).decode() + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def price_table(prices: dict) -> dict:
    if (set(prices) != set(PRICES) or prices.get("currency") != "CNY"
            or prices.get("synthetic") is not True
            or type(prices.get("unit_tokens")) is not int or prices["unit_tokens"] <= 0
            or type(prices.get("extra_max_micro_cny")) is not int
            or prices["extra_max_micro_cny"] < 0):
        raise ValueError("missing synthetic price or unknown extra fee")
    for key in ("input", "cached", "output"):
        value = Decimal(prices[key])
        if not value.is_finite() or value < 0:
            raise ValueError("invalid price")
    return dict(prices)


def cost(prices: dict, prompt: int, cached: int, output: int, *, reserve=False) -> int:
    if any(type(value) is not int or value < 0 for value in (prompt, cached, output)) or cached > prompt:
        raise ValueError("invalid usage")
    table = price_table(prices)
    ordinary_rate = Decimal(table["input"])
    cached_rate = Decimal(table["cached"])
    if reserve:
        ordinary_rate = max(ordinary_rate, cached_rate)
    amount = ((prompt - cached) * ordinary_rate + cached * cached_rate
              + output * Decimal(table["output"])) * 1000000 / table["unit_tokens"]
    return int(amount.to_integral_value(rounding=ROUND_CEILING)) + table["extra_max_micro_cny"]


class Ledger:
    def __init__(self, root: Path, pilot: str, manifest_hash: str, *, limits=None, prices=None,
                 reopen=False, policy=None):
        self.root = root
        self.policy = policy
        self.limits = dict(LIMITS if limits is None else limits)
        if (set(self.limits) != set(LIMITS) or
                any(type(value) is not int or value <= 0 for value in self.limits.values())):
            raise ValueError("all limits including explicit context must be positive integers")
        self.prices = (price_table(PRICES if prices is None else prices) if policy is None
                       else policy.validate_prices(prices))
        self.poisoned = False
        if reopen:
            if (root / "persistence-unknown.json").exists() or (root / "ledger.json.new").exists():
                raise ValueError("persistence result unknown; ledger cannot resume")
            envelope = json.loads((root / "ledger.json").read_text())
            self.state = envelope["state"]
            if envelope["sha256"] != digest(self.state):
                raise ValueError("corrupt ledger checksum")
            self.validate()
            if (self.state["pilot"] != pilot or self.state["manifest"] != manifest_hash
                    or self.state["limits"] != self.limits or self.state["prices"] != self.prices):
                raise ValueError("ledger identity/price/limit drift")
            if self.state["closed"] or any(row["status"] == "reserved" for row in self.state["requests"]):
                raise ValueError("stopped or unknown in-flight ledger cannot resume")
        else:
            if (root / "ledger.json").exists():
                raise ValueError("pilot ledger already exists; budget cannot reset")
            self.state = {"schema": 1, "pilot": pilot, "manifest": manifest_hash,
                          "limits": self.limits, "prices": self.prices,
                          "wakes": 0, "closed": "", "requests": []}
            self.save()

    def validate(self):
        state = self.state
        if (set(state) != {"schema", "pilot", "manifest", "limits", "prices", "wakes", "closed", "requests"}
                or state["schema"] != 1 or type(state["wakes"]) is not int
                or not 0 <= state["wakes"] <= self.limits["wakes"]
                or not isinstance(state["closed"], str) or not isinstance(state["requests"], list)):
            raise ValueError("corrupt ledger structure")
        for index, row in enumerate(state["requests"], 1):
            if (row["id"] != f"{state['pilot']}/{index:04d}"
                    or row["status"] not in ("reserved", "settled")
                    or type(row["input_bound"]) is not int or row["input_bound"] <= 0
                    or not 0 < row["allowance"] <= self.limits["output"]
                    or row["reserved"] != self.cost(row["input_bound"], 0, row["allowance"], reserve=True)):
                raise ValueError("corrupt request reservation")
            if row["status"] == "settled":
                charged = (row["reserved"] if row["usage"] is None else
                           self.cost(*row["usage"]))
                if row["charged"] != charged:
                    raise ValueError("corrupt settled charge")
        if (len(state["requests"]) > self.limits["attempts"]
                or sum(row["allowance"] for row in state["requests"]) > self.limits["allowances"]
                or sum(row["status"] == "reserved" for row in state["requests"]) > 1):
            raise ValueError("corrupt cumulative ledger")

    def save(self):
        try:
            durable(self.root / "ledger.json", {"state": self.state, "sha256": digest(self.state)})
        except BaseException:
            self.poisoned = True
            # WHY: the last durable reserve is the authority when fsync fails.
            # This marker is best effort evidence, never a claim of durability.
            try:
                with (self.root / "persistence-unknown.json").open("x") as stream:
                    json.dump({"status": "persistence-unknown", "resume": "unsupported"}, stream)
            except OSError:
                pass
            raise

    def cost(self, prompt, cached, output, *, reserve=False):
        if self.policy is None:
            return cost(self.prices, prompt, cached, output, reserve=reserve)
        return self.policy.cost(self.prices, prompt, cached, output, reserve=reserve)

    def guard(self):
        if self.poisoned or self.state["closed"]:
            raise RuntimeError("pilot closed: " + (self.state["closed"] or "persistence-unknown"))
        if any(row["status"] == "reserved" for row in self.state["requests"]):
            raise RuntimeError("one request is already in flight")

    def wake(self):
        self.guard()
        if self.state["wakes"] >= self.limits["wakes"]:
            raise RuntimeError("external wake limit")
        self.state["wakes"] += 1
        self.save()

    def reserve(self, request: dict, allowance: int) -> dict:
        self.guard()
        if type(allowance) is not int or not 0 < allowance <= self.limits["output"]:
            raise RuntimeError("output allowance limit")
        bound = len(canonical(request)) + 1024 if self.policy is None else self.policy.input_bound(request)
        if bound > self.limits["context"]:
            raise RuntimeError("explicit input context limit")
        rows = self.state["requests"]
        if len(rows) >= self.limits["attempts"]:
            raise RuntimeError("actual attempt limit")
        if sum(row["allowance"] for row in rows) + allowance > self.limits["allowances"]:
            raise RuntimeError("cumulative allowance limit")
        reserved = self.cost(bound, 0, allowance, reserve=True)
        if sum(row.get("charged", row["reserved"]) for row in rows) + reserved > self.limits["micro_cny"]:
            raise RuntimeError("cumulative CNY limit")
        row = {"id": f"{self.state['pilot']}/{len(rows) + 1:04d}", "status": "reserved",
               "request_sha256": digest(request), "input_bound": bound,
               "allowance": allowance, "reserved": reserved, "requested_at": time.time()}
        rows.append(row)
        self.save()
        return row

    def settle(self, row: dict, usage: list[int] | None, outcome="stop"):
        if self.poisoned or row is not self.state["requests"][-1] or row["status"] != "reserved":
            raise RuntimeError("settlement identity/state mismatch")
        charged = row["reserved"] if usage is None else self.cost(*usage)
        over = ((usage is not None and (usage[0] > row["input_bound"] or usage[2] > row["allowance"]))
                or charged > row["reserved"] or (self.policy is not None and self.policy.usage_exceeds(usage)))
        row.update(status="settled", usage=usage, charged=charged,
                   usage_source="full-reserve" if usage is None else ("fake-transport" if self.policy is None else self.policy.usage_source),
                   outcome=outcome, settled_at=time.time())
        if over or outcome != "stop" or usage is None:
            self.state["closed"] = "usage-overreport" if over else ("missing-usage" if usage is None else outcome)
        self.save()
        if self.state["closed"]:
            raise RuntimeError("pilot closed: " + self.state["closed"])

    def stop(self, reason: str):
        self.state["closed"] = reason
        self.save()
