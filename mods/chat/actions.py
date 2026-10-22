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


def render(output: dict, result: dict | None, *, chars: int = 1200) -> str:
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(output.get("read_at", 0)))
    lines = [f"[{stamp}] 行动 {output['id']}"]
    assistant = output.get("assistant") or {}
    body = assistant.get("content") or ""
    if isinstance(body, str) and body.strip():
        lines.append("    （自语）" + _short(body.strip(), chars))
    returns = {item.get("tool_call_id"): item for item in (result or {}).get("returns", [])}
    for call in assistant.get("tool_calls") or []:
        function = call.get("function", {})
        lines.append(f"    {function.get('name', '?')}({_short(function.get('arguments', ''), chars)})")
        answer = returns.get(call.get("id"))
        if answer is not None:
            lines.append("      → " + _short(answer.get("content", ""), chars))
        elif result is None:
            lines.append("      → （没有记录到结果）")
    return "\n".join(lines)


def title(output: dict) -> str:
    assistant = output.get("assistant") or {}
    stamp = time.strftime("%m-%d %H:%M", time.localtime(output.get("read_at", 0)))
    names = [call.get("function", {}).get("name", "?") for call in assistant.get("tool_calls") or []]
    said = [json.loads(call["function"].get("arguments") or "{}").get("text", "")
            for call in assistant.get("tool_calls") or []
            if call.get("function", {}).get("name") == "say"
            and _parses(call["function"].get("arguments"))]
    summary = _short(said[0], 40) if said else _short(assistant.get("content") or "", 40)
    return f"{output['id']} [{stamp}] {','.join(names) or '（无工具）'} {summary}".rstrip()


def _parses(arguments) -> bool:
    try:
        return isinstance(json.loads(arguments or "{}"), dict)
    except (TypeError, ValueError):
        return False
