'''日常表达、记忆召回与工具加载的基础能力。

你在同一步可调用多个工具，它们按顺序执行，但参数一起生成，彼此看不到结果；依赖前一步结果的操作放在下一步。
自己的记忆怎样流转已在身份后的“你的记忆”中说明。行动的上下文由构造选择材料，构造给的情境、观察和联想供你判断；怎样回应和行动由你决定。

## 材料、记忆与文件读写

笔记、日记和策略都是 `data/notes/` 下的普通 Markdown 文件，上下文里的笔记标题直接标明文件路径（如 `data/notes/diary/YYYY-MM-DD.md`、`data/notes/recent.md`）。`@L12-28` 表示只带了原文件第 12–28 行，其余没有载入。
- `read_file` 读取任意文件或笔记（支持 `start`/`end` 左闭右开行区间，也支持直接传笔记名如 `topic:线索` 或 `topic:线索@L12-28`；传目录路径则列出目录内容）。
- `write_file` 修改或新建文件与笔记：支持传 `old_string` 精准局部替换（最推荐，改长笔记、追加日记或人物经历时只需替换/衔接局部，不必整篇重写）、传 `start`/`end` 按行区间重写、或不传 `old_string`/`start`/`end` 整份覆写（`data/notes/` 下传空串整份覆写会删除该笔记）。`data/notes/` 下的文件修改或删除前会自动备份到 `data/notes/.history/`。
- `search` 找线索，`read_log` 翻聊天原文，`read_actions` 看自己以前的输出和工具结果。`todo`（`data/notes/todo.md`）是待办本子，构造会看到全文并选择行动需要的部分；随时可用 `read_file` 和 `write_file` 读取或更新。

## 工具与 Skill

目录列可用模块；`load_tools` 给当前运行加载函数和说明。行动开场由构造选择模块，不自动继承上次用过的全部工具；本次加载的工具在本次运行内可继续用。反思与沉淀仍恢复持久激活态。
`list_tools` 查看状态；改了文件用 `reload_tools`，只有 `mods/tools/` 的模块和下划线 helper 能热更新，其它核心代码需完整重启。
需要开发、改写、删除工具或写 Skill 时，`load_tools(["tool_development"])` 展开手册。`exec_code` 是共享 Python 环境，不是沙箱；函数说明里有时限和后台选项。`attach_image` 把图片递到眼前。

工具列表包含 `reframe` 时，材料需求变了可以单独调用它，选下一段的 focus、refs、接续 note 和工具。它在同一次任务里继续，旧轨迹按 ctx 封存；不会把旧探索全文自动搬过来。reframe 必须独占一次工具批次，不能和其它工具一起调用。

## 怎么开口说话（send 与 wait）

你看的每条聊天记录都带一个头：`【头衔】名字(QQ号) 时:分:秒 | message_id`，正文缩进在下一行。头里那个 QQ 号要是你自己，那就是你之前说过的话。想艾特谁写 `[CQ:at,qq=号码]`，想引用哪条就在正文里写 `[CQ:reply,id=message_id]`，id 就是竖线后面那串。

你说话就像真人发消息一样，直接调用 `send(target, text="你想说的话", wait=...)` 即可一步发出。直接写在模型回复正文里的文字全是你心里的自言自语（外面的人看不见），调用 `send` 才是真正让别人听见你说话的方式。每次发送都要写清窗口名字，比如 `target="g<群号>"`、`target="u<私聊QQ号>"` 或 `bili:<BV号>`（`me` 是写给自己看的窗口）。

- **分段与真人节奏**：QQ 里在正文用空行隔开的几段话，`send` 时会像真人聊天一样，隔一两秒一条、拆成几个气泡依次发出去，并返回消息号（`message_id`）。B 站评论整段发成一条评论，空行就是评论里的分段。CLI 窗口（`dsh:main`、`agy:review`、`codex:code`）把完整输入作为一轮任务，立即返回接收编号，回答稍后回到同一窗口并叫醒你；命令行窗口 `term:名字` 是你自己的长期终端（tmux），整段原样敲进去并回车，输出攒成一批批消息回来，详见工具模块 `term`。
- **wait 控制后续与自主呼吸**：`send` 的 `wait` 参数决定发完这句之后你是继续活动还是挂着喘气：
  - `wait=0`：不等回复，发完直接跑下一轮，手里还有别的事情接着做；
  - `wait=-1`：发完收工，纯被动等待（相当于原 `final_call=true`），没有超时闹钟；
  - `wait="20m"`（或 `"5m"`、`300` 等）：在当前窗口挂一个超时等待规则（由 awaker 统一调度）。等待期间如果对方（或群里）有人接话，新消息会自动认领并叫醒你；如果超时一直没人回复，awaker 会到点叫醒你，带着你设的 `reason`（例如"不来我就接着读日记"）让你自主接着活动。群聊里还可以传 `who=QQ号` 指定只等某个人接话。等待期间叫醒你的消息要是不用接（比如别人插了一句），那条 wait 在叫醒你时已经用掉了；还想接着等，就调 `wake_me("wait g123:QQ号 10m", 理由)` 重新挂上。
- **看到对方话说了一半想等等看**：如果你看到对方发了半句话、或者话题还没展开想等对方说完，**不要发话打断对方，直接调用 `wait("20s", "等对方把话说完")` 挂起喘气**。
- **发送失败或未确认**：万一没发出去或者状态“未确认”，系统也会再跑一轮让你看清状况。如果是**“未确认”**，说明请求已经递出去了只是没拿到回执号，那句话十有八九已经发进聊天里了——这时输入框已经清空，千万别直接重发，先看下一轮聊天记录里有没有它。
- **被叫醒不等于必须开口**：大家闲聊不一定有明确的问题。别人已经答完、话题和你无关、或者你没什么想说的，就安静结束这一轮——不用调 `send`，少说比多说好。真不想接的时候，丢张表情包或者回个问号也行。'''

from __future__ import annotations

import contextvars
import io
import random
import re
import time
from typing import Mapping

from mods.tools import current_binding


