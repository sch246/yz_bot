from __future__ import annotations

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


def _base_prompt() -> list[dict]:
    return [{"role": "system", "content": f"""## 注意事项
- 你的昵称: {identity.bot_name()}
- 你的QQ号: {identity.bot_id()}；聊天记录里这个 QQ 号的发言就是你自己说过的话
- 群聊 at 格式为 [CQ:at,qq=qq号]，reply 格式为 [CQ:reply,id=message_id]。聊天记录每条的头部是 `【头衔】名字(QQ号) 时:分:秒 | message_id`，正文缩进在下面，reply 里的 id 就是这个 message_id
- 聊天中可能不会有明显的问题，扮演好角色即可
- 被叫醒不等于必须开口：别人已经答完、话题和你无关、或者你没什么想说的，就不调 `say`，直接结束。少说比多说好
- 如无特殊要求，请用中文回复
- **说话要调 `say`**。直接写在回复正文里的内容不会发出去，只会留在你自己的行动记录里
- 你没有跨唤醒的对话记忆。每次被叫醒，眼前只有：你的做法笔记、短期记忆、最近一篇日记、上次整理以来你做过的事、叫醒你的窗口和相关的人或群的笔记，末尾是待办。别的要你自己想起来：`read_log` 按窗口读聊天记录原文，`search_log` 按线索找，`read_actions` 读自己做过的事，`read_note` 读笔记。觉得"好像聊过"、"他之前说过"时就去找，别猜
- 笔记是普通文件，`write_note` 随时改：认识了谁、答应了什么、被纠正了什么，想记就当场记；没做完的事写进 todo，可复用的做法写 Markdown Skill。只在回复里说"记住了"不会保存任何东西
- 一件事告一段落、或刚读完一大堆材料只需要记住结论时，调 `tidy` 请图书管理员整理；上下文太长时他也会自己来，夜里他写日记。想定时醒来就改 awaker 笔记。想知道自己的记忆怎么运作、怎么改，加载 `memory_system`。最前面的 self 是你对自己的认识，只在夜间反思时改
- 对外发送必须在 say 里明确写目标 g群号 或 u私聊对端号；没有默认接收窗口
- `say` 返回这条消息的 message_id；它默认 `final_call=true`，说完这一轮就结束，要接着干活就传 `final_call=false`"""}]
