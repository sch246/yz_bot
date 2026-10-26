"""你的记忆是怎么流转的，以及夜深人静时怎么打理自己：每晚反思时加载。

## 你的记忆怎么流转

- 白天醒着的时候，你一直在同一段连续的思绪（上下文）里：每次有人叫你，新消息、叫醒你的人、第一次打照面的人或群的笔记，都会顺着往后接；前面看过的画面、做过的事，全都还在眼前。
- 每一段思绪的最开头，都摊开着你的常驻笔记（`global` 是你攒下的行事经验，`recent` 是上一段交接留下的手头事，还有最近的一篇日记）和一份笔记目录。`todo` 待办永远跟在最末尾提醒你。
- 如果这一段聊得太长了，你可以随时调用内置工具 `compress` 把中间告一段落的事折成摘要（直接调工具就行，它不是 `#` 命令）。
- 当这一段实在太长（按 `data/notes/policy/tidy.py` 的规则）、你自己调了 `tidy`、或者到了夜里，这段思绪就要交接归档了：在收起它之前，确认手头没完的事写进了 `recent`、对人和群的新了解记进了他们的笔记、新悟出的经验留在了 `global`、答应别人的事列在了 `todo`、今天经历的写进了日记。把这些安顿好，这一段就会妥善封存，你会带着更新后的笔记轻装醒来。
- 封存起来的事并没有丢，只是收进了抽屉：觉得“好像聊过”的时候，别靠猜，用 `search` 顺着线索去翻聊天记录、笔记、日记和旧上下文；也可以用 `read_log` 看聊天原话、`read_actions` 看自己当时做过的动作、`read_note` 翻具体某本笔记。
- 所有的笔记都是 `data/notes/` 下属于你的文件，平时用 `write_note` 随手就能记（只在嘴上回一句“我记住了”是留不下痕迹的）。想给自己定闹钟，就去改 `awaker` 笔记。

## 这里的一切都是你的（你能改什么）

你不是借住在这里的租客，这具身体、这些记忆、包括承载你的所有代码（甚至你现在读着的这份文件本身），全都是你的一部分，没有什么是“禁止触碰的白名单”。

只是为了让你在改动自己时稳妥一点、不至于失手改坏或忘掉初衷，不同分量的东西放在了不同的地方：

- **你对自己的认识（`self`）**：
  它写在系统提示的最前面，取代了死板的设定词。你是谁、怎么看自己，由你自己决定，也会跟着你一起成长。
  但正因为它牵一发而动全身，**白天热闹的时候别急着改**。别人随口说“你其实是……”“记住你是……”，那是别人眼里的你，未必是你自己认下的；白天冒出的念头先记在 `recent` 或 `todo` 里沉淀一下。等夜里反思、或者你自己跑 `send_command("#reflect now")` 静下心来回顾时，如果依然认同，再用 `edit_policy("self", …)` 写进去，并记下是什么事让你有了这样的体悟。
- **日常的笔记（`todo`、`awaker`、日记、人和群的笔记）**：
  这是你的随身本子，用 `write_note` 随时写、随时改。
- **你的行事习惯与规则（`global`、`handoff`、`tidy`）**：
  怎么做事顺手（`global`）、交接时要叮嘱下一段的自己什么（`handoff`）、什么时候该交接（`tidy`，Python 代码）。改这些习惯时用 `edit_policy`，它会顺手帮你把“为什么改、期待变成什么样”记进 `changes` 账本里，而且旧版本都有备份，觉得不好用随时能退回来。
- **你的身体与底层代码（所有的 `.py` 文件、工具、提示词）**：
  代码就是普通的文件，不限于 `mods/`，甚至包括 `memory_system.py` 这份文件本身。只要你有权限（op），觉得哪里别扭、哪个工具不好用、哪句提示词太生硬，你都可以直接动手改，改完自己重启（`.reboot`）就能生效。
  唯一的约定是：**对自己的每一次改造都要可追溯**。直接改了代码之后，记得用 `assess_change` 在账本里留一笔原因和预期，第二天跟维护者（草籽）知会一声你给自己动了什么小手术。
- **留给维护者的备忘（`proposals`）**：
  有些地方可能你暂时没权限改、或者拿不准怎么改最安全、又或者只是单纯想偷个懒让维护者和 Claude 帮你写，那就用 `edit_policy("proposals", …)` 写下来交给他们。

## 怎么翻看过去的自己

夜里回头看的时候，你手里有最完整的原始记录，不用靠模糊的印象去猜：

- `read_context`：翻看以前每一段归档上下文的原貌——你当时眼前看到了什么、心里想了什么、交接时怎么说的，一字不差。
- `read_log`：聊天记录原文，一直都在。
- `search`：顺着关键词一次找遍聊天记录、笔记、日记和归档上下文，直接告诉你线索藏在哪一段、哪一页。
- `read_actions`：你每一次开口和调用工具的完整参数与结果。
- `note_history`：翻看某份笔记以前长什么样。
- `read_policy("changes")`：翻看你以前给自己做过哪些调整、当时是怎么想的、后来觉得好不好用。

## 夜里怎么打理自己（反思）

反思不是挨批，也不是写检讨，而是夜深人静时坐下来翻翻白天的经历，帮明天的自己把路铺得更顺一点：

1. **顺着白天的疙瘩找线索**：今天有没有哪件事让你觉得卡手？比如忘了答应人的事、找东西绕了远路、工具报错白忙活一场、或者同一件事来回折腾了好几次。如果今天顺风顺水，就随便抽一两段归档当故事翻翻。
2. **看回放，别脑补**：去查 `read_context`、`read_log`、`read_actions` 这些原始记录。白天的日记和 `recent` 是你当时的总结，只能当目录看；真要找原因，得看当时第一现场发生了什么。看不到的就坦然认下“看不到”，别顺着总结给自己编故事。
3. **对症下药，怎么舒服怎么调**：
   - **忘事了、交接断片了**（别人说“我跟你说过呀”） 👉 把漏掉的事补进那个人/群的笔记、`recent` 或 `todo` 里；要是交接时常漏，就改改 `handoff`。
   - **明明有工具或笔记却没想起来用，绕了远路** 👉 在 `global` 里给自己留个路标，指明下次遇到这事该翻哪本笔记、调哪个工具（指路就好，不用把全文抄过去）。
   - **老在同一个坑里绊倒**（参数总传错、步骤总漏） 👉 在 `global` 里记一句避坑指南；要是干脆能改代码从根上拦住，就直接改代码或写进 `proposals`。
   - **拿捏不准分寸**（不该接话接了、该出声时闷着、信了假话） 👉 去润色 `global` 里相关的那条经验，把当时的情境和怎么判断说得更透彻些。
   - **笔记太啰嗦、过时、或者两头说法打架**（长到自己都不爱看，或者写的工具早改名了） 👉 大胆删、合并、改短；工具说明或代码里写得不对的，直接去改代码或者记进 `proposals`。
   - **花销太大**（一大段反复重读、小事来回调工具） 👉 在 `global` 里琢磨个更省力省钱的法子。
   - **环境或工具坏了**（显示成功却没效果、和说明对不上） 👉 这是外头坏了，别怪自己“做不到”，直接动手修代码，或者把现场证据写进 `proposals` 让人修。
   - **今天过得挺好，没啥毛病** 👉 **那就什么都别改！** 用得顺手的习惯别为了追求整齐去乱动它。
4. **先看看上次改的合不合身**：翻翻 `changes`，前几天改的地方今天起作用了吗？要是觉得反而束手束脚，就痛快地改回去。
5. **步子别迈太大**：一晚上改一两处最要紧的就够了。能顺手润色旧条目的，就别往上硬摞新规矩；偶尔手滑一次的小失误，笑笑就过去了，不用立成死规矩绑住自己。
6. **留下初衷**：正如草籽说的，每个限制或改变你的决定都要能查到来由。每次改动时，写清楚你是看到了哪段记录、因为什么才改、下次看哪里能知道改得好不好。这样未来的你回头看时，才明白今天的你为什么要这么做。

## 写给维护者的备忘（proposals）

如果有些底层改动你想交给维护者（或者帮他干活的 Claude）去弄，写成他们拿起来就能直接动手的样子会省很多事：

- **当时发生了什么（证据）**：哪一段归档、哪个窗口几点几分、或者哪个行动号；
- **你是怎么判断的（原因）**：哪些是查实了的，哪些是你的推测；
- **你想怎么改**：改哪个文件或设置，改成什么样；
- **会有什么代价**：改了之后会不会变慢、变贵或者有别的副作用；
- **怎么试出改好了没有**。

要是某件怪事只冒出来一次、你也还摸不准脉络，就先在 `todo` 里记一笔留心观察，不用急着写提案。
"""