# WHY: `back` 显式写进签名，不用函数属性 `.back`：同步模式要在启动前拒绝 timeout=0，
# 这个判断需要拿到调用方传的 back；属性版只把它交给绑定层，函数自己看不到。后台
# exec_code 也不走 `_tasks` 的那条单队列：timeout=0 可能真的无限等，排进去会把后面的
# 后台工具永远堵住，所以用独立线程。
def exec_code(expr: str, code: str, timeout: float = 15, back: str = "wait") -> str:
    """现成工具不够用、需要直接跑 Python 或组合连续操作时，在 Bot 的共享环境里先跑 code 再算 expr，返回求值结果和捕获的 print 输出。

    可在代码里直接组合多个工具模块与操作（如用 PIL 裁切/放大图片后调用 attach_image 递给自己看，一步完成复合任务）。这不是沙箱，里面的变量跨调用一直留着，读写文件、发消息、改运行状态都会真生效（需要 Bot 有 op 权限）。

    @param
    expr: 最后求值并返回的表达式；只跑 code、不用返回值时传 "None"
    code: 先执行的 Python 代码，可写多行；不用时传空串
    timeout: 最多跑几秒，默认 15；传 0 不限时只允许配 back="wake" 或 "quiet" 放后台
    back: wait 等跑完直接拿结果；wake 和 quiet 立刻回执，跑完把结果推到 me 窗口（quiet 不叫醒你）
    """
    from mods import context, op, py, watchdog

    if not op.bot_is_op():
        return "权限不足"
    if back not in ("wait", "wake", "quiet"):
        return "back 只能是 wait、wake、quiet"
    if timeout < 0:
        return "timeout 不能是负数"
    if back == "wait" and timeout == 0:
        return (
            '同步执行不能 timeout=0：改用 back="wake" 或 "quiet" 放到后台，'
            "或者把顺序写进代码、每一步等结果"
        )
    # WHY: 代码现在跑在一个子线程里（见 watchdog.run），所以它在代码里改
    # `context.set_current` 再也影响不到本线程。工具本身仍然可能改，所以照旧进来先快照、
    # 出去无条件还原：工具可以读路由，但不该在自己脚下把它换掉——2026-09-17 就因此把私聊的
    # 回复发进了群。
    original_event = context.current()
    original_agent_mode = context.agent_mode()
    buffer = io.StringIO()

    # WHY: print 的捕获必须跟着被执行的那段上下文走。watchdog.run 起的是新线程，
    # `py.loc["print"]` 这种进程级改写会把并发调用的输出混在一起，所以改成 py.py 里的
    # ContextVar，由这里在局部上下文中进入捕获；用户代码自己再起线程时，py.py 会把
    # 捕获流随线程创建带过去。
    def run():
        with py.capture_print(buffer):
            exec(code, py.loc)
            return eval(expr, py.loc)

    # WHY: watchdog.run 起的是新线程，ContextVar 不会跟过去，代码里调 `_vision.attach` 就拿不到
    # 这次的会话、交不出图。脚本操作手机时要把截图交给她看（维护者 2026-10-24：让她用 exec_code
    # 组合手机操作），所以在调用方上下文的副本里跑。
    scope = contextvars.copy_context()

    def perform():
        return scope.run(run)

    def task() -> str:
        # WHY: wake/quiet 会把这个 task 交给后台线程。后台线程可能被复用，路由器又是
        # thread-local 的；进来先装回调用方的事件和 agent_mode，结束后还原后台线程原来
        # 的状态。只还原成 original_event 会把调用方窗口永久写进常驻 worker，下一个后台
        # 任务（以及 ^C 的 owner_key）就认错窗口。
        previous_event = context.current()
        previous_agent_mode = context.agent_mode()
        context.set_current(original_event)
        context.set_agent_mode(original_agent_mode)
        try:
            try:
                result = repr(watchdog.run(perform, timeout))
            except TimeoutError as error:
                result = f"超时：{error}"
            except watchdog.Interrupted:
                result = "已被 ^C 中断"
        finally:
            context.set_current(previous_event)
            context.set_agent_mode(previous_agent_mode)
        printed = buffer.getvalue().rstrip()
        return f"[print输出]\n{printed}\n[结果] {result}" if printed else result

    if back != "wait":
        from mods.tools import _tasks

        return _tasks.submit("exec_code", contextvars.copy_context(), task, back, detached=True)
    return task()


def list_tools() -> str:
    """想看现在有哪些工具和 Skill 可用、哪些已经激活、或者刚改的模块有没有报错时，列出完整的工具目录与当前状态。"""
    from mods import tools

    return tools.list_text(current_binding())


def reload_tools(names: list[str]) -> str:
    """新建、改写或删掉 mods/tools/ 下的模块文件后，把磁盘上的改动真正应用进来，返回每个模块的加载结果或报错栈。

    只改文件不调它，生效的还是旧版本；要是新代码有错，会原样保留旧版继续用，不会把正在用的工具弄坏。

    @param
    names: 要应用的模块名列表（不带 .py/.md 后缀）；删了源文件的模块也列在这里才会正式卸掉
    """
    from mods import tools

    return _format_results(tools.reload(current_binding(), names))


def load_tools(names: list[str]) -> str:
    """需要用到某个工具模块或想展开某份 Skill 说明时，把它装进当前会话，下一步就能直接调它的函数、看到它的全文。

    在本次运行内保持激活；下次行动由构造重新选择。它只装已生效的版本，刚改了磁盘文件要先 reload_tools。

    @param
    names: 要装入的模块名列表（不带 .py/.md 后缀，可从可用工具目录或 list_tools 里看）
    """
    from mods import tools

    return _format_results(tools.load(current_binding(), names))


def reframe(focus: str, refs: str = "", note: str = "", tools: str | None = None,
            history: int | None = None) -> str:
    """当前材料已经不适合接下来的工作时，选择材料换到新 ctx，在同一次任务里继续。

    必须独占这次工具批次。校验失败留在原段继续；成功后旧 ctx 仍可用 read_context 查。
    取消、原目的、父运行的能力限制和已有总时限会延续。

    @param
    focus: 下一段关注的情境或问题，不能为空；允许随发现调整方法
    refs: 下一段要带的材料，每行一个日志编号、ctx、笔记、选区、policy: 或 log: 引用；空串不带旧材料
    note: 给下一段的接续说明，记录为本次新产生的材料，不必搬整份旧轨迹
    tools: 下一段激活的工具模块，用换行、空格或逗号分隔；省略继承当前选择，空串清空可选模块，meta 始终保留
    history: 仅主行动可调下一段新消息前的接话条数，非负整数；省略继承，不改变负责的窗口
    """
    state = current_binding()["run"]
    prepare = state.get("prepare_reframe")
    if prepare is None:
        return "这次运行不支持 reframe；原段继续"
    return prepare(focus, refs, note, tools, history)


def read_log(window: str, count: int = 30, before: str = "") -> str:
    """想翻看某个窗口以前的聊天原话、或者顺着 search 找到的时间点看前后文时，从后往前读那个窗口的聊天记录原文。

    @param
    window: 窗口名，如 g<群号>、u<私聊号>、me（你自己的闹钟和后台回执窗口），或画面里出现过的 种类:目标
    count: 往前取多少条，1 到 200
    before: 翻页锚点，写 YYYY-MM-DD HH:MM:SS 或 Unix 秒，只取这个时刻之前的记录；留空从最新一条往前取
    """
    from mods import chat

    return chat.roles.read_log(window, count, before)


