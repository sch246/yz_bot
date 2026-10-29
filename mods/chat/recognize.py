"""再认柜子里的表情：第一级只看 CQ 的 file 字段，不下载、不调模型。

WHY: 2026-10-28 群里再见到收过的表情时没认出来（“初见失忆”）。QQ 的 `[CQ:image]`
的 `file` 字段就是这份文件的 md5，所以第一级只要读消息、比对柜子索引；`#image off`
也能认出原样重发的图，不违背“不在任何群里到处下载图片”。

WHY: 结论第一次得出就钉死进 storage `meme/labels`，键是「窗口 + 消息 + 图在消息里的
位置」。柜子后来收了这张图、改了她那句话，旧消息的渲染也不变——画面是前缀缓存的一部分，
回头改一个字，缓存就从那里断掉。表里只存短字符串，不跟着本地图片缓存 15 天清理，
否则清掉以后重判，结论又会变。标注不写回 chatlog。

第二、三级（dHash、图像模型）要图片字节，不在这一级；Phase 1 只做 md5。
"""

from __future__ import annotations

import hashlib
import re

from mods import cq, storage

from .windows import label

_MD5 = re.compile(r"[0-9A-F]{32}")
_TABLE = "labels"


def _md5_of(code: str) -> str | None:
    """CQ:image 的 file 字段是 32 位十六进制时，它就是这份文件的 md5。

    花括号临时名（老 gchat.qpic.cn 的 `{FAFDDB49-…}.jpg`）、`file://` 本地路径都不是
    md5，返回 None；这一级对它们没有结论，也就不往表里写。
    """
    try:
        parsed = cq.load(code)
    except ValueError:
        return None
    if parsed["type"] != "image":
        return None
    stem = str(parsed["data"].get("file") or "").rsplit(".", 1)[0].strip("{}").upper()
    return stem if _MD5.fullmatch(stem) else None


def _cabinet() -> dict[str, tuple[str, dict]]:
    """md5 → (编号, 条目)。没有 md5 的老条目跳过，索引仍是唯一的写入权威。"""
    found: dict[str, tuple[str, dict]] = {}
    for meme_id, entry in storage.get("meme", "index").items():
        md5 = str(entry.get("md5") or "").upper()
        if _MD5.fullmatch(md5):
            found.setdefault(md5, (meme_id, entry))
    return found


def _label(meme_id: str, entry: dict) -> str:
    """身份加一句描述：编号给机器认，note 给我自己看着有温度。

    WHY: 2026-10-28 草籽定——只标编号太冷冰冰，几十字的描述他不嫌刷屏；但「可编辑」
    说的是柜子里能改 note（`meme__edit`），不是要重算缓存里钉住的那行。改完只影响以后新到的图。
    """
    note = " ".join(str(entry.get("note") or entry.get("mood") or "").split())
    return f"[表情包: {note} #{meme_id}]" if note else f"[表情包 #{meme_id}]"

def _conclusion(md5: str | None, cabinet: dict[str, tuple[str, dict]]) -> str | None:
    """一张图在这一级的结论：标注、空串（柜里没有）、None（判不了，不钉）。

    WHY: 空串也要钉。今天柜里没有、明天收进来了，那条旧消息的渲染不该跟着变；
    这正是缓存表存在的理由。
    """
    if md5 is None:
        return None
    hit = cabinet.get(md5)
    return _label(*hit) if hit is not None else ""


def _identity(text: str) -> str:
    """一条记录的身份：v1 用消息号，v0 没有消息号就用整条记录的摘要。

    渲染和到达必须算出同一个键，所以消息号从记录头右侧取，和 chatlog 写进去的是同一个值。
    """
    head = text.partition("\n")[0]
    if " | " in head:
        tail = head.rpartition(" | ")[2].strip()
        if tail.isdigit():
            return tail
    return "~" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _remember(table: dict, key: str, md5: str | None, cabinet: dict) -> bool:
    if key in table:
        return False
    conclusion = _conclusion(md5, cabinet)
    if conclusion is None:
        return False
    table[key] = conclusion
    return True


def pin(window: tuple, message_id, message: str) -> None:
    """消息到达时就认出柜中表情并钉死结论，不等第一次渲染。"""
    if not message_id or not isinstance(message, str) or "[CQ:image" not in message:
        return
    cabinet = _cabinet()
    table = storage.get("meme", _TABLE)
    prefix = f"{label(window)}|{message_id}"
    changed = False
    ordinal = 0
    for match in cq.re_CQ.finditer(message):
        if match.group("type") != "image":
            continue
        key = f"{prefix}|{ordinal}"
        ordinal += 1
        if _remember(table, key, _md5_of(match.group(0)), cabinet):
            changed = True
    if changed:
        storage.save()


def _annotate_one(window_label: str, text: str, table: dict, cabinet: dict) -> tuple[str, bool]:
    if "[CQ:image" not in text:
        return text, False
    prefix = f"{window_label}|{_identity(text)}"
    changed = False
    pieces: list[str] = []
    last = 0
    ordinal = 0
    for match in cq.re_CQ.finditer(text):
        if match.group("type") != "image":
            continue
        key = f"{prefix}|{ordinal}"
        ordinal += 1
        if key not in table:
            conclusion = _conclusion(_md5_of(match.group(0)), cabinet)
            if conclusion is None:
                continue
            table[key] = conclusion
            changed = True
        annotation = table[key]
        if not annotation:
            continue
        pieces.append(text[last:match.end()])
        pieces.append(" " + annotation)
        last = match.end()
    if not pieces:
        return text, changed
    pieces.append(text[last:])
    return "".join(pieces), changed


def annotate(window: tuple, texts: list[str]) -> list[str]:
    """给这一屏的每条记录补标注；第一次见到的图当场算结论并钉死。"""
    if not any("[CQ:image" in text for text in texts):
        return texts
    cabinet = _cabinet()
    table = storage.get("meme", _TABLE)
    window_label = label(window)
    changed = False
    result = []
    for text in texts:
        annotated, dirty = _annotate_one(window_label, text, table, cabinet)
        result.append(annotated)
        changed = changed or dirty
    if changed:
        storage.save()
    return result