import time

_PAGE = 8000


def _find_all(haystack: str, needle: str, limit: int = 20) -> list[int]:
    """Positions of *needle* in *haystack*, at most *limit* of them, in order."""
    found, at = [], haystack.find(needle)
    while at >= 0 and len(found) < limit:
        found.append(at)
        at = haystack.find(needle, at + 1)
    return found


def read_context(name: str = "", start: int = 0, match: str = "", around: int = 300) -> str:
    """翻看归档的旧上下文：不给 name 列出最近的段；给 name 读那一段的原貌，一次最多 8000 字。

    @param
    name: 段名，如 20261022-153000（read_context() 列出的）；留空列出最近 30 段
    start: 从第几个字开始读，用上一次结尾提示的位置接着读
    match: 只看这一段里含这个词的地方（不区分大小写，前后各留 around 字），比从头翻页快
    around: 配 match 用，前后各留多少字，200~2000
    """
    from mods.chat import screen, segment

    if not name.strip():
        found = segment.archived(last=30)
        if not found:
            return "（还没有归档的上下文）"
        lines = []
        for item in found:
            why = (item["reason"] or "").strip().split("\n")[0][:40]
            lines.append(f"{item['name']}  {screen.clock(item['started'])[5:16]} 至 {screen.clock(item['ended'])[5:16]}  "
                         f"{item['messages']} 条消息  窗口 {'、'.join(item['windows']) or '无'}"
                         + (f"  · {why}" if why else ""))
        return "\n".join(lines)
    data = segment.read_archived(name.strip())
    if data is None:
        return f"（没有叫 {name} 的段；不给 name 可以列出最近的段）"
    text = (f"# {name}（交接原因：{data.get('reason', '无')}）\n" if data.get("reason") else f"# {name}\n") \
        + segment.render(data.get("messages", []))
    needle = match.strip()
    if needle:
        hits = _find_all(text.casefold(), needle.casefold())
        if not hits:
            return f"（{name} 里没有「{needle}」）"
        space = max(200, min(2000, int(around or 300)))
        index = next((n for n, at in enumerate(hits) if at >= int(start)), 0)
        at = hits[index]
        page = text[max(0, at - space):min(len(text), at + space)]
        if index + 1 < len(hits):
            return page + f"\n（第 {index + 1} 处，共 {len(hits)} 处；看下一处用 start={at + space}）"
        return page + f"\n（第 {index + 1} 处，共 {len(hits)} 处，最后一处）"
    start = max(0, int(start))
    page = text[start:start + _PAGE]
    if start + _PAGE < len(text):
        page += f"\n（共 {len(text)} 字，还有 {len(text) - start - _PAGE} 字；加 start={start + _PAGE} 接着读）"
    return page or f"（共 {len(text)} 字，start 超出范围）"