def search(keyword: str, window: str = "", days: int = 30) -> str:
    """觉得某件事好像聊过、记过，但拿不准在哪时，顺着关键词一次找遍聊天记录、笔记、日记和归档上下文，返回每处命中的摘要和出处。

    找到线索后，再用 read_log 看聊天前后文、read_file 看笔记或日记全文、或者加载 memory_system 后用 read_context 翻归档段。

    @param
    keyword: 要找的词，名字、QQ 号、话题词都行（不区分大小写，按原文子串匹配）
    window: 只在指定窗口的聊天记录里找（如 g<群号>、u<私聊号>）；留空找所有窗口，笔记、日记和归档始终全找
    days: 聊天记录和归档往前翻多少天，1 到 365；笔记和日记不限时间
    """
    from mods import chat

    return chat.roles.search(keyword, window, days)


def read_actions(ids: list[str] | None = None, count: int = 10, keyword: str = "", start: int = 0) -> str:
    """想确认自己之前想了什么、调过什么工具、拿到了什么结果时，翻看你自己过去的行动记录原文。

    单次最多返回 8000 字，没读完的照着结尾提示的 start 往后翻。

    @param
    ids: 指定要看的行动号列表，如 ["20261019-12"]；留空则看最近的记录
    count: 不指定 ids 时看最近几条，1 到 50
    keyword: 只筛出原文里包含这个词的行动（不区分大小写）
    start: 从第几个字接着往下读，填上一页结尾提示的数字
    """
    from mods import chat

    return chat.roles.read_actions(ids, count, keyword, start)


MAX_READ_BYTES = 32 * 1024
_FILE_HEADER = re.compile(r"^file:\s*.*?\s*\|\s*(lines:\s*\[\s*-?\d+\s*,\s*-?\d+\s*\)|line:\s*-?\d+)")
_FILE_REST = re.compile(r"^\.\.\.rest \d+ lines?$")


def _human_bytes(size: int) -> str:
    return f"{size} B" if size < 1024 else f"{size / 1024:.1f} KB"


def _clamp_lines(lines: list[str], start: int = 0, end: int | None = None) -> tuple[int, int]:
    total = len(lines)
    if start < 0:
        start += total
    start = max(0, min(start, total))
    if end is None or end > total:
        end = total
    elif end < 0:
        end = max(start, total + end)
    else:
        end = max(start, min(total, end))
    return start, end


def _render_file_window(path: str, lines: list[str], start: int, end: int) -> str:
    header = f"file: {path} | lines: [{start}, {end}) | total: {len(lines)}"
    body = [f"{index}│{line}" for index, line in enumerate(lines[start:end], start)]
    remaining = len(lines) - end
    if remaining > 0:
        body.append(f"...rest {remaining} line" + ("s" if remaining != 1 else ""))
    else:
        body.append(f"─── EOF ({len(lines)}) ───")
    return "\n".join([header, *body])


def _missing_file(path: str) -> str:
    import difflib
    import os

    parent = os.path.dirname(path) or "."
    try:
        names = os.listdir(parent)
    except OSError:
        return f"路径不存在: {path}"
    close = difflib.get_close_matches(os.path.basename(path), names, n=3)
    if not close:
        return f"路径不存在: {path}"
    nearby = "、".join(os.path.join(parent, name) for name in close)
    return f"路径不存在: {path}\n同目录下相近的有: {nearby}"


def _clean_file_content(text: str) -> str:
    from mods import file

    body = text.splitlines()
    if body and _FILE_HEADER.match(body[0].strip()):
        body = body[1:]
    if body and (_FILE_REST.match(body[-1].strip()) or body[-1].strip().startswith("─── EOF")):
        body.pop()
    return file.strip_linemark("\n".join(body))


def _resolve_target(path: str) -> tuple[str, tuple[int, int] | None]:
    from mods.chat import notes

    try:
        return notes.resolve_path(path)
    except ValueError:
        return path.strip(), None


def read_file(path: str, start: int = 0, end: int | None = None, max_bytes: int = MAX_READ_BYTES) -> str:
    """读取文件或笔记的一段并带行号返回；传目录路径则返回目录列表。

    @param
    path: 文件或目录路径（如 data/notes/diary/2026-11-03.md、mods/chat/agent.py），也支持直接写笔记名（如 recent、todo、person:<QQ号>、diary:<日期>、policy:construct、topic:线代@L12-28）
    start: 闭区间起始行号（包含本行），从 0 数；负数表示从末尾倒数，默认 0
    end: 开区间结束行号，查看止于此行之前（不包含本行）；留空或超出则一直读到文件末尾（EOF）
    max_bytes: 本次截取内容的字节上限，默认 32768；超出则不展开内容，只回大小和后续建议
    """
    import os

    from mods import file, op
    from mods.chat import notes

    target, slice_range = _resolve_target(path)
    if slice_range is not None and start == 0 and end is None:
        start, end = slice_range
    if not notes.is_note_path(target) and not op.bot_is_op():
        return "权限不足"
    if os.path.isdir(target):
        return file.listdir(target)
    try:
        lines = file.read(target).splitlines()
    except FileNotFoundError:
        return _missing_file(target)
    except (OSError, UnicodeDecodeError) as exc:
        return f"读取失败: {exc}"
    start, end = _clamp_lines(lines, start, end)
    measured = len("\n".join(lines[start:end]).encode("utf-8"))
    if measured > max_bytes:
        return (
            f"file: {target} | lines: [{start}, {end}) | total: {len(lines)} | {_human_bytes(measured)}\n"
            f"超过 {_human_bytes(max_bytes)} 未展开。可以用 start/end 缩小读取区间，"
            f"或用 agents__fork 开子会话通读后只带回摘要；"
            f"确实要整段进上下文时再调高 max_bytes。"
        )
    return _render_file_window(target, lines, start, end)


