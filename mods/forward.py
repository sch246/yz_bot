"""合并转发消息的取回、落盘与渲染，以及发送用节点的构造。

合并转发在事件里只是一串卡片文字 `[CQ:forward,id=…]`，真正的节点要拿这个 id 去问
OneBot（`get_forward_msg`）。两个观察决定了这个模块的形状：

1. **id 会过期，图片 url 也会过期。** 旧转发报"消息已过期或者为内层消息"，节点里的图片
   带 `rkey`、几天后同样失效。所以取回来的东西落到 `data/forward/<id>.json`，图片顺手
   本地化（走 `cq.save_pic` 那条路）——`.cave` 里存下的转发因此几天后还读得出来，而不是
   留下一串取不回来的 id。
2. **取回是尽力而为。** 除了过期，还有"回包 retcode 0 但 messages 为空"这种稳定失败。
   取不到就原样留着那串 CQ，任何调用方都不因为它出错。这不是回退得漂亮，是承认
   "取不到"本身就是要留给人的事实。

发送方向不需要取回：`send_forward_msg` / `send_private_forward_msg` 收的是节点数组，
`build` 把几行文本编成节点，`deliver` 按窗口选 action。节点里的正文照样是 CQ 文本，
所以图片、at 这些写法和普通消息一致。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import quote

from mods import connect, cq, identity


_log = logging.getLogger(__name__)

# 落盘位置。一个 id 一个文件，和 chatlog、data/images 一样是"人能直接看、能备份、能删"
# 的形状；转发很少，不值得为它建索引。
DIR = Path("data/forward")
# 一次渲染最多展开多少条节点。转发可以很长，全摊开会把模型上下文挤掉；要看全部就调大
# limit，而不是指望默认值兜住。
MAX_NODES = 30
# 嵌套转发的展开深度，与 NapCat 自己发送转发时的 3 层上限对齐。
MAX_DEPTH = 3

# 入站的转发是 [CQ:forward,id=…]；而**自己发出去的**那条，get_msg 里是一张
# com.tencent.multimsg 的 json 卡片，它的 meta.detail.resid 才是同一个转发 id。
# 两种形状都要认，否则"我发出去的那条转发"在日志和上下文里就只是一坨 json 噪声。
_card = re.compile(r"\[CQ:(?:forward|json),[^\]]*\]")
_multimsg_app = "com.tencent.multimsg"
_image = re.compile(r"\[CQ:image,[^\]]*\]")

# 取回来的记录按 id 留在内存里：同一轮上下文里一条转发会被转换很多次（预算计算、
# 真正组装各一次），每次都读盘没有必要。
_records: dict[str, dict] = {}


def _json_id(parsed: dict) -> str:
    """一张 json 卡片：是合并转发就给出它的 resid，别的卡片返回空串。"""
    try:
        payload = json.loads(parsed["data"].get("data") or "")
        if payload.get("app") != _multimsg_app:
            return ""
        return str(payload["meta"]["detail"].get("resid") or "")
    except (KeyError, TypeError, ValueError):
        return ""


def code_id(code: str) -> str:
    """一个 CQ 串里的转发 id；不是转发、或者没有 id 时返回空串。"""
    try:
        parsed = cq.load(code)
    except (KeyError, TypeError, ValueError):
        return ""
    if parsed.get("type") == "forward":
        return str(parsed["data"].get("id") or "")
    if parsed.get("type") == "json":
        return _json_id(parsed)
    return ""


def fetch(forward_id: str) -> list[dict] | None:
    """向 OneBot 要一条转发的节点；过期、失败、回包为空都返回 None。"""
    try:
        result = connect.call_api("get_forward_msg", message_id=str(forward_id))
    except Exception:
        _log.exception("取合并转发 %s 时请求失败", forward_id)
        return None
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    nodes = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(nodes, list) or not nodes:
        _log.warning(
            "取合并转发 %s 没有得到节点：%s",
            forward_id,
            result.get("wording") or result.get("message") or "回包里的 messages 是空的",
        )
        return None
    return nodes


def _segments(value) -> str:
    """把 OneBot 的数组消息拼回 CQ 文本；本来就是字符串就原样返回。"""
    if isinstance(value, str):
        return value
    parts = []
    for segment in value or []:
        if not isinstance(segment, dict):
            continue
        kind = segment.get("type")
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if kind == "text":
            parts.append(str(data.get("text", "")))
        elif kind:
            parts.append(cq.dump({"type": kind, "data": dict(data)}))
    return "".join(parts)


def _node(value: dict) -> dict:
    """一个 OneBot 节点 → 本模块记录的节点。"""
    sender = value.get("sender") if isinstance(value.get("sender"), dict) else {}
    text = value.get("raw_message")
    if not isinstance(text, str):
        text = _segments(value.get("message"))
    qq = value.get("user_id") or sender.get("user_id")
    return {
        "name": str(sender.get("nickname") or sender.get("card") or qq or "未知"),
        "qq": qq,
        "time": value.get("time"),
        # 图片本地化：节点里的 url 带 rkey，过几天就取不回来了。下载失败时 save_pic 会把
        # 原串留着，于是记录里存的就是"当时那条 url"，不如本地路径耐久，但没丢东西。
        "text": cq.save_pic(text),
    }


def _path(forward_id: str) -> Path:
    # WHY: 自己发出去的转发的 id 是 resid，里面有 / 和 +，直接当文件名会写出目录层级去。
    # 数字 id 经过 quote 原样不变，所以常见的入站转发文件名还是能一眼认出来。
    return DIR / f"{quote(str(forward_id), safe='')}.json"


def _write(record: dict) -> None:
    """写盘并记住它；写不进去只记日志，内存副本照样可用。"""
    _records[record["id"]] = record
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        path = _path(record["id"])
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        _log.exception("落盘合并转发 %s 失败", record.get("id"))


def load(forward_id: str) -> dict | None:
    """已经取回来过的转发：先看内存，再看 data/forward，都没有返回 None。"""
    key = str(forward_id)
    if not key:
        return None
    if key in _records:
        return _records[key]
    path = _path(key)
    if not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _log.exception("读取 %s 失败", path)
        return None
    if not isinstance(record, dict) or not isinstance(record.get("nodes"), list):
        return None
    record.setdefault("id", key)
    _records[key] = record
    return record


def save(forward_id: str, depth: int = 0) -> dict | None:
    """取回一条转发、落盘（嵌套的一起），返回记录；取不到返回 None。"""
    nodes = fetch(forward_id)
    if nodes is None:
        return None
    record = {"id": str(forward_id), "time": int(time.time()), "nodes": [_node(node) for node in nodes]}
    _write(record)
    if depth < MAX_DEPTH:
        # 嵌套转发在节点正文里又是一串 CQ，这里顺路取回来：只在保存时联网一次，渲染时
        # 只读内存和磁盘。
        for node in record["nodes"]:
            for code in _card.findall(node.get("text", "")):
                inner = code_id(code)
                if inner and load(inner) is None:
                    save(inner, depth + 1)
    return record


def record(forward_id: str) -> dict | None:
    """拿一条转发的记录：内存 → 磁盘 → 现取。"""
    return load(forward_id) or save(forward_id)


def _head(node: dict) -> str:
    name = node.get("name") or "未知"
    qq = node.get("qq")
    text = f"{name}({qq})" if qq is not None else str(name)
    when = node.get("time")
    if when:
        try:
            text += " " + time.strftime("%m-%d %H:%M", time.localtime(float(when)))
        except (TypeError, ValueError, OverflowError):
            pass
    return text


def _friendly(text: str) -> str:
    """图片 CQ 换成 `[图片: 路径]`——路径是本地文件或原始 url，两种都能直接交给
    `recognize_image` 用，模型不必自己去 CQ 串里抠。
    """

    def replace(match: re.Match) -> str:
        try:
            data = cq.load(match.group(0))["data"]
        except ValueError:
            return match.group(0)
        return f"[图片: {data.get('file') or data.get('url') or ''}]"

    return _image.sub(replace, text)


def _lines(text: str, indent: str, depth: int) -> list[str]:
    """节点正文：图片写成好认的形式，嵌套转发就地展开，再逐行加缩进。"""
    value = _friendly(str(text or ""))
    for code in _card.findall(value):
        inner = load(code_id(code))
        if inner is None or depth >= MAX_DEPTH:
            continue
        value = value.replace(code, "\n" + render(inner, depth=depth + 1) + "\n", 1)
    return [indent + line if line.strip() else "" for line in value.split("\n")]


def render(record: dict, limit: int = MAX_NODES, depth: int = 0, indent: str = "") -> str:
    """把一条转发记录渲染成给人（和模型）看的多行文本。"""
    nodes = record.get("nodes") or []
    lines = [f"{indent}[合并转发 {len(nodes)} 条]"]
    shown = nodes[:limit] if limit and limit > 0 else list(nodes)
    for index, node in enumerate(shown, 1):
        lines.append(f"{indent}{index}. {_head(node)}")
        lines.extend(_lines(node.get("text", ""), indent + "   ", depth))
    rest = len(nodes) - len(shown)
    if rest > 0:
        lines.append(f"{indent}…还有 {rest} 条没有展开")
    return "\n".join(lines)


def expand(value: str, limit: int = MAX_NODES) -> str:
    """把一段文本里的每处合并转发就地换成人能读的正文；取不到的原样留着。"""
    if not isinstance(value, str) or "CQ:" not in value:
        return value
    result = value
    for code in _card.findall(value):
        inner = code_id(code)
        found = record(inner) if inner else None
        if found is None:
            continue
        result = result.replace(code, "\n" + render(found, limit=limit) + "\n", 1)
    return result


def store(value: str) -> str:
    """变成长时间保存得下的形式：图片落到本地，合并转发取回来存档后展开成正文。

    `.cave` 存消息时走的就是这里——回声洞是给以后读的，而转发 id 和图片 url 都会过期。
    """
    return expand(cq.save_pic(value))


def build(spec: str) -> list[dict]:
    """把多行文本编成发送用的节点数组。

    每行一个节点，`昵称: 正文`，冒号半角全角都认，取第一个；行里没有冒号就用 Bot
    自己的名字。正文要换行就写 `\\n`（两个字符），它会变成真换行——一个节点一行，
    这是唯一的换行写法。空行和空正文会被丢掉。
    """
    name = identity.bot_name()
    uin = identity.bot_id()
    nodes = []
    for line in str(spec).splitlines():
        head, separator, body = line.partition(":")
        if not separator:
            head, separator, body = line.partition("：")
        if not separator:
            head, body = "", line
        text = body.strip().replace("\\n", "\n")
        if not text:
            continue
        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": head.strip() or name,
                    "uin": str(uin),
                    "content": text,
                },
            }
        )
    return nodes


def deliver(nodes: list[dict], user_id=None, group_id=None) -> str:
    """把编好的节点数组发出去；返回一句能原样转告用户的中文结果。"""
    from mods import context, message

    if not nodes:
        return "发送失败：没有可用的节点"
    if group_id is None and user_id is None:
        event = context.current()
        if not event:
            return "发送失败：当前没有聊天上下文，不知道发给谁"
        target = message.target(event)
        group_id = target.get("group_id")
        user_id = target.get("user_id")
    if group_id is not None:
        action, params = "send_forward_msg", {"group_id": group_id}
        where = f"群 {group_id}"
    else:
        action, params = "send_private_forward_msg", {"user_id": user_id}
        where = f"私聊 {user_id}"
    try:
        result = connect.call_api(action, messages=nodes, **params)
    except Exception as error:
        return f"发送失败：{error}"
    if not isinstance(result, dict) or result.get("retcode") != 0:
        detail = result.get("wording") or result.get("message") if isinstance(result, dict) else result
        return f"发送失败：{detail}"
    # WHY: 这里不补登记。合并转发不经过 mods.message 的发送队列，所以它原先自己调一次
    # `message.record_sent` 才进得了聊天记录；现在写记录统一归自发消息回声（实测
    # send_forward_msg 也回流），那次补登记连同 record_sent 一起删了。同 message._send_now。
    return f"已发送 {len(nodes)} 条合并转发 → {where}"