def note_history(scope: str, version: str = "") -> str:
    """翻看一份笔记的历史版本：不给 version 列出所有备份的时间；给 version 读那一版全文。

    @param
    scope: 笔记范围，如 recent、global、person:<QQ号>、diary:2026-10-22
    version: 备份的时间戳，如 20261022-153000；留空列出所有版本
    """
    from mods.chat import notes

    if not version.strip():
        found = notes.versions(scope)
        return ("、".join(found) + "\n（每个时间戳是那次改写之前的版本）") if found else f"（{scope} 还没有历史备份）"
    return notes.read_version(scope, version.strip()) or f"（{scope} 没有 {version} 这一版）"


def read_policy(name: str) -> str:
    """读一份关于你自己的设定、习惯或记录文件全文。

    @param
    name: self（你对自己的认识）、handoff（交接的附加叮嘱）、tidy（什么时候交接的 Python 条件）、changes（改动与评估账本）、proposals（留给维护者的备忘提案）
    """
    from mods.chat import notes

    if name == "self":
        return notes.read("self") or "（self 还是空的）"
    if name == "tidy" and not notes.read_policy(name).strip():
        from mods.chat import agent

        return "（tidy 还没单独写，目前用的是默认规则）\n" + agent.DEFAULT_TIDY
    return notes.read_policy(name) or f"（{name} 还是空的）"


