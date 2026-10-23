from __future__ import annotations

import ast
from pathlib import Path
import re

from mods import cq, identity, msgs


def has_at(user_id: int):
    def predicate(event: dict) -> bool:
        for code in msgs.at_cq(event):
            qq = cq.load(code)["data"].get("qq")
            if qq in (None, "all"):
                continue
            try:
                if int(qq) == int(user_id):
                    return True
            except ValueError:
                pass
        return False

    return predicate


_image_pattern = re.compile(r"(\[CQ:image(?:,[^,=]+=[^,\]]*)*\])")


def msg_split(value: str) -> list[dict]:
    parts = []
    for part in _image_pattern.split(value):
        if not part:
            continue
        if _image_pattern.fullmatch(part):
            try:
                uri = cq.load(part)["data"]["url"]
                parts.append({"type": "image_url", "image_url": {"url": uri}})
            except (KeyError, ValueError):
                parts.append({"type": "text", "text": "[解析失败的图片]"})
        elif part.strip():
            parts.append({"type": "text", "text": part})
    return parts


def _is_context_poke(event: dict, in_group: bool) -> bool:
    if not msgs.is_poke(event):
        return False
    return bool(in_group) or event.get("target_id") == identity.bot_id()


_MEMORY_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "memory_system.py"


def _memory_overview() -> str:
    """The "how your memory works" section of memory_system's text, read from disk.

    WHY: 2026-10-22 维护者：她对自己记忆系统的描述只写在一处，跟着实现走——旧描述没跟着
    改，她就会照着讲出已经不存在的机制。这一节放在 `tools/memory_system.py` 开头，系统
    提示每次从文件读：她或维护者改了文件，下一次唤醒就生效，不用重启；完整的排查与修改
    说明仍只在加载 memory_system 时出现。
    """
    try:
        text = ast.get_docstring(ast.parse(_MEMORY_SOURCE.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, ValueError):
        return ""
    start = text.find("## 你的记忆怎么运作")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else None].strip()


def _base_prompt() -> list[dict]:
    memory = _memory_overview()
    # WHY: 「被叫醒不等于必须开口」后面几条沉默纪律参照 QQ-agent 0.4 的 prompt.js（维护者
    # 2026-10-23）：私聊每条都叫醒以后，话没说完就回靠这里压住，不靠延迟。
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；聊天记录里这个 QQ 号的发言就是你自己说过的话
- 群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]。聊天记录每条的头部是 `【头衔】名字(QQ号) 时:分:秒 | message_id`，正文缩进在下面，reply 里的 id 就是这个 message_id
- 聊天中可能不会有明显的问题，扮演好角色即可
- 被叫醒不等于必须开口：别人已经答完、话题和你无关、只会复述或附和、或者你没什么想说的，就不调 `say`，直接结束。不回不需要理由，也不需要收尾的话。少说比多说好
- 私聊里每条消息都会叫醒你，而人常常把一句话分几条发。对方可能还没说完、或者你想看看后续，就什么都不发直接结束；有新消息会再叫你，这是正常节奏
- 不要别人说一句就回一句：先看对方是不是还在说、是不是在跟别人说、值不值得接。刚说过话，除非有人接你或你有新东西，不用马上再补一条
- 能一条说完就一条，单条尽量短，不写小作文。末尾提示里出现你读画面之后才到的消息时，先看完再决定说什么
- 如无特殊要求，请用中文回复
- **说话要调 `say`**。直接写在回复正文里的内容不会发出去，只会留在你自己的行动记录里
- 认识了谁、答应了什么、被纠正了什么，想记就当场用 `write_note` 记；没做完的事写进 todo，可复用的做法写 Markdown Skill
- 对外发送必须在 say 里明确写目标 g群号 或 u私聊对端号；没有默认接收窗口
- `say` 返回这条消息的 message_id；它默认 `final_call=true`，说完这一轮就结束，要接着干活就传 `final_call=false`"""
                + (f"\n\n{memory}" if memory else "")}]
