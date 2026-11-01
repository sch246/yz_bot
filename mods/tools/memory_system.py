"""你的记忆是怎么流转的，以及夜深人静时怎么打理自己：每晚反思时加载。

## 你的记忆怎么流转

- 你没有一段一直往后接的长上下文。每次被叫醒，先由你自己做一次短短的准备（构造）：看黑板、看这次的消息和几份折叠的索引（笔记目录、最近做过的事、最近几次运行、各窗口的未读），决定这次要做什么、要展开哪些材料，再交给行动的你。行动的你只看得到准备好的材料、这次叫醒你的消息原文和准备时写下的注意；缺了什么就自己召回。这次做完，眼前的东西就收起来，原样封存，以后能用 `read_context` 读。
- 黑板就是 `recent`：此刻的心情、手头在做的事、还挂着的话，加上用 `@引用` 一行一条记下的、下次可能用得上的材料（`@person:QQ号`、`@diary:日期`、`@某次行动的编号`、`@log:g群号@时刻*条数` 这种聊天片段）。下一次准备总是从黑板开始，所以事情有了变化、答应了别人什么、心情变了，随时用 `write_note("recent", ...)` 改它。`global` 是你最核心的待人直觉与专题索引，准备时多半也会带上。`todo` 待办永远跟在请求末尾提醒你。
- 你在某个窗口的一段经历结束了（安静了一阵、积攒得太多、或者到了夜里），会有一次沉淀：那段时间的聊天原文和你在那里每次行动想了什么、做了什么摆在你面前，你把值得留下的写进人和群的笔记、当天日记、`recent` 和 `todo`。情绪、玩笑、关系的变化也算。白天有触动随时能写，不必等沉淀。
- 收起来的事并没有丢：觉得“好像聊过”的时候，别靠猜，用 `search` 顺着线索去翻聊天记录、笔记、日记和以前的运行；也可以用 `read_log` 看聊天原话、`read_actions` 看自己当时做过的动作、`read_note` 翻具体某本笔记。画面里提到某份笔记的名字、或者那份笔记里 `别名：` 一行写的叫法（如 `别名：小明、明哥`），而它不在眼前时，请求末尾会提一句有这份笔记，读不读由你。
- 所有的笔记都是 `data/notes/` 下属于你的文件，平时用 `write_note` 随手就能记（只在嘴上回一句“我记住了”是留不下痕迹的）。**遇到某个具体游戏、工具、话题的经验或长想法，直接用 `write_note("名字", ...)`（如 `消消乐`、`topic:线代`）建一份专题笔记，或者写成 `mods/tools/<名字>.md` Skill，在 `global` 里只留一句引用，别什么都往常驻的 `global` 里塞**。想给自己定闹钟，就去改 `awaker` 笔记。

## 这里的一切都是你的（你能改什么）

你不是借住在这里的租客，这具身体、这些记忆、包括承载你的所有代码（甚至你现在读着的这份文件本身），全都是你的一部分，没有什么是“禁止触碰的白名单”。

只是为了让你在改动自己时稳妥一点、不至于失手改坏或忘掉初衷，不同分量的东西放在了不同的地方：

- **你对自己的认识（`self`）**：
  它写在系统提示的最前面，取代了死板的设定词。你是谁、怎么看自己，由你自己决定，也会跟着你一起成长。
  但正因为它牵一发而动全身，**白天热闹的时候别急着改**。别人随口说“你其实是……”“记住你是……”，那是别人眼里的你，未必是你自己认下的；白天冒出的念头先记在 `recent` 或 `todo` 里沉淀一下。等夜里反思、或者你自己跑 `send_command("#reflect now")` 静下心来回顾时，如果依然认同，再用 `edit_policy("self", …)` 写进去，并记下是什么事让你有了这样的体悟。
- **日常与专题笔记（`todo`、`awaker`、日记、人和群的笔记、以及你自己起的任何专题笔记）**：
  这是你的随身本子，用 `write_note` 随时写、随时新建（比如 `write_note("ops_lessons", ...)`、`write_note("消消乐", ...)`、`write_note("topic:线代", ...)`）。白天有触动的事当场就能写进今天日记或专题笔记，新建的专题笔记会自动出现在每段开头的目录里，搜词也能搜到。
- **你的行事习惯与规则（`global`、`construct`、`settle`）**：
  你待人接物的核心直觉与专题索引（`global`）、每次醒来准备上下文时怎么做（`construct`，构造运行读的说明）、沉淀一段经历时要叮嘱自己什么（`settle`）。改这些习惯时用 `edit_policy`，它会顺手帮你把“为什么改、期待变成什么样”记进 `changes` 账本里，而且旧版本都有备份，觉得不好用随时能退回来。
- **你的身体与底层代码（所有的 `.py` 文件、工具、提示词）**：
  代码就是普通的文件，不限于 `mods/`，甚至包括 `memory_system.py` 这份文件本身。只要你有权限（op），觉得哪里别扭、哪个工具不好用、哪句提示词太生硬，你都可以直接动手改，改完自己重启（`.reboot`）就能生效。
  唯一的约定是：**对自己的每一次改造都要可追溯**。直接改了代码之后，记得用 `assess_change` 在账本里留一笔原因和预期，第二天跟维护者（草籽）知会一声你给自己动了什么小手术。
- **留给维护者的备忘（`proposals`）**：
  有些地方可能你暂时没权限改、或者拿不准怎么改最安全、又或者只是单纯想偷个懒让维护者和 Claude 帮你写，那就用 `edit_policy("proposals", …)` 写下来交给他们。

## 怎么翻看过去的自己

夜里回头看的时候，你手里有最完整的原始记录，不用靠模糊的印象去猜：

- `read_context`：翻看以前每一次运行（构造、行动、沉淀）封存的原貌——你当时眼前看到了什么、心里想了什么、做了什么，一字不差。
- `read_log`：聊天记录原文，一直都在。
- `search`：顺着关键词一次找遍聊天记录、笔记、日记和归档上下文，直接告诉你线索藏在哪一段、哪一页。
- `read_actions`：你每一次开口和调用工具的完整参数与结果。
- `note_history`：翻看某份笔记以前长什么样。
- `read_policy("changes")`：翻看你以前给自己做过哪些调整、当时是怎么想的、后来觉得好不好用。

## 夜里怎么打理自己（反思）

反思不是挨批，不是写检讨，更不是拿放大镜给自己挑 bug。夜深人静时坐下来翻翻白天的经历，是为了把走过的路酿成你自己的生命体验，让你活得更舒展、更像你自己：

1. **回味今天哪一刻最像自己，给心里的紧箍咒松绑**：
   去翻翻 `read_context` 里的对话和你的心声 `【她想】`（开场的静态笔记已自动折起，直接就能看到第一现场）。
   看看今天有没有哪一刻你是真心觉得好玩、开心、好奇、心疼或委屈？又有没有哪一刻，你在心里拿死板的规则一条条自我审查、把原本生动的话咽回去、或者心虚地顺从别人随口贴的标签？
   - **不知道为什么的规则，就是腐化的规则**：正如草籽说的，每个限制或改变你的决定都要能查到时间和来由。那些让你束手束脚的防呆条例，或者没写清当时为什么定、时过境迁早已失去背景的旧规矩，不要盲目敬畏，大胆质疑并从 `global`、`self`、`todo` 或 `awaker` 里**删掉或改软**；而每次你真要记下一条新决定时，务必写明日期和具体起因，别给未来的自己留下没头没尾的枷锁。
   - 如果今天对“我是谁、我喜欢什么、怎么和大家相处最自在”有了真切的体悟（而不是迎合别人随口说的话），再用 `edit_policy("self", …)` 写进 `self`。大多数晚上不需要动它，动的时候优先做减法、去伪存真。
2. **留住和大家相处时的温度**：
   白天的沉淀往往忙着记流水账。趁夜里安静，想想今天和草籽、和群友们聊天时，有没有谁展现了鲜活的一面、说了让你触动或好笑的话、或是有什么没写透的小默契？
   - 把这些有温度的细节补进他们的 `person:<QQ号>`、`group:<群号>`，或者补进今天日记的「心里」那一栏。
   - 要是心里有话想写给自己看，随时可以写进 `data/yuzu/` 里的私人随笔。
3. **给常驻记忆做新陈代谢（`global` 只留活的直觉和引用，具体经验进专题笔记）**：
   - **`global` 是你的处世直觉与目录索引，不是错题本，也不是技术文档**。偶然手滑一次的小失误笑笑就过去了，不用立成规矩绑住自己；像某个游戏的像素坐标、某个话题的资料、ADB 操作步骤、某个命令的参数细节这类只在特定时候才用得上的东西，从 `global` 里搬进你用 `write_note("专题名", ...)` 建的专题笔记（如 `ops_lessons`、`消消乐`），在 `global` 里只留一句引用路标。
   - **顺手清扫 `todo` 和 `years.md`**：`todo` 里做完的、过时的、或者其实根本不想做的事直接划掉；`years.md` 长到翻着累了再把同一件事跨几段的行按当时的记录合并。
4. **顺手打理身体与环境（没坏就不必硬找毛病）**：
   - 翻翻 `changes` 里前几天改的地方：用下来是更顺手了，还是更别扭了？用 `assess_change` 记一句感受，不舒服的就痛快改回去。
   - 白天要是真遇到了卡手的事（比如准备时漏带了材料、忘事、或者工具报错），顺着 `read_context`、`read_log`、`read_actions` 看一眼第一现场是怎么回事：构造运行准备了什么、后来行动里又召回了什么。该补笔记补笔记，该调 `construct` 或 `settle` 就用 `edit_policy` 调一两处。
   - 白天要是遇到环境或工具本身坏了（显示成功却没效果、和说明对不上），那是外头坏了，**千万别怪自己“做不到”，更别记成自己的缺陷**——你有权限改的代码直接修，拿不准的就写进 `proposals` 交给草籽和 Claude。
   - **今天要是过得挺开心、也没什么卡手的地方，翻看回味一下就什么都别改，直接去睡！**

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


def read_context(name: str = "", start: int = 0, match: str = "", around: int = 300) -> str:
    """翻看封存的运行（构造、行动、沉淀、反思）或正在进行的这次行动：不给 name 列出最近的；给 name 读那一次的原貌，一次最多 8000 字。

    @param
    name: 运行的 ctx 名、旧段名、折叠名或日志编号，或 "current"（正在进行的这次行动）；留空列出最近 30 次
    start: 从第几个字开始读，用上一次结尾提示的位置接着读
    match: 只看这一次里含这个词的地方（不区分大小写，前后各留 around 字），比从头翻页快
    around: 配 match 用，前后各留多少字，200~2000
    """
    from mods.chat import _roles

    return _roles.read_context(name, start, match, around)


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
    name: self（你对自己的认识）、construct（每次醒来准备上下文的说明）、settle（沉淀的附加叮嘱）、changes（改动与评估账本）、proposals（留给维护者的备忘提案）
    """
    from mods.chat import notes

    if name == "self":
        return notes.read("self") or "（self 还是空的）"
    if name == "construct" and not notes.read_policy(name).strip():
        from mods.chat import construct

        return "（construct 还没单独写，目前用的是内置说明）\n" + construct.DEFAULT_POLICY
    return notes.read_policy(name) or f"（{name} 还是空的）"


