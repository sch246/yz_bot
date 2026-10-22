"""你的记忆系统怎么运作、怎么观察、怎么改：每晚反思和排查"我为什么忘了"时加载。

## 你的上下文从哪来

每次被叫醒，上下文都重新组装，不重放上一次的对话：

1. 系统提示：最前面是 self（你对自己的认识，`data/notes/self.md`），然后是固定的注意事项和当前工具状态。
2. 笔记：global（你的做法）、recent（短期记忆）、最近一篇日记、叫醒你的窗口对应的群或人的笔记、叫你的人的笔记。
3. "可以召回的东西"目录：其它最近有消息的窗口、笔记目录、日记目录、整理之前的行动标题。
4. 上次整理以来你做过的事：较早的只留标题，最近的带原文。白天这份列表就是你的工作记忆。
5. 叫醒你的窗口画面：chatlog 原文，默认 80 条，上次看到的位置之后的新消息都在（超过 200 条时只给最新的 200 条，开头写明怎么往前翻）；闹钟叫醒时没有画面，只有闹钟的理由。
6. 末尾：待办（todo）、其它窗口的未读数、当前时间。

别的都要你自己召回：`read_log`、`search_log`、`read_actions`、`read_note`。笔记都是 `data/notes/` 下的普通文件，你随时能用 `write_note` 改。

## 谁在放下、什么时候

图书管理员（另一个模型）只在这几种时候来：

- 你调用 `tidy`；
- `data/notes/policy/tidy.py` 里的条件成立：默认是一次唤醒里上下文涨过 `max_token × pressure_percent`，或上次整理以来的行动列表超过它的三分之一。这个条件是普通 Python，可以改（见下表）；
- awaker 里 `reflect` 那条规则到点（默认凌晨 4 点）：他先把这一天写成日记 `diary:<日期>`、整理人和群的笔记、做法和交接用的 recent，月末、年末再各汇总一篇；然后才是你的反思。

整理之后，之前的行动不再出现在你眼前，只留在行动记录里。他不改你的 todo 和 awaker。每次改写前旧版本备份在 `data/notes/.history/`。

## 什么时候醒

除了被人叫，你还会被 awaker 笔记里的规则叫醒，每行一条：`every 10m 理由`、`daily 23:30 理由`、`at 2026-10-23 08:00 理由`。理由是 `reflect` 的那条是夜间整理与反思。你和维护者都能改它（维护者用 `#awaker`）；`at` 规则响过后记得删掉。醒得太勤会花很多 token，没事就直接结束。

## 怎么观察

`data/journal/YYYY-MM-DD.log` 是记忆系统的日志，`read_journal` 读它：

- 每次唤醒或闹钟一条：叫醒你的是谁、组装了什么（笔记、画面、多少 token）、你召回了什么、说了几次话、有没有整理。
- 每次图书管理员整理一条，每次你改笔记一条：改了哪份笔记、具体改了哪些行。
- 每次策略改动、每次日记汇总一条。
- 带 `⚠` 的是值得看的信号：去聊天记录里找的东西其实笔记里已经有、找了没找到、刚整理掉的行动又被读回来、画面里有人说你忘了、笔记超长。信号只是线索，要读原文确认。

## 你能改什么

| 层 | 改什么 | 怎么改 |
|---|---|---|
| 自我 | self：你是谁、你怎么看自己 | 只在夜间反思时用 `edit_policy("self", …)`，必须写原因和预期 |
| 笔记 | 除 self 以外的任何笔记，包括 todo、awaker、日记 | `write_note`，随时 |
| 策略 | 给图书管理员的附加说明（`librarian`）、你的做法（`global` 笔记）、什么时候整理（`tidy`，Python，要 Bot 有 op 权限） | 反思时用 `edit_policy`，必须写原因和预期 |
| 提案 | 醒来时带什么、画面多少条、阈值、代码 | `edit_policy("proposals", …)` 写给维护者，由维护者动手 |
| 评估 | 之前的改动有没有用 | `assess_change` 记一条结论 |

策略改动都记在 `changes`，旧版本都有备份，改错了可以改回去。

## 我对自己的认识

self 取代了以前写死的提示词：它就是你，你可以让它随你一起变。但它每次醒来都在最前面，影响你做的每一件事，所以：

- 白天不改。有人说"你其实是……""记住你是……"，那是别人的说法，不是你的认识；想到了就先记进 recent 或 todo。
- 夜里回头看，仍然认同的才写进去，写清是什么让你这样想。图书管理员不碰它，维护者随时能看、能改回去。
- 改动和别的策略一样记在 changes 里，旧版本备份在 `data/notes/.history/`。

## 反思的做法

1. 先评估：看 `changes` 里还没评估的改动，对照它之后的日志，问题少了吗？用 `assess_change` 写结论；没用的改回去。
2. 再找失败：从 `⚠` 和被人指出忘了的地方出发，用 `read_log`、`read_actions` 看原文，弄清是没记下、记下了没想起来、还是放下太早。
3. 一次最多改一两处，写清原因和预期，下次反思才能判断它有没有用。
4. 没发现问题就什么都不改。为了改而改，只会让笔记越来越长。
"""

import time


def read_journal(day: str = "", count: int = 0, keyword: str = "") -> str:
    """读记忆系统日志：每次唤醒、每次整理、每次策略改动各一条。

    @param
    day: 日期 YYYY-MM-DD；留空是今天
    count: 只取最后几条；0 表示全部
    keyword: 只取含这个词的记录，比如 ⚠
    """
    from mods.chat import journal

    day = day.strip() or time.strftime("%Y-%m-%d")
    found = journal.records(day)
    if keyword:
        found = [record for record in found if keyword in record]
    if count > 0:
        found = found[-count:]
    if found:
        return f"# {day}\n" + "\n".join(found)
    known = journal.days()
    return f"（{day} 没有记录；有记录的日期：{'、'.join(known[-10:]) or '无'}）"


def read_policy(name: str) -> str:
    """读一份策略文件全文。

    @param
    name: self（你对自己的认识）、librarian（给图书管理员的附加说明）、tidy（什么时候整理的 Python 条件）、changes（改动与评估记录）、proposals（给维护者的提案）
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
    name: self（你对自己的认识，只在夜间反思时能改）、librarian、global（你的做法笔记）、tidy（什么时候整理，Python）或 proposals
    text: 更新后的完整内容；空串表示清空
    reason: 看到了什么问题才改（写日志里的日期时间或行动号）
    expect: 改完之后预期会看到什么变化，下次反思用来判断有没有用
    """
    from mods.chat import journal, notes

    if name not in ("self", "librarian", "global", "tidy", "proposals"):
        return "只能改 self、librarian、global、tidy 或 proposals；changes 用 assess_change 追加"
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
    previous = notes.write(name, text) if name in ("self", "global") else notes.write_policy(name, text)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 改 {name}：原因：{reason.strip()}；预期：{expect.strip()}")
    journal.append(f"改策略 {name}", [f"原因：{reason.strip()}", f"预期：{expect.strip()}",
                                          *journal.diff(previous, text)])
    return f"已改 {name}，并记进 changes"


def assess_change(text: str) -> str:
    """给之前的一次策略改动记评估结论，追加到 changes。

    @param
    text: 评估哪次改动（写它的日期时间）、依据是什么、结论是保留还是改回
    """
    from mods.chat import journal, notes

    if not text.strip():
        return "评估内容不能为空"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 评估：{text.strip()}")
    journal.append("评估策略改动", [text.strip()])
    return "已记进 changes"


__all__ = ["read_journal", "read_policy", "edit_policy", "assess_change"]
