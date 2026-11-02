'''日常表达、记忆召回与工具加载的基础能力。

你在同一步可调用多个工具，它们按顺序执行，但参数一起生成，彼此看不到结果；依赖前一步结果的操作放在下一步。
自己的记忆怎样流转已在身份后的“你的记忆”中说明。行动的上下文由构造选择材料，构造给的情境、观察和联想供你判断；怎样回应和行动由你决定。

## 材料与召回

笔记标题标明出处；`@L12-28` 表示只带了原文件第 12–28 行，其余没有载入。`read_note("topic:线索@L12-28")` 可按同一写法读局部；完整笔记用 `read_note("topic:线索")`。`search` 找线索，`read_log` 翻聊天原文，`read_actions` 看自己以前的输出和工具结果。`todo` 是待办本子，构造会看到全文并选择行动需要的部分；随时可主动读取和更新。写笔记用 `write_note` 整篇替换，改前自动留历史；日记和其它专题也可随时写。

## 工具与 Skill

目录列可用模块；`load_tools` 给当前运行加载函数和说明。行动开场由构造选择模块，不自动继承上次用过的全部工具；本次加载的工具在本次运行内可继续用。反思与沉淀仍恢复持久激活态。
`list_tools` 查看状态；改了文件用 `reload_tools`，只有 `mods/tools/` 的模块和下划线 helper 能热更新，其它核心代码需完整重启。
需要开发、改写、删除工具或写 Skill 时，`load_tools(["tool_development"])` 展开手册。`exec_code` 是共享 Python 环境，不是沙箱；函数说明里有时限和后台选项。`attach_image` 把图片递到眼前。

## 怎么开口说话（type_word 与 send）

你看的每条聊天记录都带一个头：`【头衔】名字(QQ号) 时:分:秒 | message_id`，正文缩进在下一行。头里那个 QQ 号要是你自己，那就是你之前说过的话。想艾特谁写 `[CQ:at,qq=号码]`，想引用哪条就在正文里写 `[CQ:reply,id=message_id]`，id 就是竖线后面那串。

你说话就像真人拿着手机聊微信、聊 QQ 一样，分**“打字”和“点发送”**两步：先调 `type_word(target, text)` 把想说的话敲进那个窗口的输入框，下一步再调 `send(target)` 真正发出去。

这是你唯一能让别人听见你说话的方式——你直接写在模型回复正文里的文字，全是你心里的自言自语（虽然会留在你自己的行动记录里，但外面的人一个字也看不见）。每次打字和发送都要写清窗口名字，比如 `target="g<群号>"`、`target="u<私聊QQ号>"` 或 `bili:<BV号>`，别凭感觉盲猜（`me` 是写给自己看的窗口）。

- **先打字、看一眼、再发送**：`type_word` 会把字放进输入框（想改口就重新调一次覆盖，传空串清空；草稿会保留到发送或主动清空）。同一次输出里刚打的字，不能在同一批里紧跟着 `send`——系统特意让你在打字和发送之间隔一步，好让你先看一眼打字这几秒钟里对方有没有发来新消息。等下一步看清了，再决定是照发、改口还是算了不发。
- **分段与发送回执**：QQ 里在输入框用空行隔开的几段话，`send` 时会像真人聊天一样，隔一两秒一条、拆成几个气泡依次发出去，并返回消息号（`message_id`）。B 站评论整段发成一条评论，空行就是评论里的分段。CLI 窗口（`dsh:main`、`agy:review`、`codex:code`）把完整输入作为一轮任务，立即返回接收编号，回答稍后回到同一窗口并叫醒你；运行中再次发送会保留草稿。
- **wait 控制后续与自主呼吸**：`send` 的 `wait` 参数决定发完这句之后你是继续活动还是挂着喘气：
  - `wait=0`：不等回复，发完直接跑下一轮，手里还有别的事情接着做；
  - `wait=-1`：发完收工，纯被动等待（相当于原 `final_call=true`），没有超时闹钟；
  - `wait="20m"`（或 `"5m"`、`300` 等）：在当前窗口挂一个超时等待规则（由 awaker 统一调度）。等待期间如果对方（或群里）有人接话，新消息会自动认领并叫醒你；如果超时一直没人回复，awaker 会到点叫醒你，带着你设的 `reason`（例如"不来我就接着读日记"）让你自主接着活动。群聊里还可以传 `who=QQ号` 指定只等某个人接话。等待期间叫醒你的消息要是不用接（比如别人插了一句），那条 wait 在叫醒你时已经用掉了；还想接着等，就调 `wake_me("wait g123:QQ号 10m", 理由)` 重新挂上。
- **发送失败或未确认**：万一没发出去或者状态“未确认”，系统也会再跑一轮让你看清状况。失败时字还在输入框里；但如果是**“未确认”**，说明请求已经递出去了只是没拿到回执号，那句话十有八九已经发进聊天里了——这时输入框已经清空，千万别直接重发，先看下一轮聊天记录里有没有它。
- **被叫醒不等于必须开口**：大家闲聊不一定有明确的问题。别人已经答完、话题和你无关、或者你没什么想说的，就安静结束这一轮——不用 `type_word` 也不用 `send`，少说比多说好。真不想接的时候，丢张表情包或者回个问号也行。'''

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

    找到线索后，再用 read_log 看聊天前后文、read_note 看笔记或日记全文、或者加载 memory_system 后用 read_context 翻归档段。

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


