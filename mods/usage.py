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


def log_request(row: dict) -> None:
    """记一条请求组成或它的 usage，进 ``requests/`` 子目录的日文件。

    WHY: 2026-11-06 维护者：上下文的组成没法监测、没法追踪，前缀缓存在哪里断、换了
    哪份材料，事后都说不清；11-02 的缓存调查因此只能比可见文字，给不出逐轮前缀。这里
    每次实际派发记一行（各条消息的哈希与来源，不存正文），usage 回来再记一行，按
    ctx 和请求序号对上。和上面的账一样，写盘失败不能弄崩对话。
    """
    _append("requests", row)


def log_recall(row: dict) -> None:
    """记一条笔记召回：一次唤醒有哪些提示，或这次唤醒里她第一次用到某份笔记。

    WHY: 2026-11-07 维护者：长期记忆的问题是“需要的时候能不能触发”，提醒本身又会占
    注意力、成为偏向。所以相近笔记的候选先只记不给她看（construct.SHOW_SIMILAR），
    面包屑没点到、她却翻开了的笔记记下当时重合的词，夜里反思时给她当别名候选。
    这两件事都靠这份记录对账；和请求组成一样只是日志，写盘失败不能弄崩对话。
    """
    _append("recall", row)


def read_recall(since: float) -> list[dict]:
    """``recall/`` 里时间不早于 since 的行，按时间顺序。"""
    directory = os.path.join(_root, "recall")
    rows = []
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".jsonl"))
    except OSError:
        return rows
    first = _day(since)
    for name in names:
        if name[:10] < first:
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and float(row.get("t") or 0) >= since:
                        rows.append(row)
        except OSError:
            continue
    return rows


