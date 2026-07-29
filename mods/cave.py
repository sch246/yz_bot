"""The global 回声洞 pool and its interactive command."""

from __future__ import annotations

import json
import os
from random import randint
import re
import time
from typing import Any

from mods import context, cq, history, identity, msgs, op, pages, storage, text
from mods.command import command
from mods.message import sendmsg
from mods.randoms import getran


LOAD_AFTER = ("storage", "identity", "op")
CAVE_MSG_REQUIRED_KEYS = ("sender", "qq", "time", "text")
re_int = re.compile(r"(-?\d+)$")
cave: "Cave | None" = None
_cave_startup_snapshot = ""


def _snapshot(value: "Cave") -> str:
    return json.dumps(
        {"msgs": value.msgs, "pool": value.pool},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


class Cave:
    def __init__(self) -> None:
        items = sorted(storage.get("", "cave").items(), key=lambda item: int(item[0]))
        self.msgs: dict[str, dict[str, Any]] = dict(items)
        storage.get_namespace("")["cave"] = self.msgs
        self.pool: list[str] = storage.get("", "cave_pool", list)

    def index(self, index: str = "") -> str:
        if index == "":
            if not self.pool:
                self.pool.extend(self.msgs)
            selected = getran(self.pool, True)
            if selected is None:
                return ""
            position, index = selected
            if randint(0, 2):
                del self.pool[position]
        if index.startswith("-"):
            keys = list(self.msgs)
            index = keys[int(index) + len(keys)]
        return index

    def empty(self) -> str:
        keys = sorted(self.msgs, key=int)
        last = int(keys[-1]) if keys else -1
        for number in range(last + 2):
            if str(number) not in self.msgs:
                return str(number)
        raise RuntimeError("无法取得 cave 空位")

    def last(self) -> str | None:
        user_id = context.current()["user_id"]
        owned = [item for item in self.msgs.items() if user_id == item[1].get("qq")]
        return owned[-1][0] if owned else None

    def get(self, index: str) -> str:
        if not self.msgs:
            return "回声洞是空的！"
        value = self.msgs.get(index)
        if value is None:
            return "该条消息不存在！"
        if value.get("group"):
            return (
                f"{index}:\n{value['text']}\n    ——{value['sender']} "
                f"于 {value['group']}，\n  {value['time']}"
            )
        return f"{index}:\n{value['text']}\n    ——{value['sender']} 于 {value['time']}"

    def delete(self, index: str) -> str:
        if not self.msgs:
            return "回声洞是空的！"
        value = self.msgs.get(index)
        if value is None:
            return "该条消息不存在！"
        user_id = context.current()["user_id"]
        if not (op.is_op(user_id) or user_id == value.get("qq")):
            return "删除其他人的回声洞需要op"
        del self.msgs[index]
        if index in self.pool:
            self.pool.remove(index)
        return f"序号 {index} 删除成功"

    def set(self, index: str, value: str) -> str:
        event = context.current()
        self.msgs[index] = {
            "sender": cq.save_pic(identity.getname()),
            "qq": event["user_id"],
            "group": identity.getgroupname() if event.get("group_id") else None,
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "text": cq.save_pic(value),
        }
        self.pool.append(index)
        return f"已添加，序号 {index}"

    def search(self, keyword: str):
        if not self.msgs:
            return "回声洞是空的！"
        results = []
        for index, value in self.msgs.items():
            if keyword.lower() in value["text"].lower():
                preview = value["text"]
                if len(preview) > 20:
                    preview = preview[:20] + "..."
                results.append(f"{index} | {value['time']}:\n    {text.addtab(preview)}")
        if not results:
            return f'未找到包含关键词 "{keyword}" 的消息'
        sendmsg(f'找到 {len(results)} 条包含 "{keyword}" 的消息:')
        return pages.display(results, 10)


def _require_cave() -> Cave:
    if cave is None:
        raise RuntimeError("cave 尚未加载")
    return cave


@command
def run(body: str):
    """读取和管理全局回声洞。

    格式：.cave [编号]；add [内容]；addn <数量>；search <文本>；del [编号]。
    无参数随机读取；缺少内容时进入交互。管理员可用 save|load [路径] 导出或载入。
    """
    state = _require_cave()
    operation, rest = text.read_params(body)
    if not operation or re_int.fullmatch(operation):
        return state.get(state.index(operation))
    if operation == "del":
        if not rest.strip():
            index = state.last()
            if index is None:
                return "没有找到你设置的回声洞"
        else:
            index, _ = text.read_params(rest)
            if not re_int.fullmatch(index):
                return run.__doc__
        return state.delete(state.index(index))
    if operation == "add":
        value = rest.strip()
        if not value:
            reply = yield "发送一条消息，^C以取消"
            if not msgs.is_msg(reply):
                return "非消息，执行终止"
            value = reply["message"]
        return state.set(state.empty(), value)
    if operation == "addn":
        count_text, _ = text.read_params(rest)
        try:
            count = int(count_text)
        except ValueError:
            return "语法: .cave addn <n:int>"
        if count == 0:
            return "n不能为0"
        if count < 0:
            events = history.get_self_log(context.current())[1 : -count + 1]
            value = "".join(event["message"] for event in reversed(events))
            return state.set(state.empty(), value)
        value = ""
        for index in range(count):
            reply = yield f"接下来的{count}条消息将会被合并为1条记录" if index == 0 else None
            if not msgs.is_msg(reply):
                return "非消息，执行终止"
            value += reply["message"]
        return state.set(state.empty(), value) if value else "不知道为啥消息为空"
    if operation == "search":
        keyword = rest.strip()
        return state.search(keyword) if keyword else "请输入要搜索的关键词"
    if operation in ("save", "load"):
        if not op.require_op(context.current(), remind=False):
            return "导入导出回声洞需要op"
        path, _ = text.read_params(rest)
        path = path.strip() or "data/cave_save.json"
        return _save_cave(path) if operation == "save" else _load_cave(path)
    return run.__doc__


def _save_cave(path: str):
    state = _require_cave()
    data = {"msgs": state.msgs, "pool": state.pool}
    try:
        current = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception as error:
        return f"序列化失败: {error}"
    if _cave_startup_snapshot != current:
        reply = yield f"当前 cave 与启动时不同（{len(state.msgs)} 条），确认覆盖 {path}？(y/n)"
        if not (msgs.is_msg(reply) and reply["message"].strip().lower() == "y"):
            return "操作取消"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False, default=str)
    return f"已保存 {len(state.msgs)} 条回声洞到 {path}"