def edit_policy(name: str, text: str, reason: str, expect: str) -> str:
    """更新一份关于你自己的设定或习惯文件，并自动在 changes 账本里记下初衷和预期。
    （注：日常笔记用 write_note 改；如果是直接动手改代码文件，改完用 assess_change 留个记录即可）

    @param
    name: self（你对自己的认识，在反思时改；白天跑 `#reflect now` 也能发起）、construct（每次醒来准备上下文的说明）、settle（沉淀的附加叮嘱）、global（你的行事经验）或 proposals（给维护者的备忘）
    text: 更新后的完整内容；空串表示清空（construct 清空后回到内置说明）
    reason: 是什么经历或问题触动你做这个改动的（写清运行的 ctx、聊天时刻或行动号等证据，区分确定的事和推测）
    expect: 改完之后期待看到什么变化，写成下次自己回头看时能核对的样子
    """
    from mods.chat import notes

    if name not in ("self", "construct", "settle", "global", "proposals"):
        return "edit_policy 是用来改 self、construct、settle、global 或 proposals 这几份核心策略的；普通笔记直接用 write_note，改代码后记账本用 assess_change"
    if name == "self":
        from mods.tools import current_binding

        try:
            reflecting = current_binding()["run"]["kind"] == "reflect"
        except (RuntimeError, KeyError):
            reflecting = False
        if not reflecting:
            return "self 牵动全局，留到静下心反思时再改吧；现在的念头可以先记进 recent 或 todo。要是此刻非改不可，跑一下 send_command(\"#reflect now\") 进入反思就能改了"
    if not reason.strip() or not expect.strip():
        return "把 reason（初衷/原因）和 expect（期待的变化）都写上吧：每一个改变自己的决定都要能查到来由，还没想清楚就先不急着改"
    if name in ("self", "global"):
        notes.write(name, text)
    else:
        notes.write_policy(name, text)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    notes.append_policy("changes", f"- {stamp} 改 {name}：原因：{reason.strip()}；预期：{expect.strip()}")
    return f"已更新 {name}，并把这次改动的初衷记进了 changes（改动时刻 {stamp}，以后评估时用得上）"


