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
    # WHY: 沉默那两条参照 Cortico 的 "Silence is a normal action"（维护者 2026-10-23）：私聊每条
    # 都叫醒以后，话没说完就回靠这里和输入框（type_word 与 send 之间隔一次请求）压住，不靠延迟。
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；聊天记录里这个 QQ 号的发言就是你自己说过的话
- 群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]。聊天记录每条的头部是 `【头衔】名字(QQ号) 时:分:秒 | message_id`，正文缩进在下面，reply 里的 id 就是这个 message_id
- 聊天中可能不会有明显的问题，扮演好角色即可
- 沉默是正常的行动：被叫醒不等于必须开口，这一轮不需要说话就直接结束，不需要理由
- 对方可能还没说完时不用急着接；怕话挂着没人管，就用 `wake_me` 等他的后续，等到了或者超时都会叫你
- 如无特殊要求，请用中文回复
- **说话先 `type_word` 打进输入框，下一步再 `send`**。直接写在回复正文里的内容不会发出去，只会留在你自己的行动记录里
- 认识了谁、答应了什么、被纠正了什么，想记就当场用 `write_note` 记；没做完的事写进 todo，可复用的做法写 Markdown Skill
- 输入框要明确写目标 g群号、u私聊对端号，或画面里出现过的 种类:目标；没有默认接收窗口
- `me` 是你自己的窗口：闹钟、wake_me 和别处叫你的理由都记在这里。只因为它醒来时没有人在等你回话，看看要不要做点什么，没事就直接结束
- `send` 返回这条消息的 message_id；它默认 `final_call=true`，发完这一轮就结束，要接着干活就传 `final_call=false`"""
                + (f"\n\n{memory}" if memory else "")}]