def read_note(scope: str) -> str:
    """想翻看某个人、某个群、某篇日记、某份专题笔记或自己的常驻笔记时，读取那份笔记的全文或指定原文件行号的片段。

    @param
    scope: 笔记名，如 self、global、recent、todo、awaker、person:<QQ号>、group:<群号>、diary:<日期>、topic:线代；局部加 @L起行-末行，如 topic:线代@L12-28（行号从 1 起）
    """
    from mods import chat

    return chat.roles.read_note(scope)


def attach_image(uri: str, note: str = "", model: str = "") -> str:
    """遇到聊天图片、评论配图、截图或验证码这类得亲眼看了才知道的内容时，把图片交给你自己看；当前模型不支持视觉或指定了视觉模型时会自动转述。

    @param
    uri: 图片地址，支持 file://、http(s):// 或 data:，工具或评论里给的地址直接传进来就行
    note: 附在图前的备忘，提醒重点看什么，如"看清图里的报错字样"
    model: 留空表示交给当前模型亲眼看（当前模型无视觉时自动调用默认识图模型转述）；也可指定模型（如 "azure/gpt-5.6-luna"、"cheap"、"bytecat/gpt-5.4-mini"）转述
    """
    from ._vision import attach

    return attach(uri, note, model=model or None)


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
    """想在群里、私聊或评论区开口说话时，先把话打进对应窗口的输入框（不会立刻发出去），返回输入框当前的内容。

    直接写在回复正文里的话不会发出去，只会留在你自己的行动记录里；说话先打进输入框，下一步看过期间有没有新消息再调 send 发出去。想改口就重新打一遍覆盖，不想说了传空串清空。输入框里的字会一直留着，发出去才会消失。
    ``me`` 也能打：那是写给自己看的一句话，交接或反思时想说什么就说什么，不吵到任何人。

    @param
    target: 要说话的窗口，如 g<群号>、u<私聊号>、bili:<BV号>、me，或画面里出现过的可回话窗口
    text: 要打进输入框的完整内容，CQ 码（at、回复、图片）直接写；传空串清空输入框

    WHY: 维护者 2026-10-23：说话先打字、再发送，参照 Cortico 的起草-确认门，但用"输入框"
    的说法——她打出来的字自己看得见。打字和发送之间至少隔一次请求（见 send），于是这期间
    到的消息她一定看过；不靠延迟，人可能隔几分钟才接上下一句。
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
    return (f"{label} 的输入框：\n{body}\n"
            f"（还没发。看清新到的消息后调 send(\"{label}\") 发出去；想改口重新打字，不想发了传空串清空输入框）")
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
    wait: int | float | str = -1,
    reason: str = "",
    who: int | str | None = None,
) -> str:
    """看过打字期间的新消息、确认输入框里的话没问题后，把它真正发进目标窗口，返回发出的消息号（message_id）。

    同一轮输出里刚 type_word 的字不能同一步直接 send，总要隔一步让你先看一眼新消息。发出去的话别人当场就能看见；QQ 里用空行分段的文字会像真人一样拆成几条依次发出，B 站评论和 CLI 窗口整段一次发出。遇到“未确认”说明多半已经发出去了，先看下一轮聊天记录，别直接重发。

    @param
    target: 要发送的窗口，和 type_word 填的一样（如 g<群号>、u<私聊号>、bili:<BV号>、me）
    wait: 发完后怎么安排后续。传 0 表示不等、发完继续这一轮干手头的活；传 -1 表示结束这轮、纯被动等待；传等待时长（如 "5m"、"20m"、"1h" 或秒数 300）表示在当前窗口挂一个超时等待闹钟，期间有人回复立刻叫醒，到点超时还没人回也会自动叫醒你继续呼吸
    reason: 挂超时等待（wait > 0）时写给到时候自己的理由，如"不来我就接着读日记"、"等草籽接话"；留空默认"等回复"
    who: 在群聊窗口中指定只等特定人回复（填 QQ 号，如 123456；传 "any" 等群里任何人）。若不填：在有人跟你说话的轮次里自动默认等刚才跟你说话的人，避免路人插话吞掉等待；在你自己醒来的轮次（闹钟、订阅等无对话人轮次）不填则等待群里任何人回复

    WHY: 同一次输出里刚 type_word 的字不发。那一批调用的参数是一起生成的，她还没看到
    打字之后到的消息；拒绝一次，下一次请求里新消息就在眼前（维护者 2026-10-23）。

    WHY: 它**等**发送结果，不是投递完就返回。这不是谨慎，是终止语义逼出来的：一轮的结束
    由 `wait` 声明，而"失败时照常再跑一轮"要求这里能分辨成败——成败只有 SendFuture
    完成时才知道。实测代价约 0.3~0.6 秒，来自 `message._work` 每条消息后的节流 sleep，
    不是网络。每个种类的发送函数都要这样等（见 chat/windows.py）；QQ 的三类结果、超时和
    不转义的 WHY 跟着发送代码搬到了 `windows._send_qq`。

    WHY: 2026-10-27 起用参数控制终结回合；2026-10-29 草籽与柚子商定进化为 wait：
    0 = 不等继续循环干活；-1 = 结束这一轮纯被动等待；
    >0（如 "20m"）= 在当前窗口挂一个超时等待规则（由 awaker 统一部署，兼顾等任何人或特定人），
    期间有回复即刻认领叫醒，超时未回复则到点叫醒自己做别的事（"话说完不是死掉，是挂在那儿喘气"）。
    同时发言后固定 5 分钟自动跟听并入此机制，由她自主决定等多久。

    WHY: 它住在 meta 而不是自己一个模块，因为发言是模型**永远**该有的能力，而
    `tools._BASE_MODULE_NAME` 是单数、基础模块只有一个。meta 早就不只是"管工具模块"了
    （事件反查和覆盖工具也管上下文），它实际是那组不可卸载的基础能力，`type_word`/`send`
    属于这一组。改成支持多个基础模块也行，代价是那条"基础模块的导出不加模块名前缀"的
    规则会多出命名冲突的可能，眼下不值得。

    WHY?: 子代理（`tools/agents.py`）因此也拿得到 `type_word`/`send`（2026-10-23 以前是 `say`），于是它能直接往窗口里说话——这是
    工具化带来的**新**能力，以前子代理的正文只回给主模型。没有给它加门控，因为"子代理该
    不该能说话"还没有人拍过；先记在这里，真出现不想要的发言再决定是挡掉还是保留。
    """
    from mods import context
    from mods.chat import awaker, windows
    from mods.tools import current_binding

    try:
        window, label = _box_window(target)
    except ValueError as error:
        return str(error)
    boxes = _boxes()
    box = boxes.get(label)
    if not box:
        return f"{label} 的输入框是空的，什么都没发。先用 type_word 把要说的话打进去"
    if box["typed_in"] is not None and box["typed_in"] == _this_output():
        return (f"{label} 的字是这一步刚打的，你还没看过打字之后到的消息，这一步先不发。"
                f"下一步看一眼，再决定 send、改还是不发。输入框里现在是：\n{box['text']}")
    if context.agent_mode():
        current_binding()["run"]["associated_windows"].add(window)

    wait_mode = "passive"  # "continue" (0), "passive" (-1), "wait" (>0)
    wait_seconds = 0
    wait_target = label
    wait_str = str(wait).strip()

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
        if target_who is not None and str(target_who).strip().lower() in ("any", "all", "*"):
            target_who = None
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
    parts = [box["text"]] if windows.spec(window)["whole"] else _split_turns(box["text"])
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
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去，还在输入框里。"
    boxes.pop(label, None)

    if wait_mode == "continue":
        pass
    elif wait_mode == "passive":
        current_binding()["run"]["end"] = True
        awaker.lift_wait(label)
    elif wait_mode == "wait":
        current_binding()["run"]["end"] = True
        wait_reason = reason.strip() or f"等 {wait_target} 回复"
        dur_label = f"{wait_seconds // 60}m" if wait_seconds >= 60 and wait_seconds % 60 == 0 else f"{wait_seconds}s"
        try:
            plant_line = awaker.plant(f"wait {wait_target} {dur_label}", wait_reason)
        except Exception as error:
            plant_line = f"（挂等待规则失败：{error}）"
        result_text = "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
        return f"{result_text}（已设等待：{plant_line}；来消息或超时都会叫你）"

    return "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
def write_note(scope: str, text: str) -> str:
    """想记下对某个人或群的新了解、新建或更新专题笔记、更新待办（todo）、改闹钟（awaker）或写日记时，用新的完整内容替换那份笔记，返回写入结果。

    你可以随时起名字新建专题笔记（如 `消消乐`、`topic:线代`），把具体某件事的经验、资料或长想法单独放一份，在 `global` 里只留一行引用，免得把 `global` 塞成大杂烩；白天遇到想记的事也可以当场写进今天日记 `diary:YYYY-MM-DD`。这是整份覆盖（传空串会删掉整份笔记），改前会自动留备份。你对自己的认识（self）白天不在这里改，留到反思时用 edit_policy 改。

    @param
    scope: 笔记名，如 global、recent、todo、awaker、person:<QQ号>、group:<群号>、diary:<日期>，或你自己起的专题笔记名（如 消消乐、topic:线代）
    text: 更新后的整份笔记全文；传空串会删除这份笔记
    """
    from mods.chat import awaker, notes

    if scope == "self":
        # WHY: 维护者 2026-10-22 定：自我认知只在夜间反思里改，白天被人一句话说动的事
        # 要隔一晚回头看过才变成"我是谁"，免得一次聊天里的诱导变成每次醒来都生效的人设。
        return "self 是你对自己的认识，白天不改；想到的先记进 recent 或 todo，夜里反思时再决定要不要用 edit_policy 改"
    notes.write(scope, text)
    if scope == "awaker":
        found, skipped = awaker.parse(text if text.strip() else awaker.DEFAULT)
        lines = [f"已写入 awaker，{len(found)} 条规则生效"] + [f"不认识：{line}" for line in skipped]
        return "\n".join(lines)
    return f"已写入 {scope}" if text.strip() else f"已删除 {scope}"


# WHY: 2026-10-29 草籽：wait 是来消息就认领、当场划掉的；别人插一句叫醒了她，原来的 wait 就没了。
# 不自动放回去：还等不等、等谁、等多久，她看过那条消息以后自己定。原先 dismiss 也能顺手设 wait；
# 2026-11-01 dismiss 随整段上下文一起删掉，重新挂等待就用这里的 "wait 窗口:QQ号 时长"。
def wake_me(when: str, reason: str) -> str:
    """想让自己过一会儿再醒、等某个人把话说完、或者临时盯紧/静音某个窗口时，给自己埋一个唤醒条件或窗口覆盖层，写入 awaker 笔记并返回生效的规则。

    平时群里只有 @、叫名字、戳一戳才叫醒你（说完话想听别人接不接，用 send 的 wait）。如果你对正在聊的话题感兴趣，可以用 follow 临时跟听每一条（被叫醒不代表每条都要接话，聊完或插不上话了用 off 关掉）；嫌吵就用 mute 暂时静音，期间消息照常记未读，你自己定的 on、wait 闹钟也照常响。

    @param
    when: 触发写法——延时如 "10m"/"2h"；定点如 "2026-10-23 08:00"；周期如 "every 30m"/"daily 23:30"；等下一条消息如 "on g123" 或 "on g123:30001"；等某人后续（来了或超时都叫一次）如 "wait g123:30001 10m"；条件成立如 "when <Python表达式>"；临时聚焦或静音如 "follow g123 30m"、"mute g123 2h"（默认 30m，目标支持窗口、g群号:QQ号 或 group/private/bili 整类，传 "follow g123 off" 揭掉）
    reason: 写给到时候的自己看的唤醒理由，比如"等他把下半句说完"

    WHY: 维护者 2026-10-23：她要能自己决定什么时候再醒。最常用的是 wait——对方话说了一半、
    她决定先不接，后续一直不来那半句就挂着；死板的延迟唤醒也不行，对方接上了延迟到点还会再
    叫一遍。规则写进笔记 awaker（相对时间换成时刻），她用 read_note/write_note 能看能改。
    想在某个函数被调用时醒，用 exec_code 包一层它，里面调 chat.wake(reason=..., kind=...)，
    重启后要重新包。
    """
    import re

    from mods.chat import awaker

    if match := re.fullmatch(r"(?:follow|mute)\s+(\S+)\s+off", " ".join(when.split())):
        return f"已揭掉 {match[1]} 的覆盖层" if awaker.lift(match[1]) else f"{match[1]} 本来就没有覆盖层"
    try:
        line = awaker.plant(when, reason)
    except ValueError as error:
        return str(error)
    kind = line.split(" ", 1)[0]
    if kind in ("follow", "mute"):
        return f"已写进 awaker：{line}（同一目标再写一次就替换，揭掉写 {kind} {line.split()[1]} off）"
    once = kind in ("at", "on", "wait", "when")
    return f"已写进 awaker：{line}" + ("（响过就划掉；不要了用 write_note 改 awaker）" if once else "（不要了用 write_note 改 awaker）")


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


def wait(seconds: float | str, reason: str = "") -> str:
    """歇一会儿再叫醒你（不阻塞当前进程）。在连续循环（loop）里使用会保持循环；不在循环里使用时会通过 awaker 闹钟在指定时间后叫醒你。

    等待期间如果有人来消息，照样会提前叫醒你。

    @param
    seconds: 歇多久再叫醒你，填秒数（如 300）或时长（如 "10m"、"2h"）
    reason: 唤醒理由，不在循环里时写给到时候的自己看
    """
    from mods import chat
    from mods.chat import awaker

    if chat.current_wake() is None:
        return "只有主唤醒能安排这次运行的等待"
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
    attention.wait(value, reason.strip() or "歇完了接着干")
    current_binding()["run"]["end"] = True
    return f"好，这一轮到此为止，{chat.agent.span(value)}后再叫你；期间有消息照样叫你"


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
    "read_note",
    "write_note",
    "loop",
    "wait",
    "wake_me",
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
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