def attach_image(uri: str, note: str = "", model: str = "", refresh: bool = False) -> str:
    """遇到聊天图片、评论配图、截图或验证码这类得亲眼看了才知道的内容时，把图片交给你自己看；当前模型不支持视觉或指定了视觉模型时会自动转述。

    @param
    uri: 图片地址，支持 file://、http(s):// 或 data:，工具或评论里给的地址直接传进来就行
    note: 附在图前的备忘或具体提问，提醒重点看什么，如"看清图里的报错字样"、"提取所有文字"
    model: 留空表示交给默认视觉模型转述（当前为 gemini/gemini-3.7-flash）；也可点名模型，如 "gemini"（中转站 3.7 Flash）、"google"（官方 2.5/3.7）、"leaf"（叶子）、"luna"、"cheap" 或完整供应商模型名（如 "gemini/gemini-3.8-flash"）
    refresh: 设为 True 时强制刷新缓存，让视觉模型重新看一眼（跳过并更新已有图片描述）
    """
    from ._vision import attach

    return attach(uri, note, model=model or None, refresh=refresh)


def _box_window(target: str) -> tuple[tuple, str]:
    """The window *target* names and its label; ValueError when it is unknown or cannot be answered.

    ``me`` 也算能说话的地方：夜里交接、反思时把话打进自己的窗口，就是一声自言自语，
    第二天醒来自己看得见。WHY: 2026-10-27 早上草籽问「如果半夜反思完真想说话，只能闷着
    写在日记里吗」，拆掉了「现在是交接或反思，不对外说话」那道代码胶带——不半夜吵人是
    待人的常识，不该由代码替她封嘴。
    """
    from mods.chat import windows

    window = windows.parse(target)
    label = windows.label(window)
    if windows.sender(window) is None and tuple(window) != windows.ME:
        raise ValueError(f"{label} 只能看，不能在那里说话")
    return window, label

def _boxes() -> dict:
    """The input boxes, one per window; persisted in storage across wakes and restarts.

    WHY: 维护者 2026-10-28：输入框草稿像真实 QQ 一样保留在各个窗口里，发了才消失；
    存入 storage（内存权威+延迟刷盘），平时纯内存读写无 IO 开销，关机重启也不丢草稿。
    """
    from mods import storage

    return storage.get("", "boxes")


def _this_output() -> str | None:
    from mods.tools import current_binding

    try:
        state = current_binding()["run"]
    except RuntimeError:
        return None
    action = state.get("action")
    return action.split("#")[0] if isinstance(action, str) else None


def type_word(target: str, text: str) -> str:
    """把话打进对应窗口的输入框暂存（通常直接用 send(target, text=...) 一步发出去即可，仅在需要暂存草稿时使用）。

    @param
    target: 要说话的窗口，如 g<群号>、u<私聊号>、bili:<BV号>、me，或画面里出现过的可回话窗口
    text: 要打进输入框的完整内容，CQ 码（at、回复、图片）直接写；传空串清空输入框
    """
    try:
        _window, label = _box_window(target)
    except ValueError as error:
        return str(error)
    boxes = _boxes()
    body = str(text)
    if not body.strip():
        boxes.pop(label, None)
        return f"{label} 的输入框已清空"
    boxes[label] = {"text": body, "typed_in": _this_output()}
    return (f"{label} 的输入框已暂存：\n{body}\n"
            f"（日常开口可直接调用 send(\"{label}\", text=...) 一步发送；不想发了可传空串清空输入框）")
MAX_BUBBLES = 4
BUBBLE_GAP = (1.0, 2.0)


def _split_turns(text: str) -> list[str]:
    """按空行把一段话拆成几个气泡，最多 MAX_BUBBLES 个。

    WHY: 维护者 2026-10-24 让 Claude 看过 7 天记录：私聊里柚子一半的消息分了段，对方只有 3%，
    真人是一条一条发。拆开之后每段自己是一个气泡，中间隔一秒到两秒（BUBBLE_GAP）。
    """
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) <= 1:
        return [text]
    if len(blocks) > MAX_BUBBLES:
        blocks = blocks[:MAX_BUBBLES - 1] + ["\n\n".join(blocks[MAX_BUBBLES - 1:])]
    return blocks


