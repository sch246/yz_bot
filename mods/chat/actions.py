"""Render the agent's own past actions as readable records.

Each output and the result batch that answered it become one record shaped like
a chatlog entry: an unindented head with time and id, an indented body.  The
oplog stays the write authority; this is only how the model and the librarian
read it back.
"""

from __future__ import annotations

import json
import time

from mods import oplog


def _window():
    from mods import chat

    return chat.AGENT_WINDOW


def _short(value, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split()) if limit < 200 else text
    return text if len(text) <= limit else text[:limit] + f"…（共 {len(text)} 字）"


def pairs(*, ids=None, after: float | None = None) -> list[tuple[dict, dict | None]]:
    """Outputs in order, each with the result that answered it."""
    entries = oplog.events(_window(), include_condensed=True)
    results = {entry["source"]: entry for entry in entries if entry["kind"] == "result"}
    wanted = set(ids) if ids is not None else None
    found = []
    for entry in entries:
        if entry["kind"] != "output":
            continue
        if wanted is not None and entry["id"] not in wanted:
            continue
        if after is not None and entry.get("read_at", 0) < after:
            continue
        found.append((entry, results.get(entry["id"])))
    return found


def _speech(output: dict) -> tuple[str, list[dict]]:
    """Body and calls of one output.

    WHY: 旧日志翻译过来的输出可能没有 `assistant`，只有 `body` 与 `actions`
    （name/arguments，没有调用号）；生产日志里两种都有，读回时一并认。
    """
    assistant = output.get("assistant")
    if isinstance(assistant, dict):
        calls = [{"id": call.get("id"), "name": call.get("function", {}).get("name", "?"),
                  "arguments": call.get("function", {}).get("arguments", "")}
                 for call in assistant.get("tool_calls") or []]
        return str(assistant.get("content") or ""), calls
    calls = [{"id": None, "name": action.get("name", "?"), "arguments": action.get("arguments", "")}
             for action in output.get("actions") or []]
    return str(output.get("body") or ""), calls


def render(output: dict, result: dict | None, *, chars: int = 1200) -> str:
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(output.get("read_at", 0)))
    lines = [f"[{stamp}] 行动 {output['id']}"]
    body, calls = _speech(output)
    if body.strip():
        lines.append("    （自语）" + _short(body.strip(), chars))
    returns = (result or {}).get("returns", [])
    by_id = {item.get("tool_call_id"): item for item in returns if item.get("tool_call_id")}
    by_position = {item.get("position"): item for item in returns}
    for position, call in enumerate(calls):
        lines.append(f"    {call['name']}({_short(call['arguments'], chars)})")
        answer = by_id.get(call["id"]) if call["id"] else None
        if answer is None:
            answer = by_position.get(position)
        if answer is not None:
            lines.append("      → " + _short(answer.get("content", ""), chars))
        elif result is None:
            lines.append("      → （没有记录到结果）")
    return "\n".join(lines)


def title(output: dict) -> str:
    stamp = time.strftime("%m-%d %H:%M", time.localtime(output.get("read_at", 0)))
    body, calls = _speech(output)
    said = [parse_arguments(call["arguments"]).get("text", "") for call in calls if call["name"] == "say"]
    summary = _short(str(said[0]), 40) if said else _short(body, 40)
    return f"{output['id']} [{stamp}] {','.join(call['name'] for call in calls) or '（无工具）'} {summary}".rstrip()


def parse_arguments(arguments) -> dict:
    try:
        value = json.loads(arguments or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