def _append(subdirectory: str, row: dict) -> None:
    try:
        now = float(row.get("t") or time.time())
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        directory = os.path.join(_dir(), subdirectory)
        os.makedirs(directory, exist_ok=True)
        with _lock:
            with open(os.path.join(directory, _day(now) + ".jsonl"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        purge()
    except Exception:
        return


def purge(keep_days: int = KEEP_DAYS) -> int:
    """删掉太老的日文件（账本、``requests/`` 和 ``recall/``）；一天最多扫一次。返回删了几个。"""
    today = time.strftime("%Y-%m-%d")
    with _lock:
        if _last_purge[0] == today:
            return 0
        _last_purge[0] = today
    removed = 0
    cutoff = time.time() - keep_days * 86400
    for directory in (_dir(), os.path.join(_root, "requests"), os.path.join(_root, "recall")):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".jsonl") or len(name) != 16:
                continue
            try:
                day = time.mktime(time.strptime(name[:10], "%Y-%m-%d"))
            except ValueError:
                continue
            if day < cutoff:
                try:
                    os.remove(os.path.join(directory, name))
                    removed += 1
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


def _iter_jsonl_rows(directory: str, since: float | None = None) -> list[dict]:
    """Read jsonl dict rows under *directory* without creating missing directories."""
    if not os.path.isdir(directory):
        return []
    rows = []
    first_day = _day(since - 86400) if since is not None else None
    try:
        names = sorted(name for name in os.listdir(directory) if name.endswith(".jsonl"))
    except OSError:
        return rows
    for name in names:
        if first_day is not None and name[:10] < first_day:
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if since is not None and float(row.get("t") or 0) < since:
                        continue
                    rows.append(row)
        except OSError:
            continue
    return rows


def _latest_write(directory: str) -> tuple[str | None, float | None]:
    """Return (YYYY-MM-DD, latest_timestamp) for the newest jsonl file in *directory*."""
    if not os.path.isdir(directory):
        return None, None
    try:
        names = sorted(
            name for name in os.listdir(directory)
            if name.endswith(".jsonl") and len(name) == 16
        )
    except OSError:
        return None, None
    for name in reversed(names):
        path = os.path.join(directory, name)
        latest_t: float | None = None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and row.get("t") is not None:
                        try:
                            val = float(row["t"])
                        except (TypeError, ValueError):
                            continue
                        if latest_t is None or val > latest_t:
                            latest_t = val
            if latest_t is None:
                latest_t = os.path.getmtime(path)
            return name[:10], latest_t
        except OSError:
            continue
    return None, None


def _is_error_row(row: dict) -> tuple[bool, bool]:
    """Return (is_error, is_400) for explicit error metadata in a request log row."""
    status = row.get("status_code") if row.get("status_code") is not None else row.get("status")
    try:
        status_int = int(status) if status is not None else 0
    except (TypeError, ValueError):
        status_int = 0
    err = row.get("error") or row.get("failure")
    is_err = bool(err) or status_int >= 400
    is_400 = status_int == 400 or ("400" in str(err or ""))
    return is_err, is_400


def signals(days: int = 7) -> str:
    """只读扫描近 *days* 天 ``data/usage/``（含 ``requests/`` 与 ``recall/``），输出四类工程信号。

    仅统计次数、token/字数极值、缓存率与轮数等客观指标，不读取也不输出任何聊天正文或身份标识。
    """
    from collections import Counter, defaultdict

    days = max(1, int(days))
    now = time.time()
    cutoff = now - days * 86400
    requests_dir = os.path.join(_root, "requests")
    recall_dir = os.path.join(_root, "recall")

    # 1. 沉默/断更的数据流 & 各运行 kind 每日调用数
    stream_lines = []
    for label, directory in (("usage(主账本)", _root), ("usage/requests", requests_dir), ("usage/recall", recall_dir)):
        last_day, last_ts = _latest_write(directory)
        if last_day is None or last_ts is None:
            stream_lines.append(f"  - {label}: 无写入记录")
        else:
            idle_days = max(0.0, (now - last_ts) / 86400)
            stream_lines.append(f"  - {label}: 最近写入 {last_day}（距今 {idle_days:.1f} 天无新写入）")

    req_rows = _iter_jsonl_rows(requests_dir, since=cutoff)
    dispatches: dict[tuple[str, int], dict] = {}
    usages: dict[tuple[str, int], dict] = {}
    ctx_dispatches: dict[str, list[dict]] = defaultdict(list)
    daily_kind: dict[str, Counter] = defaultdict(Counter)
    explicit_errors = 0
    explicit_400 = 0

    for row in req_rows:
        is_err, is_400 = _is_error_row(row)
        if is_err:
            explicit_errors += 1
        if is_400:
            explicit_400 += 1
        ctx = str(row.get("ctx") or "")
        n_raw = row.get("n")
        try:
            n = int(n_raw) if n_raw is not None else 0
        except (TypeError, ValueError):
            n = 0
        if "messages" in row or ("kind" in row and "usage" not in row):
            kind = str(row.get("kind") or "unknown")
            day = _day(float(row.get("t") or now))
            daily_kind[day][kind] += 1
            if ctx:
                dispatches[(ctx, n)] = row
                ctx_dispatches[ctx].append(row)
        elif "usage" in row and ctx:
            usages[(ctx, n)] = row

    daily_lines = []
    for day in sorted(daily_kind):
        parts = ", ".join(f"{k}={c}" for k, c in sorted(daily_kind[day].items()))
        daily_lines.append(f"  - {day}: {parts}")
    if not daily_lines:
        daily_lines.append("  - （近期内无 requests 派发记录）")

    # 2. 首轮大请求（同一 ctx 首次派发 prompt tokens 或字符数 >= 100_000）
    big_threshold = 100_000
    big_by_kind: Counter = Counter()
    big_count = 0
    max_first_prompt = 0
    max_first_prompt_kind = ""
    max_first_chars = 0
    max_first_chars_kind = ""

    for ctx, rows in ctx_dispatches.items():
        first_row = min(rows, key=lambda r: int(r.get("n") or 0))
        try:
            first_n = int(first_row.get("n") or 0)
        except (TypeError, ValueError):
            first_n = 0
        if first_n != 1:
            continue
        kind = str(first_row.get("kind") or "unknown")
        chars = sum(
            int(m[3]) for m in (first_row.get("messages") or [])
            if isinstance(m, (list, tuple)) and len(m) >= 4 and m[3] is not None
        )
        u_row = usages.get((ctx, first_n))
        u_data = (u_row.get("usage") or {}) if u_row else {}
        prompt_tokens = int(u_data.get("prompt_tokens") or 0)
        if prompt_tokens >= big_threshold or chars >= big_threshold:
            big_count += 1
            big_by_kind[kind] += 1
            if prompt_tokens > max_first_prompt:
                max_first_prompt = prompt_tokens
                max_first_prompt_kind = kind
            if chars > max_first_chars:
                max_first_chars = chars
                max_first_chars_kind = kind

    big_kind_text = "，".join(f"{k}={c}" for k, c in big_by_kind.most_common()) if big_by_kind else "无"
    big_summary = (
        f"共 {big_count} 次（按 kind：{big_kind_text}）；"
        f"最大 prompt_tokens={max_first_prompt:,}" + (f"（{max_first_prompt_kind}）" if max_first_prompt_kind else "")
        + f"，最大 chars={max_first_chars:,}" + (f"（{max_first_chars_kind}）" if max_first_chars_kind else "")
    )

    # 3. 低缓存命中模型与报错（调用 >= 5 次且命中率 < 5%，以及失败/400 计数）
    ledger_rows = _iter_jsonl_rows(_root, since=cutoff)
    model_stats: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # calls, prompt, cached
    for row in ledger_rows:
        model = str(row.get("model") or "unknown")
        model_stats[model][0] += 1
        model_stats[model][1] += int(row.get("prompt") or 0)
        model_stats[model][2] += int(row.get("cached") or 0)

    low_cache = []
    for model, (calls, prompt_sum, cached_sum) in sorted(model_stats.items()):
        if calls >= 5 and prompt_sum > 0 and (cached_sum / prompt_sum) < 0.05:
            low_cache.append(f"{model}（{calls} 次，缓存命中 {cached_sum / prompt_sum * 100:.2f}%）")
    unpaired = sum(
        1 for key, d_row in dispatches.items()
        if key not in usages and (now - float(d_row.get("t") or now)) > 60
    )

    # 4. 单轮高频工具循环（同一 ctx 连续派发 > 8 轮）
    loop_count = 0
    loop_max_rounds = 0
    loop_by_kind: Counter = Counter()
    for ctx, rows in ctx_dispatches.items():
        rounds = max([int(r.get("n") or 0) for r in rows] + [len(rows)])
        if rounds > 8:
            loop_count += 1
            loop_by_kind[str(rows[0].get("kind") or "unknown")] += 1
            if rounds > loop_max_rounds:
                loop_max_rounds = rounds
    loop_kind_text = "，".join(f"{k}={c}" for k, c in loop_by_kind.most_common()) if loop_by_kind else "无"

    return "\n".join([
        f"## usage 工程信号（近 {days} 天）",
        "1. 数据流写入与每日调用（requests 按 kind）：",
        *stream_lines,
        *daily_lines,
        f"2. 首轮大请求（>= 100,000 prompt tokens 或 chars）：{big_summary}",
        "3. 低缓存命中模型（>= 5 次且 < 5%）与报错：",
        f"  - 低缓存模型：{'；'.join(low_cache) if low_cache else '无'}",
        f"  - 失败/400 记录：显式报错 {explicit_errors} 次（含 400 共 {explicit_400} 次），未回报 usage 派发 {unpaired} 次",
        f"4. 单轮高频工具循环（同一 ctx > 8 轮）：共 {loop_count} 次运行（按 kind：{loop_kind_text}），最大 {loop_max_rounds} 轮",
    ])


__all__ = ["log_call", "purge", "read", "signals", "KEEP_DAYS"]
