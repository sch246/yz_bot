"""每次 LLM 调用的真实用量账本：按天一个 jsonl，滚动保留 30 天。

WHY: 归档里每段的 tokens 是按字数估出来的，用来看"这段有多长"够了，用来看
"哪次调用真贵、上下文是怎么长起来的"不够。响应里带回来的 usage 才是真的，
而且缓存命中、输出 token 这些只有 API 报得准。一天几十条，三十天几千行，
画图时按天取就够，不用数据库。
"""

from __future__ import annotations

import json
import os
import threading
import time

KEEP_DAYS = 30

_lock = threading.Lock()
_last_purge = [0.0]
_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "usage")


def _dir() -> str:
    os.makedirs(_root, exist_ok=True)
    return _root


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def log_call(model: str, prompt_tokens: int = 0, completion_tokens: int = 0, cached_tokens: int = 0,
             requested_at=None, window=None) -> None:
    """记一条调用。

    WHY: 记账失败绝不能把一次对话弄崩——写盘出问题就吞掉，顶多少一条统计。
    """
    try:
        now = requested_at.timestamp() if hasattr(requested_at, "timestamp") else time.time()
        row = {
            "t": round(now, 3),
            "model": model,
            "prompt": int(prompt_tokens or 0),
            "completion": int(completion_tokens or 0),
            "cached": int(cached_tokens or 0),
        }
        if window:
            row["window"] = list(window)
        line = json.dumps(row, ensure_ascii=False)
        path = os.path.join(_dir(), _day(now) + ".jsonl")
        with _lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        purge()
    except Exception:
        return


def purge(keep_days: int = KEEP_DAYS) -> int:
    """删掉太老的日文件；一天最多扫一次。返回删了几个。"""
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if _last_purge[0] == today:
            return 0
        _last_purge[0] = today
    removed = 0
    cutoff = time.time() - keep_days * 86400
    try:
        for name in os.listdir(_dir()):
            if not name.endswith(".jsonl") or len(name) != 16:
                continue
            try:
                day = time.mktime(time.strptime(name[:10], "%Y-%m-%d"))
            except ValueError:
                continue
            if day < cutoff:
                try:
                    os.remove(os.path.join(_root, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return removed


def read(days: int = 7) -> list:
    """按时间顺序读出最近 days 天的记录（画图、核对用）。"""
    cutoff = time.time() - days * 86400
    rows = []
    try:
        for name in sorted(os.listdir(_dir())):
            if not name.endswith(".jsonl"):
                continue
            try:
                if time.mktime(time.strptime(name[:10], "%Y-%m-%d")) < cutoff - 86400:
                    continue
            except ValueError:
                continue
            with open(os.path.join(_root, name), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("t", 0) >= cutoff:
                        rows.append(row)
    except OSError:
        pass
    rows.sort(key=lambda row: row.get("t", 0))
    return rows


__all__ = ["log_call", "purge", "read", "KEEP_DAYS"]
