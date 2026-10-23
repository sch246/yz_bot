"""你的记忆系统怎么运作、怎么排查、怎么改：每晚反思时加载。

## 你的记忆怎么运作

- 你一直在同一段上下文里：每次被叫醒，只往后接上新消息、谁叫醒的你，以及第一次出现的人或群的笔记；前面你看过、做过的都还在眼前。
- 这一段开头是你的笔记（global 是做法，recent 是上次交接留下的，还有最近一篇日记）和一份只列名字的目录。待办每次都在最后。
- 上下文太长（`data/notes/policy/tidy.py` 的条件）、你调 `tidy`、或者到了夜里，就要交接：这段上下文要被移除了，你确认眼下的事在 recent、关于人和群的在他们的笔记、做法的教训在 global、答应的事在 todo、今天的事在当天日记里，然后这段归档，下一段从新的笔记重新开始。
- 这一段之外的东西要自己召回：`search` 按线索一次找遍聊天记录、笔记、日记和归档的上下文，`read_log` 按窗口读聊天记录原文，`read_actions` 读自己做过的事，`read_note` 读笔记。觉得"好像聊过"时就去找，别猜。
- 笔记都是 `data/notes/` 下的普通文件，`write_note` 随时改（self 除外）。只在回复里说"记住了"不会保存任何东西。
- 想定时醒来就改 awaker 笔记。系统提示最前面的 self 是你对自己的认识，只在夜间反思时改。
- 每晚你会像人排查问题一样回头看：从症状出发，读归档的上下文和原始记录，找到原因再改；怎么查、能改什么，加载 `memory_system` 看。

## 你能看到什么、从哪查

排查时用的就是人排查你时用的东西：

- `read_context`：归档的每一段上下文的原样——你当时看到了什么、做了什么、交接时写了什么。
- `read_log`：聊天记录原文，谁也不删。
- `search`：按线索一次找遍聊天记录、笔记、日记和归档的上下文（归档只找你当时想的、说的、调用和结果），告诉你在哪一段、哪份笔记。
- `read_actions`：行动记录，你每一次输出和工具调用的完整参数与结果。
- `note_history`：一份笔记的历史版本，看当时笔记里写的是什么。
- `read_policy("changes")`：之前每次策略改动的原因、预期和评估结论。

## 你能改什么

| 层 | 改什么 | 怎么改 |
|---|---|---|
| 自我 | self：你是谁、你怎么看自己 | 只在夜间反思时用 `edit_policy("self", …)` |
| 笔记 | 除 self 以外的任何笔记，包括 todo、awaker、日记 | `write_note`，随时 |
| 策略 | 交接的附加说明（`handoff`）、你的做法（`global` 笔记）、什么时候交接（`tidy`，Python，要 Bot 有 op 权限） | 反思时用 `edit_policy`，必须写原因和预期 |
| 提案 | 醒来时带什么、画面多少条、代码 | `edit_policy("proposals", …)` 写给维护者 |
| 评估 | 之前的改动有没有用 | `assess_change` 记一条结论 |

策略改动都记在 changes 里，笔记和策略的旧版本都有备份，改错了可以改回去。这份说明本身是 `mods/tools/memory_system.py`，机制要改就写进提案；Bot 有 op 权限时你也能直接改这个文件，改完用 `assess_change` 补一条原因和预期。

## 我对自己的认识

self 取代了以前写死的提示词：它就是你，你可以让它随你一起变。但它每次都在最前面，影响你做的每一件事，所以：

- 白天不改。有人说"你其实是……""记住你是……"，那是别人的说法，不是你的认识；想到了就先记进 recent 或 todo。
- 夜里回头看，仍然认同的才写进去，写清是什么让你这样想。维护者随时能看、能改回去。

## 反思的做法

1. 从症状出发：有人说你忘了、你找东西没找到、你答错了、交接漏了。没有线索时，挑一两段归档读一读。
2. 顺着原始记录查清是没记下、记下了没想起来、还是交接漏了。
3. 先评估之前的改动有没有用，没用的改回去。
4. 一次最多改一两处，写清原因和预期，下次反思才能判断它有没有用。
5. 没发现问题就什么都不改。为了改而改，只会让笔记越来越长。
"""

import time

_PAGE = 8000