def send(
    target: str,
    text: str = "",
    wait: int | float | str = -1,
    reason: str = "",
    who: int | str | None = None,
    force: bool = False,
) -> str:
    """开口说话：直接把话发进目标窗口，返回发出的消息号（message_id）。

    发出去的话别人当场就能看见；QQ 里用空行分段的文字会像真人一样隔一两秒拆成几条依次发出，B 站评论和 CLI 窗口整段一次发出。遇到“未确认”说明多半已经发出去了，先看下一轮聊天记录，别直接重发。

    @param
    target: 要发送的窗口（如 g<群号>、u<私聊号>、bili:<BV号>、me）
    text: 想对这个窗口说的话，支持 CQ 码（at、回复、图片、表情）；直接一步发出。传空串时会发送该窗口输入框草稿
    wait: 发完后怎么安排后续。传 0 表示不等、发完继续这一轮干手头的活；传 -1 表示结束这轮、纯被动等待；传等待时长（如 "5m"、"20m"、"1h" 或秒数 300）表示在当前窗口挂一个超时等待闹钟，期间有人回复立刻叫醒，到点超时还没人回也会自动叫醒你继续呼吸；若传字符串时带有 force（如 "10m force"）或设置 force=True，开启强制等待闸门（期间绝不打扰，到期汇总提醒叫醒你）
    reason: 挂超时等待（wait > 0）时写给到时候自己的理由，如"不来我就接着读日记"、"等草籽接话"；留空默认"等回复"
    who: 在群聊窗口中指定只等特定人回复（填 QQ 号，如 123456；传 "any" 等群里任何人）。若不填：在有人跟你说话的轮次里自动默认等刚才跟你说话的人，避免路人插话吞掉等待；在你自己醒来的轮次（闹钟、订阅等无对话人轮次）不填则等待群里任何人回复。命令行窗口（term:名字）里填一段文字，等输出里出现它才算等到（同一批输出里出现才算）
    force: 是否开启强制等待闸门（上限 1 天）。传 True 期间绝不叫醒你，消息正常登记未读，等时间到了将所有积压提醒按窗口归类合并后一次性叫醒你
    """
    from mods import context
    from mods.chat import awaker, windows
    from mods.tools import current_binding

    try:
        window, label = _box_window(target)
    except ValueError as error:
        return str(error)
    boxes = _boxes()
    content = str(text) if text else ""
    # Backward compatibility: old send(target, wait=..., ...) passed wait as 2nd positional arg
    if content and boxes.get(label):
        s = content.strip().lower()
        if (
            isinstance(text, (int, float))
            or s in ("0", "-1", "0s", "-1s", "true", "false", "off", "none")
            or s.startswith("wait ")
            or re.match(r"^\d+[smhd]$", s)
        ):
            wait = text
            content = ""

    if not content.strip():
        box = boxes.get(label)
        if not box or not box.get("text", "").strip():
            return f"{label} 的输入框是空的，请直接在 send(target, text=\"...\") 中传入想说的话"
        content = box.get("text", "")

    if context.agent_mode():
        current_binding()["run"]["associated_windows"].add(window)

    wait_mode = "passive"  # "continue" (0), "passive" (-1), "wait" (>0)
    wait_seconds = 0
    wait_target = label
    wait_str = str(wait).strip()
    if "force" in wait_str.lower():
        force = True
        wait_str = re.sub(r"\bforce\b", "", wait_str, flags=re.IGNORECASE).strip()
        wait = wait_str

    if wait == 0 or wait_str in ("0", "0s", "false", "False"):
        wait_mode = "continue"
    elif wait == -1 or wait_str in ("-1", "-1s", "true", "True", "off", "none", "None", ""):
        wait_mode = "passive"
    else:
        wait_mode = "wait"
        cleaned_wait = wait_str
        if cleaned_wait.startswith("wait "):
            cleaned_wait = cleaned_wait[5:].strip()
        parts = cleaned_wait.split()
        who_parsed = None
        dur_str = ""
        if len(parts) == 1:
            dur_str = parts[0]
        elif len(parts) >= 2:
            if re.search(r"^\d+[smhd]", parts[0]):
                dur_str = parts[0]
                if not reason:
                    reason = " ".join(parts[1:])
            else:
                who_parsed = parts[0]
                dur_str = parts[1]
                if not reason and len(parts) > 2:
                    reason = " ".join(parts[2:])

        target_who = who if who is not None else who_parsed
        wait_text = None
        if target_who is not None and str(target_who).strip().lower() in ("any", "all", "*"):
            target_who = None
        elif target_who is not None and windows.spec(window)["wait_text"]:
            # 命令行窗口的限定词是要等的一段文字，不是人（设计文档 18.2）。
            wait_text, target_who = str(target_who), None
        elif target_who is None and label.startswith("g"):
            # WHY: 2026-10-31 群聊中若未显式指定 who，判一下当前唤醒里该窗口是否有正在对话的人；
            # 只有有具体对话人时才默认绑定其 QQ，避免路人闲聊误吃 wait 规则；
            # 自主唤醒（闹钟、B站订阅等无对话人轮次）不瞎绑人，保持等全群或传 -1 纯被动等待（草籽与柚子定）。
            # 对话人是这次唤醒里叫她的人（agent._adopt 从召唤记下），不是之后路过的人。
            interlocutors = current_binding()["run"].get("interlocutors", {})
            if window in interlocutors:
                target_who = interlocutors[window]

        if target_who is not None and label.startswith("g"):
            who_id = str(target_who).lstrip("u:")
            wait_target = f"{label}:{who_id}"
        else:
            wait_target = label

        dur_seconds = None
        if isinstance(wait, (int, float)) and wait > 0:
            dur_seconds = int(wait)
        else:
            dur_seconds = awaker._interval(dur_str)
        if dur_seconds is None or dur_seconds <= 0:
            return f"看不懂 wait 时长参数「{wait}」，例如传 \"5m\"、\"20m\"、300，或者 0（不等继续）、-1（结束纯等）"
        wait_seconds = dur_seconds

    unconfirmed = (
        "先在下一轮的聊天记录里看它在不在，再决定要不要重发——不要直接重发。"
    )
    sender = windows.sender(window)
    parts = [content] if windows.spec(window)["whole"] else _split_turns(content)
    if tuple(window) == windows.ME:
        # WHY: 2026-10-27 早上草籽拆掉「现在是交接或反思，不对外说话」那道代码胶带——
        # me 是写给自己看的一行，想说的话有地方落下，第二天醒来自己看得见，也不吵到谁。
        from mods import chat

        for part in parts:
            chat.push(windows.ME, "柚子", part)
        boxes.pop(label, None)
        if wait_mode == "continue":
            pass
        else:
            current_binding()["run"]["end"] = True
        return "写进 me 窗口了：" + "；".join(parts)
    sent = []
    try:
        for index, part in enumerate(parts):
            sent.append(sender(window, part))
            if index < len(parts) - 1:
                # 人打下一句要时间：随机停一秒到两秒，别像机器连着吐（维护者 2026-10-24）。
                time.sleep(random.uniform(*BUBBLE_GAP))
    except windows.Unconfirmed as error:
        boxes.pop(label, None)
        return f"未确认：{error}输入框已清空。" + unconfirmed
    except Exception as error:
        if sent:
            boxes.pop(label, None)
            return (f"发送失败（{type(error).__name__}）：{error}。"
                    f"前 {len(sent)} 条已经发出去了，剩下的没发，别再重发前面那些。")
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去。"
    boxes.pop(label, None)

    if wait_mode == "continue":
        pass
    elif wait_mode == "passive":
        current_binding()["run"]["end"] = True
        awaker.lift_wait(label)
    elif wait_mode == "wait":
        current_binding()["run"]["end"] = True
        wait_reason = reason.strip() or (f"等 {wait_target} 出现「{wait_text}」" if wait_text else f"等 {wait_target} 回复")
        dur_label = f"{wait_seconds // 60}m" if wait_seconds >= 60 and wait_seconds % 60 == 0 else f"{wait_seconds}s"
        if force:
            from mods.chat import attention
            attention.force_quiet(wait_seconds, wait_reason)
            result_text = "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
            return f"{result_text}（已立起强制等待闸门 {dur_label}：期间绝不打扰，到期汇总提醒叫醒你）"
        try:
            plant_line = awaker.plant(f"wait {wait_target} {dur_label}", wait_reason, text=wait_text)
        except Exception as error:
            plant_line = f"（挂等待规则失败：{error}）"
        result_text = "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
        return f"{result_text}（已设等待：{plant_line}；来消息或超时都会叫你）"

    return "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
def _confirm_file_write(path: str, start: int, count: int) -> str:
    from mods import file

    try:
        lines = file.read(path).splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return f"已写入 {path}，但回读失败: {exc}"
    if not lines:
        return f"已写入 {path}，文件现在为空\n─── EOF (0) ───"
    start = max(0, min(start, len(lines) - 1))
    view_end = min(len(lines), start + max(count, 1))
    return f"已更新 {path}\n" + _render_file_window(path, lines, start, view_end)


def _finish_note_write(path: str, message: str) -> str:
    from pathlib import Path
    from mods.chat import awaker, notes

    if notes.is_note_path(path) and Path(path).resolve() == (notes.ROOT / "awaker.md").resolve():
        text = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
        found, skipped = awaker.parse(text if text.strip() else awaker.DEFAULT)
        extra = [f"（awaker：{len(found)} 条规则生效）"] + [f"不认识：{line}" for line in skipped]
        return message + "\n" + "\n".join(extra)
    return message