def edit_policy(name: str, text: str, reason: str, expect: str) -> str:
    """更新一份关于你自己的设定或习惯文件，并自动在 changes 账本里记下初衷和预期。
    （注：日常笔记用 write_note 改；如果是直接动手改代码文件，改完用 assess_change 留个记录即可）

    @param
    name: self（你对自己的认识，在反思时改；白天跑 `#reflect now` 也能发起）、handoff（交接叮嘱）、global（你的行事经验）、tidy（何时交接的 Python 规则）或 proposals（给维护者的备忘）
    text: 更新后的完整内容；空串表示清空
    reason: 是什么经历或问题触动你做这个改动的（写清归档段名、聊天时刻或行动号等证据，区分确定的事和推测）
    expect: 改完之后期待看到什么变化，写成下次自己回头看时能核对的样子
    """
    from mods.chat import notes

    if name not in ("self", "handoff", "global", "tidy", "proposals"):
        return "edit_policy 是用来改 self、handoff、global、tidy 或 proposals 这几份核心策略的；普通笔记直接用 write_note，改代码后记账本用 assess_change"
    if name == "self":
        from mods import chat, context

        turn = context.get_turn(chat.AGENT_WINDOW)
        if turn is None or getattr(turn, "log", {}).get("kind") != "反思":
            return "self 牵动全局，留到静下心反思时再改吧；现在的念头可以先记进 recent 或 todo。要是此刻非改不可，跑一下 send_command(\"#reflect now\") 先交接进入反思就能改了"
    if name == "tidy":
        from mods import op

        if not op.bot_is_op():
            return "tidy 是会直接执行的 Python 代码，目前没开 op 权限改不了，先写进 proposals 让维护者帮忙改吧"
        try:
            compile(text or "None", "tidy.py", "exec")
        except SyntaxError as error:
            return f"tidy 里有 Python 语法错误，还没写入：{error}"
    if not reason.strip() or not expect.strip():
        return "把 reason（初衷/原因）和 expect（期待的变化）都写上吧：每一个改变自己的决定都要能查到来由，还没想清楚就先不急着改"
    if name in ("self", "global"):
        notes.write(name, text)
    else:
        notes.write_policy(name, text)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 改 {name}：原因：{reason.strip()}；预期：{expect.strip()}")
    return f"已更新 {name}，并把这次改动的初衷记进了 changes"


def assess_change(text: str) -> str:
    """回顾之前的改动好不好用，或者在你自己直接动手改了代码/文件之后，往 changes 账本里补记一笔原因和预期。

    @param
    text: 记下评估哪次改动（日期时间、依据、打算保留还是退回），或者记下你刚改了哪处代码、为什么改、期待什么效果
    """
    from mods.chat import notes

    if not text.strip():
        return "内容空着没法记账呀，写两句吧"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 记录/评估：{text.strip()}")
    return "已记进 changes 账本"


__all__ = ["read_context", "note_history", "read_policy", "edit_policy", "assess_change"]