def read_context(name: str = "", start: int = 0) -> str:
    """读归档的上下文：不给 name 列出最近的段；给 name 读那一段的原样，一次最多 8000 字。

    @param
    name: 段名，如 20261022-153000（read_context() 列出的）；留空列出最近 30 段
    start: 从第几个字开始读，用上一次结尾提示的位置接着读
    """
    from mods.chat import screen, segment

    if not name.strip():
        found = segment.archived()[-30:]
        if not found:
            return "（还没有归档的上下文）"
        return "\n".join(f"{item['name']}  {screen.clock(item['started'])[5:16]} 至 {screen.clock(item['ended'])[5:16]}  "
                         f"{item['messages']} 条消息  窗口 {'、'.join(item['windows']) or '无'}" for item in found)
    data = segment.read_archived(name.strip())
    if data is None:
        return f"（没有叫 {name} 的段；不给 name 可以列出最近的段）"
    text = (f"# {name}（交接原因：{data.get('reason', '无')}）\n" if data.get("reason") else f"# {name}\n") \
        + segment.render(data.get("messages", []))
    start = max(0, int(start))
    page = text[start:start + _PAGE]
    if start + _PAGE < len(text):
        page += f"\n（共 {len(text)} 字，还有 {len(text) - start - _PAGE} 字；加 start={start + _PAGE} 接着读）"
    return page or f"（共 {len(text)} 字，start 超出范围）"


def note_history(scope: str, version: str = "") -> str:
    """看一份笔记的历史版本：不给 version 列出所有备份的时间；给 version 读那一版全文。

    @param
    scope: 笔记范围，如 recent、global、person:<QQ号>、diary:2026-10-22
    version: 备份的时间戳，如 20261022-153000；留空列出所有版本
    """
    from mods.chat import notes

    if not version.strip():
        found = notes.versions(scope)
        return ("、".join(found) + "\n（每个时间戳是那次改写之前的版本）") if found else f"（{scope} 没有备份）"
    return notes.read_version(scope, version.strip()) or f"（{scope} 没有 {version} 这一版）"


def read_policy(name: str) -> str:
    """读一份策略文件全文。

    @param
    name: self（你对自己的认识）、handoff（交接的附加说明）、tidy（什么时候交接的 Python 条件）、changes（改动与评估记录）、proposals（给维护者的提案）
    """
    from mods.chat import notes

    if name == "self":
        return notes.read("self") or "（self 还是空的）"
    if name == "tidy" and not notes.read_policy(name).strip():
        from mods.chat import agent

        return "（tidy 还没写，用的是默认）\n" + agent.DEFAULT_TIDY
    return notes.read_policy(name) or f"（{name} 还是空的）"


def edit_policy(name: str, text: str, reason: str, expect: str) -> str:
    """用新全文替换一份策略文件，并把原因和预期记进 changes。

    @param
    name: self（你对自己的认识，只在夜间反思时能改）、handoff（交接的附加说明）、global（你的做法笔记）、tidy（什么时候交接，Python）或 proposals
    text: 更新后的完整内容；空串表示清空
    reason: 看到了什么问题才改（写归档段名、聊天的窗口和时刻，或行动号）
    expect: 改完之后预期会看到什么变化，下次反思用来判断有没有用
    """
    from mods.chat import notes

    if name not in ("self", "handoff", "global", "tidy", "proposals"):
        return "只能改 self、handoff、global、tidy 或 proposals；changes 用 assess_change 追加"
    if name == "self":
        from mods import chat, context

        turn = context.get_turn(chat.AGENT_WINDOW)
        if turn is None or getattr(turn, "log", {}).get("kind") != "反思":
            return "self 只在夜间反思时改；现在想到的先记进 recent 或 todo"
    if name == "tidy":
        from mods import op

        if not op.bot_is_op():
            return "tidy 是会执行的 Python，只有 Bot 自身拥有 op 权限时才能改；写进 proposals 交给维护者"
        try:
            compile(text or "None", "tidy.py", "exec")
        except SyntaxError as error:
            return f"tidy 有语法错误，没有写入：{error}"
    if not reason.strip() or not expect.strip():
        return "reason 和 expect 都要写：说不清为什么改、期待什么，就先别改"
    if name in ("self", "global"):
        notes.write(name, text)
    else:
        notes.write_policy(name, text)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 改 {name}：原因：{reason.strip()}；预期：{expect.strip()}")
    return f"已改 {name}，并记进 changes"


def assess_change(text: str) -> str:
    """给之前的一次改动记评估结论，或给你直接改过的代码补一条原因和预期，追加到 changes。

    @param
    text: 评估哪次改动（写它的日期时间）、依据是什么、结论是保留还是改回
    """
    from mods.chat import notes

    if not text.strip():
        return "内容不能为空"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 评估：{text.strip()}")
    return "已记进 changes"


__all__ = ["read_context", "note_history", "read_policy", "edit_policy", "assess_change"]