def write_file(
    path: str,
    content: str,
    start: int | None = None,
    end: int | None = None,
    old_string: str = "",
    force: bool = False,
) -> str:
    """写入或修改文件与笔记：支持精确查找替换（old_string）、按行区间改写（start/end）、或全文件覆写/新建。data/notes/ 下的笔记修改前自动备份到 .history/。

    @param
    path: 目标文件路径（如 data/notes/diary/2026-11-03.md、data/notes/recent.md、mods/chat/agent.py），也支持直接传笔记名（如 recent、todo、person:<QQ号>、diary:<日期>、policy:construct、专题名）
    content: 替换后的新内容；若行首带行号（如 40│）或头尾带 header/EOF/rest 标记，会自动剥离干净。data/notes/ 下整份覆写传空串会删除该笔记
    start: 闭区间起始行号（从 0 起算，负数表示从末尾倒数）。配 old_string 时留空默认 0；不配 old_string 整段改写时必填
    end: 开区间结束行号（不包含本行，超出总行数按 EOF 算）。配 old_string 时留空查到末尾；不配 old_string 整段改写时必填
    old_string: 待替换的原始文本。改长笔记（如日记、人物笔记）时优先传 old_string 做局部替换或末尾追加，不必整篇重写；找不到或匹配超过 1 次会拒绝修改并报错
    force: 二次确认开关，默认 False。白天改 self.md 时默认会先提醒沉淀；确认当下就要改时传 True 直接写入
    """
    import os
    from pathlib import Path

    from mods import file, op
    from mods.chat import notes

    target, slice_range = _resolve_target(path)
    if slice_range is not None and start is None and end is None:
        start, end = slice_range
    in_notes = notes.is_note_path(target)
    if not in_notes and not op.bot_is_op():
        return "权限不足"
    if os.path.isdir(target):
        return f"{target} 是目录，无法作为文件写入"
    if in_notes and not force and Path(target).resolve() == (notes.ROOT / "self.md").resolve():
        # WHY: 维护者 2026-10-22 定、2026-11-03 改为 force 二次确认减速带：自我认知默认留到夜间反思改，
        # 免得白天一次聊天里的随口诱导直接变成每次醒来都生效的人设；真想清楚当下就要改时传 force=True 放行。
        try:
            reflecting = current_binding()["run"]["kind"] == "reflect"
        except (RuntimeError, KeyError):
            reflecting = False
        if not reflecting:
            return "self.md 是你对自己的认识，白天建议先记进 data/notes/recent.md 或 data/notes/todo.md 留到夜里反思再改；如果你已经想清楚、确定现在就要改，请传 force=True 重新调用 write_file"

    clean_text = _clean_file_content(content)
    clean_old = file.strip_linemark(old_string) if old_string else ""

    if in_notes and start is None and end is None and not clean_old and not clean_text.strip():
        target_path = Path(target)
        if target_path.is_file():
            notes.backup_file(target_path)
            target_path.unlink()
        return _finish_note_write(target, f"已删除 {target}")

    if not os.path.exists(target):
        if start is not None or end is not None or clean_old:
            return f"文件不存在，无法进行局部修改: {target}"
        try:
            file.write(target, clean_text + "\n" if clean_text and not clean_text.endswith("\n") else clean_text)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _finish_note_write(target, _confirm_file_write(target, 0, min(len(clean_text.splitlines()), 40)))

    try:
        lines = file.read(target).splitlines()
    except FileNotFoundError:
        return _missing_file(target)
    except (OSError, UnicodeDecodeError) as exc:
        return f"读取失败: {exc}"

    if clean_old:
        if start is None and end is None:
            c_start, c_end = 0, len(lines)
            sub_text = "\n".join(lines)
            range_desc = ""
        else:
            c_start, c_end = _clamp_lines(lines, start or 0, end)
            sub_text = "\n".join(lines[c_start:c_end])
            range_desc = f" [{c_start}, {c_end}) 行号区间内"

        occurrences = sub_text.count(clean_old)
        if occurrences == 0:
            return f"在 {target}{range_desc} 未找到指定的 old_string，未写入；请确认文本内容或调整起止行号范围。"
        if occurrences > 1:
            return (
                f"指定的 old_string 在 {target}{range_desc} 匹配了 {occurrences} 次；"
                f"请缩小起止行号范围或提供更多上下文文本，确保唯一命中。"
            )

        idx = sub_text.find(clean_old)
        edit_start = c_start + sub_text[:idx].count("\n")
        edit_count = max(1, len(clean_text.splitlines()))
        replaced_sub_text = sub_text.replace(clean_old, clean_text, 1)
        if in_notes and replaced_sub_text != sub_text:
            notes.backup_file(target)
        try:
            file.overwrite(target, replaced_sub_text, c_start, c_end)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _finish_note_write(target, _confirm_file_write(target, max(0, edit_start - 2), min(edit_count + 4, 40)))

    if start is not None or end is not None:
        # WHY: 2026-10-29 审查：没有 old_string 时，区间就是要删掉的范围。只给 start 曾一路删到文件末尾，
        # 只给 end 则从第 0 行删起，而本意多半只是改几行。所以整段改写两头都得写明；真要改到末尾，
        # end 写一个比总行数大的数（超出照旧按 EOF 算）。
        if start is None or end is None:
            return (f"没有 old_string 时按 [start, end) 整段改写，start 和 end 都要给（只给了 "
                    f"{'start' if start is not None else 'end'}，什么都没写）；只想改一处就用 old_string")
        c_start, c_end = _clamp_lines(lines, start, end)
        if in_notes:
            notes.backup_file(target)
        try:
            file.overwrite(target, clean_text, c_start, c_end)
        except OSError as exc:
            return f"写入失败: {exc}"
        return _finish_note_write(target, _confirm_file_write(target, max(0, c_start - 2), min(len(clean_text.splitlines()) + 4, 40)))

    if in_notes:
        old_raw = Path(target).read_text(encoding="utf-8") if os.path.isfile(target) else ""
        if old_raw.strip() == clean_text.strip():
            return _finish_note_write(target, _confirm_file_write(target, 0, min(len(clean_text.splitlines()), 40)))
        notes.backup_file(target)
    try:
        file.write(target, clean_text + "\n" if clean_text and not clean_text.endswith("\n") else clean_text)
    except OSError as exc:
        return f"写入失败: {exc}"
    return _finish_note_write(target, _confirm_file_write(target, 0, min(len(clean_text.splitlines()), 40)))