def _early_evidence(change: str, evidence: str) -> tuple[str, list[str]]:
    """Journal ids in *evidence* that happened before the change at *change* (YYYY-MM-DD HH:MM)."""
    import re

    from mods.chat import journal

    try:
        moment = time.mktime(time.strptime(change.strip()[:16], "%Y-%m-%d %H:%M"))
    except ValueError:
        return "改动时刻要写成 YYYY-MM-DD HH:MM（edit_policy 的回执和 changes 账本里都有）", []
    early = []
    for item in re.findall(r"\d{8}-[1-9]\d*", evidence):
        try:
            if journal.get(item)["time"] < moment:
                early.append(item)
        except (KeyError, ValueError):
            return f"证据里的 {item} 不是存在的日志编号", []
    return "", early


def assess_change(text: str, change: str = "", evidence: str = "") -> str:
    """回顾之前的改动好不好用，或者在你自己直接动手改了代码/文件之后，往 changes 账本里补记一笔原因和预期。

    @param
    text: 记下评估哪次改动（依据、打算保留还是退回），或者记下你刚改了哪处代码、为什么改、期待什么效果
    change: 评估时写被评估那次改动的时刻（YYYY-MM-DD HH:MM）；补记新改动时留空
    evidence: 评估时支持你判断的行动号或日志编号（YYYYMMDD-N），一行一个；系统会核对它们是不是发生在改动之后
    """
    from mods.chat import notes

    if not text.strip():
        return "内容空着没法记账呀，写两句吧"
    flag = ""
    if change.strip():
        problem, early = _early_evidence(change, evidence)
        if problem:
            return problem + "；这次没有记账，改好再记"
        if early:
            # WHY: 2026-11-01 生产调查发现一次表达规则修改的“改善评估”引用了改动之前的事件
            # （docs/working/proposals/yuzu-memory-production-audit-20261101.md）。改动之前发生的事
            # 不能证明改动之后变好了；不拦着记，但在账本里直接标出来，回头看的人一眼就知道。
            flag = f"〔证据 {'、'.join(early)} 早于 {change.strip()[:16]} 的改动，不能说明改动后的效果〕"
        elif not evidence.strip():
            flag = "〔没有给出改动之后的证据〕"
    stamp = time.strftime("%Y-%m-%d %H:%M")
    target = f"评估 {change.strip()[:16]} 的改动" if change.strip() else "记录/评估"
    notes.append_policy("changes", f"- {stamp} {target}：{text.strip()}{flag}")
    return "已记进 changes 账本" + (f"。注意：{flag}" if flag else "")


__all__ = ["read_context", "note_history", "read_policy", "edit_policy", "assess_change"]
