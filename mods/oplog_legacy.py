"""Optional read-only interpretation of journal rows predating the central reader."""

from __future__ import annotations

from mods import oplog


def _notification_text(entry: dict) -> dict:
    from mods import chat

    details = entry.get("unread", ())
    shown = []
    for detail in details:
        window = detail["window"]
        target = ("g" if window[0] == "group" else "u") + str(window[1])
        recovery = detail.get("recovery")
        extra = ((f" 补回未读={recovery['remaining']} 补回状态={recovery['state']}"
                  + (f" 缺口={recovery['gap']}" if recovery.get("gap") else ""))
                 if recovery else "")
        line = (f"{target} 未读={detail['unread']} 普通={detail['ordinary']} "
                f"@/提及={detail['mentions']} 其他唤醒={detail['other_wakes']}" + extra)
        if chat.count_tokens("；".join([*shown, line])) > chat.NOTICE_TOKENS - 150:
            break
        shown.append(line)
    if details:
        omitted = len(details) - len(shown)
        listing = "；".join(shown) + (f"；还有 {omitted} 个窗口未列出，用 status() 查看"
                                   if omitted else "")
    else:
        listing = "、".join(f"{window[0]}:{window[1]}" for window in entry["windows"])
    activations = entry.get("activations", ())
    activation_listing = ("；".join(
        f"{('g' if item['window'][0] == 'group' else 'u')}{item['window'][1]} "
        f"{item['kind']} 作者={item.get('user_id')} 时间={item.get('time')}"
        for item in activations) if activations else "旧版通知未记录逐条唤醒")
    content = (f"[{entry['id']}] 新召唤通知（创建时快照，未读序号可能已变化）。当时全部未读唤醒：{activation_listing}。"
               f"未读概况：{listing}。"
               "正文仍在未读信源；普通消息本身不激活。"
               "可用 take(source, start, count) 按执行时未读序号选范围正式阅读，mentions(source) 正式读入未读提及，"
               "read_messages 按 message_id 选择档案。通知已看见不等于消息已读；"
               "未读红点不会自行反复唤醒，之后的新唤醒仍会再次带上这份完整未读集合。")
    return {"role": "user", "content": content}


def translate(entry: dict, state: dict) -> dict:
    """Return an internal row using only facts available before this journal row."""
    kind = entry.get("kind")
    if kind == "source_mark_read" and any(field not in entry for field in
                                           ("positions", "mention_count", "arrivals", "read_by")):
        selected = entry.get("positions")
        if selected is None:
            source = state["sources"][entry["source"]]
            positions = [(page, offset)
                         for page in range(source["pages"] - 1, -1, -1)
                         for offset in range(source["page_counts"][page])
                         if (page, offset) not in source["read_positions"]]
            selected = positions[:entry["count"]]
            if not selected or selected[0] != (entry["page"], entry["offset"]):
                raise ValueError("source mark-read cursor is stale")
        return {**entry, "positions": [list(position) for position in selected],
                "mention_count": entry.get("mention_count", 0),
                "arrivals": entry.get("arrivals", []), "read_by": entry.get("read_by")}
    if kind == "condensed":
        target = state["indexes"][entry["target"]]
        return {"kind": "hide_events", "visibility": "collapsed", "ids": [entry["target"],
                *(item["id"] for item in state["events"]
                  if item["kind"] == "result" and item["source"] == target["id"])]}
    if kind == "clear":
        return {"kind": "hide_events", "visibility": "hidden", "ids": [item["id"] for item in
                state["windows"].get(tuple(entry["window"]), ()) if item["kind"] == "result"]}
    if kind == "floor":
        before = entry["before"]
        if before is not None and before not in state["arrival_order"]:
            raise ValueError("floor arrival does not exist")
        boundary = state["arrival_order"].get(before, -1)
        return {"kind": "drop_arrivals", "arrivals": [arrival for arrival, item in
                state["pending"].items() if state["arrival_order"][arrival] <= boundary
                and item["window"] == entry["window"] and not item.get("fetched")]}
    if kind == "output" and "assistant" not in entry and "body" in entry and "actions" in entry:
        return {**entry, "assistant": None}
    if kind == "notification" and "unread" in entry and (
            entry.get("version") != 2 or "activations" not in entry):
        return {**entry, "projection": _notification_text(entry)}
    if kind == "input" and ("read_by" not in entry or "read_via" not in entry):
        return {**entry, "read_by": entry.get("read_by"), "read_via": entry.get("read_via")}
    if kind == "mark_read" and "read_by" not in entry:
        return {**entry, "read_by": None}
    if kind == "source_start" and "queue_window" not in entry:
        return {**entry, "queue_window": entry["window"]}
    if kind == "source_page" and "mention_count" not in entry:
        return {**entry, "mention_count": 0}
    if kind == "source_finish" and "stop_cursor" not in entry:
        return {**entry, "stop_cursor": state["sources"][entry["source"]]["cursor"]}
    if kind == "activation" and "activation_kind" not in entry:
        return {**entry, "activation_kind": "wake"}
    raise ValueError("unsupported legacy event stream row")


oplog.register_legacy_adapter(translate)