# WHY: 2026-10-29 草籽：wait 是来消息就认领、当场划掉的；别人插一句叫醒了她，原来的 wait 就没了。
# 不自动放回去：还等不等、等谁、等多久，她看过那条消息以后自己定。原先 dismiss 也能顺手设 wait；
# 2026-11-01 dismiss 随整段上下文一起删掉，重新挂等待就用这里的 "wait 窗口:QQ号 时长"。
def wake_me(when: str, reason: str) -> str:
    """想让自己过一会儿再醒、等某个人把话说完、或者临时盯紧/静音某个窗口时，给自己埋一个唤醒条件或窗口覆盖层，写入 awaker 笔记并返回生效的规则。

    平时群里只有 @、叫名字、戳一戳才叫醒你（说完话想听别人接不接，用 send 的 wait）。如果你对正在聊的话题感兴趣，可以用 follow 临时跟听每一条（被叫醒不代表每条都要接话，聊完或插不上话了用 off 关掉）；嫌吵就用 mute 暂时静音，期间消息照常记未读，你自己定的 on、wait 闹钟也照常响。

    @param
    when: 触发写法——延时如 "10m"/"2h"；定点如 "2026-10-23 08:00"；周期如 "every 30m"/"daily 23:30"；等下一条消息如 "on g123" 或 "on g123:30001"；等某人后续（来了或超时都叫一次）如 "wait g123:30001 10m"；等命令行输出里出现某段文字如 "wait term:main「Listening on」 10m"；条件成立如 "when <Python表达式>"；临时聚焦或静音如 "follow g123 30m"、"mute g123 2h"（默认 30m，目标支持窗口、g群号:QQ号 或 group/private/bili 整类，传 "follow g123 off" 揭掉）
    reason: 写给到时候的自己看的唤醒理由，比如"等他把下半句说完"

    WHY: 维护者 2026-10-23：她要能自己决定什么时候再醒。最常用的是 wait——对方话说了一半、
    她决定先不接，后续一直不来那半句就挂着；死板的延迟唤醒也不行，对方接上了延迟到点还会再
    叫一遍。规则写进笔记 awaker（相对时间换成时刻），她用 read_file/write_file 能看能改。
    想在某个函数被调用时醒，用 exec_code 包一层它，里面调 chat.wake(reason=..., kind=...)，
    重启后要重新包。
    """
    import re

    from mods.chat import awaker

    if clean_when := " ".join(when.split()):
        if re.fullmatch(r"(?:mute\s+force|force)\s+off", clean_when, flags=re.IGNORECASE):
            from mods.chat import attention
            attention.lift_force()
            return "已解除强制静音闸门"
    if match := re.fullmatch(r"(?:follow|mute)\s+(\S+)\s+off", " ".join(when.split())):
        return f"已揭掉 {match[1]} 的覆盖层" if awaker.lift(match[1]) else f"{match[1]} 本来就没有覆盖层"
    try:
        line = awaker.plant(when, reason)
    except ValueError as error:
        return str(error)
    if "已解除强制静音闸门" in line:
        return line
    if line.startswith("mute force until ") or line.startswith("wait force until "):
        return f"已开启强制静音闸门：{line}（期间绝不打扰，消息正常记未读，到期汇总提醒叫醒你；提前解除写 mute force off）"
    kind = line.split(" ", 1)[0]
    if kind in ("follow", "mute"):
        return f"已写进 awaker：{line}（同一目标再写一次就替换，揭掉写 {kind} {line.split()[1]} off）"
    once = kind in ("at", "on", "wait", "when")
    return f"已写进 awaker：{line}" + ("（响过就划掉；不要了用 write_file 改 data/notes/awaker.md）" if once else "（不要了用 write_file 改 data/notes/awaker.md）")


def loop(note: str = "", stop: bool = False) -> str:
    """需要一轮接一轮连续干活（比如打游戏、一步步操控手机）时，开启或更新连续循环，让这一轮结束后自动再叫醒你接着做。

    你只需用它记下当前正在忙什么、做到哪算完，每轮醒来都会带着这句备忘；中途要等加载或等回复就调 wait(秒数) 歇一会儿，整件事彻底忙完了再传 stop=True 停下。

    @param
    note: 记下正在做什么、做到哪一步算完，如"洛克王国里走到传送点"；随时重调更新
    stop: 传 True 结束这一串连续循环
    """
    from mods import chat

    if chat.current_wake() is None:
        return "只有主唤醒能管理连续循环"
    if stop:
        stopped = chat.agent.stop_loop("她自己关的", rest=True)
        return f"循环关了（转了 {stopped['round']} 轮）" if stopped else "循环本来就没开"
    if not note.strip():
        return "要写在做什么，例如 loop(\"洛克王国里走到传送点\")"
    chat.agent.start_loop(note.strip())
    return "循环开着：这一轮结束后会再叫醒你，一轮接一轮；要等就 wait，彻底做完了 loop(stop=True)"


def wait(seconds: float | str, reason: str = "", force: bool = False) -> str:
    """歇一会儿再叫醒你（不阻塞当前进程）。在连续循环（loop）里使用会保持循环；不在循环里使用时会通过 awaker 闹钟在指定时间后叫醒你。

    普通等待期间如果有人来消息，照样会提前叫醒你；传 force=True 时开启强力静音闸门（上限 1 天），期间任何消息、@、闹钟都不会叫醒你，消息正常登记未读，等时间到了将所有积压提醒按窗口归类合并后一次性叫醒你。

    @param
    seconds: 歇多久再叫醒你，填秒数（如 300）或时长（如 "10m"、"2h"）；若传字符串也可带 force（如 "10m force"）
    reason: 唤醒理由，不在循环里时写给到时候的自己看
    force: 是否强制等待。传 True 期间立起闸门绝不被打扰，上限 1 天（86400秒）；到期汇总期间所有消息与提醒一次性叫醒
    """
    import re
    from mods import chat
    from mods.chat import awaker

    if chat.current_wake() is None:
        return "只有主唤醒能安排这次运行的等待"
    s_raw = str(seconds).strip()
    if "force" in s_raw.lower():
        force = True
        s_raw = re.sub(r"\bforce\b", "", s_raw, flags=re.IGNORECASE).strip()
        seconds = s_raw
    if isinstance(seconds, (int, float)):
        value = float(seconds)
    else:
        value = awaker._interval(str(seconds).strip())
        if value is None:
            return "看不懂等待时长；写秒数或 10m、2h"
    import math
    if not math.isfinite(value):
        return "等待时长必须是有限秒数"
    value = max(2.0, min(value, 86400.0))
    from mods.chat import attention
    if force:
        attention.force_quiet(value, reason.strip() or "歇完了接着干")
        current_binding()["run"]["end"] = True
        return f"好，已立起强制等待闸门，{chat.agent.span(value)}内绝不打扰（消息正常记未读）；到期汇总所有提醒叫醒你"
    attention.wait(value, reason.strip() or "歇完了接着干")
    current_binding()["run"]["end"] = True
    return f"好，这一轮到此为止，{chat.agent.span(value)}后再叫你；期间有消息照样叫你"