def _load_cave(path: str):
    global _cave_startup_snapshot
    state = _require_cave()
    if not os.path.exists(path):
        return f"文件不存在: {path}"
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except Exception as error:
        return f"读取失败: {error}"
    if not isinstance(data, dict):
        return f"格式错误: 期望 JSON 对象，得到 {type(data).__name__}"
    if not isinstance(data.get("msgs"), dict):
        return "格式错误: 缺少 msgs 字段或不是对象"
    if not isinstance(data.get("pool"), list):
        return "格式错误: 缺少 pool 字段或不是列表"
    errors = []
    for key, value in data["msgs"].items():
        if not isinstance(value, dict):
            errors.append(f"msgs[{key}]: 不是对象")
            continue
        for required in CAVE_MSG_REQUIRED_KEYS:
            if required not in value:
                errors.append(f"msgs[{key}]: 缺少字段 {required!r}")
    if errors:
        suffix = f"\n... 共 {len(errors)} 处错误" if len(errors) > 10 else ""
        return f"验证失败，保留当前数据 ({len(state.msgs)} 条):\n" + "\n".join(errors[:10]) + suffix
    reply = yield f'将用 {len(data["msgs"])} 条替换当前 {len(state.msgs)} 条，确认？(y/n)'
    if not (msgs.is_msg(reply) and reply["message"].strip().lower() == "y"):
        return "操作取消"
    state.msgs.clear()
    state.msgs.update(data["msgs"])
    state.pool[:] = data["pool"]
    storage.save()
    _cave_startup_snapshot = _snapshot(state)
    return f"已加载 {len(state.msgs)} 条回声洞，数据已保存到磁盘"


def on_load(_ctx) -> None:
    global cave, _cave_startup_snapshot
    from mods import is_available

    missing = [name for name in ("storage", "identity", "op") if not is_available(name)]
    if missing:
        raise RuntimeError("cave 依赖不可用: " + ", ".join(missing))
    cave = Cave()
    _cave_startup_snapshot = _snapshot(cave)