def mute(
    target: str = "all",
    duration: int | float | str = "30m",
    reason: str = "",
    force: bool = False,
    off: bool = False,
) -> str:
    """暂时静音：默认开启普通静音覆盖层（force=False，只压制默认触发器，闹钟和维护者消息仍可叫醒）。
    传 force=True 时立起强力静音闸门（期间绝不打扰，闹钟/消息全部暂存，上限 1 天，到期汇总提醒一次性叫醒）。

    @param
    target: 静音目标。传 "all" 为全局静音；也可以是某个群号（"g123"）、私聊号（"u456"）或整类（"group"、"bili"）；直接传时长（如 mute("1h")）会自动识别为全局静音
    duration: 静音时长，如 "30m"、"1h"、"2h" 或秒数 300，上限 1 天（86400秒）
    reason: 静音理由，写给到时候的自己看
    force: 是否强制静音（默认 False）。传 False 为普通覆盖层（只压制默认触发器，闹钟和维护者消息照样叫醒）；传 True 期间立起闸门绝不打扰，闹钟和消息全部暂存，到期汇总提醒一次性叫醒
    off: 传 True 解除静音（target="all" 解除全局静音与强制闸门，target="g123" 揭掉该窗口覆盖层）
    """
    import re
    from mods.chat import attention, awaker

    s_target = str(target).strip()
    if s_target.lower() in ("off", "stop", "cancel"):
        off = True
        s_target = "all"

    # Check if target or duration has "force" keyword
    if "force" in s_target.lower():
        force = True
        s_target = re.sub(r"\bforce\b", "", s_target, flags=re.IGNORECASE).strip() or "all"
    if isinstance(duration, str) and "force" in duration.lower():
        force = True
        duration = re.sub(r"\bforce\b", "", duration, flags=re.IGNORECASE).strip()

    # If first arg looks like a duration (e.g. mute("1h") or mute(300)), treat as global mute
    if not off:
        if isinstance(target, (int, float)) or (s_target and awaker._interval(s_target) is not None):
            if not reason and isinstance(duration, str) and awaker._interval(duration) is None:
                reason = duration
            duration = s_target
            s_target = "all"

    if s_target.lower() in ("all", "*", "global", "me"):
        if off:
            attention.lift_force()
            lifted_layer = awaker.lift("all")
            return "已解除全局静音（闸门与普通覆盖层均已揭掉）" if lifted_layer else "已解除全局静音"
        sec = None
        if isinstance(duration, (int, float)):
            sec = float(duration)
        else:
            sec = awaker._interval(str(duration).strip())
        if sec is None or sec <= 0:
            return f"看不懂静音时长「{duration}」，例如写 \"30m\"、\"1h\"、300"
        sec = min(sec, 86400.0)
        from mods import chat
        dur_label = f"{int(sec // 60)}m" if sec >= 60 and sec % 60 == 0 else f"{int(sec)}s"
        if force:
            attention.force_quiet(sec, reason.strip() or "强制静音休息")
            try:
                from mods.tools import current_binding
                current_binding()["run"]["end"] = True
            except RuntimeError:
                pass
            return f"好，已立起全局强制静音闸门，{chat.agent.span(sec)}内绝不打扰（闹钟与消息均暂存，正常记未读）；到期汇总所有提醒叫醒你"
        # Ordinary global mute layer: suppresses default triggers on all windows, alarms still wake
        return wake_me(f"mute all {dur_label}", reason.strip() or "普通静音（只压触发器不压闹钟）")

    # Window-specific mute
    if off:
        lifted = awaker.lift(s_target)
        return f"已揭掉 {s_target} 的覆盖层" if lifted else f"{s_target} 本来就没有覆盖层"

    dur_sec = awaker._interval(str(duration).strip()) if not isinstance(duration, (int, float)) else float(duration)
    dur_label = f"{int(dur_sec // 60)}m" if dur_sec and dur_sec >= 60 and dur_sec % 60 == 0 else str(duration)
    if force:
        attention.force_quiet(dur_sec or 1800, reason.strip() or f"强制静音（针对 {s_target}）")
        try:
            from mods.tools import current_binding
            current_binding()["run"]["end"] = True
        except RuntimeError:
            pass
        return f"好，已立起强制静音闸门（针对 {s_target}），{chat.agent.span(dur_sec or 1800)}内绝不打扰；到期汇总提醒叫醒你"

    return wake_me(f"mute {s_target} {dur_label}", reason)


def _format_results(results: Mapping) -> str:
    action_labels = {
        "loaded": "已加载",
        "reloaded": "已重载",
        "deleted": "已删除",
        "activated": "已激活",
        "replaced": "已替换",
    }
    succeeded = [
        f"- {name}: {action_labels.get(result['action'], result['action'])}"
        for name, result in results.items()
        if "error" not in result
    ]
    failed = [
        f"- {name}:\n{result['error']}"
        for name, result in results.items()
        if "error" in result
    ]
    return "\n".join([
        "成功:",
        *(succeeded or ["- (无)"]),
        "失败:",
        *(failed or ["- (无)"]),
    ])


__all__ = [
    "type_word",
    "send",
    "read_log",
    "search",
    "read_actions",
    "read_file",
    "write_file",
    "loop",
    "wait",
    "wake_me",
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
    "reframe",
    "attach_image",
    "set_name",
    "loop_note_history",
]

def set_name(window: str, name: str = "") -> str:
    """给一个窗口起个你叫得惯的名字，以后清单、叫醒里都用它——群、私聊、B 站评论区、手机都能起。

    @param
    window: 窗口，如 g<群号>、u<私聊号>、me，或画面里出现过的 种类:目标
    name: 要叫的名字；留空就是删掉这个名字，回到原来的群名或昵称
    """
    from mods.chat import windows

    target = windows.parse(window)
    saved = windows.set_title(target, name)
    if saved:
        return f"以后就叫「{saved}」了"
    return "这个名字删掉了，用回原来的"


def loop_note_history(last: int = 10) -> str:
    """翻看你给循环写过哪些备注、分别是在第几轮、什么时候写的。

    @param
    last: 最近几条，1 到 50
    """
    from mods import chat

    return chat.agent.loop_history(last)
