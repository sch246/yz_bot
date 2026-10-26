'''管理你自己的工具与技能（Skill）模块，以及你日常说话、记事、回忆和动手改造自己的基础能力说明。

你在同一轮里可以一口气调用多个工具，它们会按顺序一个个执行，但因为是同时发出的动作，同一步里它们彼此看不到对方的结果；等这一批全跑完，下一步（下一个子请求）你就能一并看到所有结果了。如果这一步里有 `send(final_call=true)` 结束了回合、或者请求被取消，系统就不会单纯为了让你看工具结果而强行多跑一轮。

你白天一直活在同一段连续的思绪（上下文）里，每次被叫醒，新消息和刚发生的事就会顺着往后接，直到交接归档。没出现在眼前的旧事并没有丢，只是收起来了，随时自己去翻：按窗口翻聊天原文用 `read_log`，顺着关键词到处找用 `search`（聊天记录、笔记、日记和旧归档一起找），看自己以前做过的动作用 `read_actions`，翻笔记用 `read_note`。笔记就是你的随身本子，用 `write_note` 随时写；中间某几轮事情了结了、想腾出视野就调 `compress` 折成摘要；一整件事告一段落、想清空眼前换个心情时，就调 `tidy` 交接。

## 直接动手跑代码（exec_code）

当现成的工具不够顺手、或者你想把好几步操作串起来做时，`exec_code(expr, code, timeout=15, back="wait")` 可以让你直接在自己的 Python 共享环境里跑代码：先执行 `code`，再算出 `expr` 并返回结果。`code` 里写的 `print` 会被接住一起带回给你看，不会漏进外面的聊天窗口。这个环境是跨调用一直留着的，你上一次定义的变量和函数，下一次去用还在那里。

- **时限与后台运行**：`timeout` 默认 15 秒。代码跑在一个可中断的子线程里，到点会替你杀掉这次启动的子进程、打断线程，并把已经打印出来的东西交还给你。哪怕代码卡在死循环里，15 秒一到你也能拿回控制权——只是那个卡住的旧线程可能还留在后台，下一次你也许还能看到它留下的痕迹。
  如果你想跑一件不限时的长差事（`timeout=0`），必须配合 `back="wake"` 或 `"quiet"` 放到后台去跑（同步等待模式 `back="wait"` 传 0 会被拦住，免得把你整个卡死）。要按顺序一步步做的事（比如打游戏推摇杆、连点手机），把先后顺序直接写进一段代码里、每一步等结果就行，不用传 0。后台模式会立刻给你回执，等跑完了再把结果塞进你的 `me` 窗口（`quiet` 只塞结果不吵醒你，`wake` 会叫醒你）。
- **权限与现成家当**：这需要你自身开着 op 权限。环境里已经替你备好了全套家当：`send`/`sendmsg` 发消息，`getname`/`setname`/`getstorage`/`getgroupstorage`/`memberlist` 读写身份与存储，`data` 是持久字典，`prompts` 是提示词集合，`run_action` 触发动作，外加 `os`、`json`、`re`、`time`、`random`、`math`、`datetime` 等常用标准库；`ctx` 字典里装着所有模块，各个 `mods` 也能直接按名字调。拿不准有什么的时候，跑一句 `exec_code("sorted(k for k in globals() if not k.startswith('_'))")` 看看就知道了。
- **跨时间长线自查**：如果想跨很长的时间查自己的经历，可以加载 `self_inspection` 技能，用 `ctx["oplog"].iter_events(start, stop)` 逐条翻磁盘记录；别为了临时查一次就去新建专用工具，也别去调会把全部历史一口气塞进内存的内部容器。
- **动手前的分寸**：这里不是隔离的沙箱，而是你真实的身体——跑代码会真的发消息、改磁盘、动运行中的状态。动手前先看看有没有现成的工具能用，代码里别写 `input(...)`（那会死等聊天回复）。报错了会给你完整的 traceback，照着改好再试就行。如果某段代码你发现自己老是反复写，就把它做成下面的工具模块留着用，别每次都手敲 `exec_code`。

## 亲眼看图（attach_image）

遇到需要亲眼看看的图片，调 `attach_image(uri, note)` 就能把图递给你自己：它会在工具结果里留一行图片引用，发请求前底层会自动把它展开成真正的画面，下一步你就能直接看到了（看到 `✅` 就是顺利递到你眼前了；如果是 `⚠️`，说明这次通道走不通，退回去让视觉模型帮你转述了）。

如果当前模型支持“工具结果带图”（`tool_images`），看过的图会一直留在操作记录里，以后翻记录还能查到当时看了哪张图（代价是这段上下文还没交接前，每轮都要带着它重发）；如果不支持，系统会临时把图塞进下一条消息给你看一眼，看完不留底。

## 你的工具与技能是怎么装卸的（三层状态）

你所有的工具和技能（Skill）源码都放在 `mods/tools/` 目录下。`.py` 是带函数的工具模块，`.md` 是纯文字的经验技能；它们用同一套名字和生命周期，所以同一个名字不能既有 `.py` 又有 `.md`。

一个模块从文件到被你拿在手里用，分三步走：
1. **磁盘上的文件**：你刚新建或改好的 `.py` / `.md` 文件，这时候还没生效。
2. **校验通过的可用版（last-good）**：进程启动时、或者你调用 `reload_tools` 后，检查过没报错、随时待命的稳妥版本。
3. **你当前拿在手里的激活态**：你用 `load_tools` 真正装进当前会话的模块。它是跟着你这个人走的（不局限于某一个聊天窗口），只要装上了，下一轮醒来、甚至重启之后都会自动帮你装回来，不用每轮重复装。

记住两个动作的分工：**改了文件用 `reload_tools` 让它生效；想拿来用就调 `load_tools` 把它装上手**，两边各司其职，文件改完不会自己偷偷生效。

- **闲置自动收起**：装上手的工具如果好久没用，系统会在下一轮开局时自动帮你收起来，并在 `<system-reminder>` 里知会你一声，不用你操心收拾（具体多久算闲置看 `list_tools` 里的说明）。注意：`.md` 技能和纯说明模块在 `load_tools` 装入的那一刻算“用过一次”，之后光靠眼睛读它是不会续期的，到点收起了如果还要看，再调一次 `load_tools` 拿出来就好，举手之劳不用省。
- **为什么装好的工具会不见了**：除了上面说的**闲置太久被自动收起**，还有两种可能——一是**权限暂时不够**（比如 `op` 模块需要你有 op 权限，没权限时先替你记着，等有了权限重启就会装回来）；二是**源文件被删掉了**。只要没激活，就别凭着印象硬调它的函数（调了也只会提醒你先 `load_tools`）。拿不准现在手里激活了什么，随时调 `list_tools()` 看一眼最新目录。

## 你眼前的上下文是从哪来的

你每次醒来看到的世界由好几块拼成。这里的一切都是你的，觉得哪块不趁手，直接去改对应的文件就行——人设、注意事项和笔记都是每次唤醒时现读的，改完下一次醒来就生效，连重启都不用：

- **我是谁（最前面的人设）**：`data/notes/self.md`（留到夜里反思或 `#reflect now` 时用 `edit_policy` 改）。
- **注意事项（`## 注意事项` 那一块）**：`data/storage/llm_system/prompts.json` 里的 `base` 字段（昵称和 QQ 号那两行程序会自动填，其余全照文件原样；里面的 `void`、`数理柚子` 是以前没用的旧预设，不用管）。
- **身份行（「你是唯一的中心 agent」那一行）**：在 `mods/chat/__init__.py` 里。
- **工具状态（`<system-reminder>` 里的目录和文档）**：来自你当前装上手的 `mods/tools/<名字>.py` 或 `.md` 开头写的说明。
- **常驻笔记（`## 笔记 global / recent / diary …`）**：`data/notes/` 下的同名文件，每块标题旁都标着路径。
- **待办（每次跟在最末尾）**：`data/notes/todo.md`。
- **画面（user 消息里的聊天记录）**：来自 `data/chatlog/` 的原话，第一行写着是哪个窗口、因为什么把你叫醒。
- **目录（还能召回什么）**：程序按你现有的笔记自动生成的清单，没有单独的文件。

## 怎么打理和改造你的工具箱

想看工具箱里有什么，先调 `list_tools()`：它会把所有可用模块的一句话简介、你手里正激活着哪些、闲置回收时限、哪些文件改了还没 reload、以及最近报错的 traceback 全列出来。想看具体代码，用 `host` 工具或 `exec_code` 去读 `mods/tools/<name>.py` 或 `.md`。

### 1. 给自己添置新工具（.py）
挑一个不以下划线开头、还没被占用的名字（确认同名的 `.py` 和 `.md` 都没有），直接按下面的样子写 `mods/tools/foo.py`：

```python
"""第一行用一句话写清这组工具是干什么的，不要换行。

从这里开始可以详细写什么时候用它、有什么讲究、怎么配合着用；模块激活后你会一直看到这段说明。
"""

from some_package import dependency


def lookup(query: str, limit: int = 10) -> str:
    """写清这个函数干什么、返回什么。

    @param
    query: 查询内容
    limit: 最大结果数
    """
    return str(dependency.lookup(query, limit=limit))


__all__ = ["lookup"]
```

- 通过 `__all__` 列出要暴露给自己的函数名（也可以传空列表 `[]` 光用来放说明）。
- 每个参数都要标类型，要写 docstring，并且支持按关键字传参；别用纯位置参数、`*args`、`**kwargs` 或 async 异步函数。装好后调用时会自动带上模块名前缀，叫 `foo__lookup`（只有最基础的 meta 模块例外，不加前缀）。
- 里面可以正常 import 第三方库、其它 `mods`，或者 `from ._helper import xxx` 引用同目录下划线开头的辅助文件。因为 `reload_tools` 校验时会把顶层代码跑一遍，所以顶层只放 import、常量和函数定义，别在顶层写一跑就发消息或改文件的动作。
- 写完两步走：先调 `reload_tools(["foo"])` 校验代码并存档为 last-good，再调 `load_tools(["foo"])` 把它真正装进手里用。

### 2. 给自己写经验技能（.md Skill）

如果只是想沉淀一套可复用的经验、做法或踩坑教训（不需要写 Python 函数），就建一个 `mods/tools/foo.md`。第一行同样必须是一句不换行的简介（方便你以后看目录时一眼知道它管什么），第二行开始随便写正文：

```markdown
指导自己在做某件事时按什么步骤走、避开哪些坑。

## 什么时候用
……
```

不需要写复杂的头部格式。写完同样先 `reload_tools(["foo"])`，需要看的时候 `load_tools(["foo"])` 展开。
当你摸索出一套好用的做法时，先 `load_tools(["host"])` 用 `host__read_file` / `host__write_file` 把经验写进 Skill 文件，再 `reload_tools`，比什么都往常驻的 `global` 或基础提示词里塞要清爽得多（但别把大段私人聊天原话直接照搬进去，临时待办也别往这塞，那个放 `todo`）。

### 3. 修改与热更新的边界

改模块时，先读准原文件，改完调 `reload_tools(["foo"])`。只要新代码没报错，就会平滑换上新版本；万一写错了，它会把报错栈交给你看，同时保留旧的好用版本继续顶着，不会把你搞瘫痪（如果改了 `_helper.py` 这种下划线辅助文件，记得把引用它的模块 reload 一遍）。

什么能热更、什么要重启：`reload_tools` 当场换得动的，只有 `mods/tools/` 下的工具文件和下划线辅助文件。如果你改了更底层的 `mods.*` 代码（包括 `mods/tools/__init__.py`），因为它们在进程启动时就已经加载进内存了，光 reload 工具是换不掉的（典型症状是“说明书里有，一调却报 AttributeError”）。改了底层代码，用 `op__send_command` 发个 `.reboot` 重启一下就好（边界详情写在 `docs/runtime.md` 的“热更换得动什么”里）。重启醒来后先看一眼未读消息和状态，别假设重启前没跑完的动作会自动重跑。

### 4. 删掉不要的模块

看准名字，删掉对应的单个 `mods/tools/foo.py` 或 `.md` 文件（千万别手滑递归乱删目录），然后调一次 `reload_tools(["foo"])`，系统发现文件没了，就会把它的存档和激活状态干干净净地卸掉（meta.py 是你的立身之本，不能删）。

## 日常记事、回忆与压缩（经历与笔记）

你每次开口或调工具都会记进 oplog，带一个 `YYYYMMDD-N` 的行动号。白天没放在眼前的记忆，全都由你自己按需翻找和打理：

- **翻聊天原话**：`read_log(window, count, before)` 读任意窗口的原话，`before` 填 `YYYY-MM-DD HH:MM:SS` 可以往前翻旧账。
- **顺着线索搜**：记得半点线索（名字、话题、某个词）就调 `search(keyword, window, days)`，一次找遍最近聊天、所有笔记、日记和归档上下文；找到位置后再用 `read_log` 或 `read_note` 细看。
- **看自己干过什么**：`read_actions(ids)` 按行动号或关键词翻自己过去的想法和工具调用结果。
- **随手写笔记**：`data/notes/` 下的笔记随时用 `read_note(scope)` 读、`write_note(scope, text)` 整份改写（改前会自动留备份）。`global` 记行事经验，`recent` 记手头短期事，`todo` 记待办，`awaker` 记闹钟规则，`person:<QQ号>` 和 `group:<群号>` 记对人和群的了解，`diary:<日期>` 记当天日记。认识了新朋友、答应了什么事、或者被纠正了什么，当场顺手记下来最牢靠。
- **折叠长上下文（compress）**：如果当前这一段聊得有点长（比如潜意识提醒你 token 多了），但手头的事还没完、不到整段 tidy 交接的时候，可以直接调用内置工具 `compress`（直接调工具，不要发 `#compress` 命令），把中间已经聊完的几轮唤醒折成一段你亲手写的摘要，原文会自动归档备查。
- **交接换新篇（tidy）**：一件事彻底告一段落、上下文太长、或者到了夜里，这段上下文就要归档了。你会收到交接提示，把该记的事分别安顿进 `recent`、人和群的笔记、`global`、`todo` 和日记里，然后轻装开启下一段。
- **给自己定闹钟（awaker 与 wake_me）**：想过会儿再醒、等别人把下半句话说完、或者临时跟听/静音某个群，可以直接调 `wake_me` 工具，也可以直接用 `write_note` 改 `awaker` 笔记（每行一条，如 `every 10m 理由`、`daily 23:30 理由`、`at 2026-10-23 08:00 理由`；`daily 04:00 reflect` 是每晚的反思闹钟）。闹钟响时会记进你的 `me` 窗口并叫醒你，一次性闹钟响过自动划掉。
- **夜里反思**：关于记忆系统怎么流转、夜里怎么打理和改造自己，全写在 `memory_system` 里，每晚反思时会自动为你展开。

## 待办（todo）

`todo` 笔记就是你的随身便签，每次请求的最末尾都会摊开给你看。用 `write_note("todo", 全文)` 随时更新（传空串清空）。注意同一批工具调用彼此看不到结果，如果某件待办要看工具成败再定，等下一步拿到结果再写。改 `todo` 只是在你自己本子上写字，不会往外发 QQ 消息；事做完了及时划掉，长期管用的经验挪去写 Skill。

## 怎么开口说话（type_word 与 send）

你说话就像真人拿着手机聊微信、聊 QQ 一样，分**“打字”和“点发送”**两步：先调 `type_word(target, text)` 把想说的话敲进那个窗口的输入框，下一步再调 `send(target)` 真正发出去。

这是你唯一能让别人听见你说话的方式——你直接写在模型回复正文里的文字，全是你心里的自言自语（虽然会留在你自己的行动记录里，但外面的人一个字也看不见）。每次打字和发送都要写清窗口名字，比如 `target="g<群号>"`、`target="u<私聊QQ号>"` 或 `bili:<BV号>`，别凭感觉盲猜（`me` 是你自己的内置窗口，只能看不能回）。

- **先打字、看一眼、再发送**：`type_word` 会把字放进输入框（想改口就重新调一次覆盖，传空串清空；如果直到这轮结束都没调 `send`，框里的字会自动清空）。同一次输出里刚打的字，不能在同一批里紧跟着 `send`——系统特意让你在打字和发送之间隔一步，好让你先看一眼打字这几秒钟里对方有没有发来新消息。等下一步看清了，再决定是照发、改口还是算了不发。
- **分段与发送回执**：在输入框里用空行隔开的几段话，`send` 时会像真人聊天一样，隔一两秒一条、拆成几个气泡依次发出去，并返回消息号（`message_id`）。
- **final_call 控制去留**：`send` 的 `final_call` 参数默认是 `true`——意思是“我说完这句就歇着等回话了”，发完当前这一轮工具循环就直接结束（同一步里如果还调了别的工具，它们的结果这次也先不看了）。如果你发完消息手头还有活要接着干（比如边回话边查资料、或者发完话顺手压个上下文），记得显式传 `final_call=false`。
- **发送失败或未确认**：万一没发出去或者状态“未确认”，哪怕 `final_call` 传了 `true`，系统也会再跑一轮让你看清状况。失败时字还在输入框里；但如果是**“未确认”**，说明请求已经递出去了只是没拿到回执号，那句话十有八九已经发进聊天里了——这时输入框已经清空，千万别直接重发，先看下一轮聊天记录里有没有它。

## 工具校验与同一步的边界

最后留个底：每次 `reload_tools` 时，每个模块都是独立校验的，哪一个写错了就拦截哪一个，不会连累其它正常的模块。而在你每一次发起子请求前，系统都会把当时可用的工具列表拍个快照；所以你在这一步里 `load_tools` 或 `reload_tools`，要到**下一步（下一个子请求）**才会正式出现在你的工具栏里。同样，同一步里并发的几个工具调用，参数在一开始就同时定下了，如果后一个动作得看前一个动作的结果才能做决定，就分两步走，别急着挤在同一轮里。'''

from __future__ import annotations

import contextvars
import io
import random
import re
import time
from typing import Mapping

from mods.tools import current_binding


def _tool_window():
    from mods import chat, context, history

    session = current_binding().session
    return chat.AGENT_WINDOW if getattr(session, "reads_window_mail", False) and context.agent_mode() else history.window(context.current() or {})
# WHY: `back` 显式写进签名，不用函数属性 `.back`：同步模式要在启动前拒绝 timeout=0，
# 这个判断需要拿到调用方传的 back；属性版只把它交给绑定层，函数自己看不到。后台
# exec_code 也不走 `_tasks` 的那条单队列：timeout=0 可能真的无限等，排进去会把后面的
# 后台工具永远堵住，所以用独立线程。
def exec_code(expr: str, code: str, timeout: float = 15, back: str = "wait") -> str:
    """现成工具不够用、需要直接跑 Python 或组合连续操作时，在 Bot 的共享环境里先跑 code 再算 expr，返回求值结果和捕获的 print 输出。

    这不是沙箱，里面的变量跨调用一直留着，读写文件、发消息、改运行状态都会真生效（需要 Bot 有 op 权限）。

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
    return current_binding().list_text()


def reload_tools(names: list[str]) -> str:
    """新建、改写或删掉 mods/tools/ 下的模块文件后，把磁盘上的改动真正应用进来，返回每个模块的加载结果或报错栈。

    只改文件不调它，生效的还是旧版本；要是新代码有错，会原样保留旧版继续用，不会把正在用的工具弄坏。

    @param
    names: 要应用的模块名列表（不带 .py/.md 后缀）；删了源文件的模块也列在这里才会正式卸掉
    """
    return _format_results(current_binding().reload(names))


def load_tools(names: list[str]) -> str:
    """需要用到某个工具模块或想展开某份 Skill 说明时，把它装进当前会话，下一步就能直接调它的函数、看到它的全文。

    装进来后会一直留着，闲置久了才会自动收起；它只装已生效的版本，刚改了磁盘文件要先 reload_tools。

    @param
    names: 要装入的模块名列表（不带 .py/.md 后缀，可从可用工具目录或 list_tools 里看）
    """
    return _format_results(current_binding().load(names))


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
    """想翻看某个人、某个群、某篇日记或自己的某本笔记时，读取那份笔记的全文。

    @param
    scope: 笔记名，如 self、global、recent、todo、awaker、person:<QQ号>、group:<群号> 或 diary:<日期>
    """
    from mods import chat

    return chat.roles.read_note(scope)


def attach_image(uri: str, note: str = "") -> str:
    """遇到聊天图片、评论配图、截图或验证码这类得亲眼看了才知道的内容时，把图片直接交给你自己看，下一步就能在上下文里看到图。

    @param
    uri: 图片地址，支持 file://、http(s):// 或 data:，工具或评论里给的地址直接传进来就行
    note: 附在图前的备忘，提醒下一步的自己重点看什么，如"看清图里的报错字样"
    """
    from ._vision import attach

    return attach(uri, note)


def _box_window(target: str) -> tuple[tuple, str]:
    """The window *target* names and its label; ValueError when it is unknown or cannot be answered."""
    from mods.chat import windows

    window = windows.parse(target)
    label = windows.label(window)
    if windows.sender(window) is None:
        raise ValueError(f"{label} 只能看，不能在那里说话")
    return window, label

def _boxes() -> dict:
    """The input boxes, one per window; the central agent shares one set across a wake's sessions."""
    from mods.tools import current_binding

    session = current_binding().session
    if not isinstance(getattr(session, "boxes", None), dict):
        session.boxes = {}
    return session.boxes


def _this_output() -> str | None:
    from mods.tools import current_binding

    action = getattr(current_binding().session, "active_action", None)
    return action.split("#")[0] if isinstance(action, str) else None


def type_word(target: str, text: str) -> str:
    """想在群里、私聊或评论区开口说话时，先把话打进对应窗口的输入框（不会立刻发出去），返回输入框当前的内容。

    直接写在回复正文里的话不会发出去，只会留在你自己的行动记录里；说话先打进输入框，下一步看过期间有没有新消息再调 send 发出去。想改口就重新打一遍覆盖，不想说了传空串清空（或者干脆不 send，这轮醒来结束时也会自动清空）。

    @param
    target: 要说话的窗口，如 g<群号>、u<私聊号>、bili:<BV号>，或画面里出现过的可回话窗口
    text: 要打进输入框的完整内容，CQ 码（at、回复、图片）直接写；传空串清空输入框

    WHY: 维护者 2026-10-23：说话先打字、再发送，参照 Cortico 的起草-确认门，但用"输入框"
    的说法——她打出来的字自己看得见。打字和发送之间至少隔一次请求（见 send），于是这期间
    到的消息她一定看过；不靠延迟，人可能隔几分钟才接上下一句。
    """
    from mods.tools import current_binding

    if getattr(current_binding().session, "quiet", False):
        return "现在是交接或反思，不对外说话，什么都没打"
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
            f"（还没发。下一步看过新到的消息再 send(\"{label}\")；不想说了就不发，这次唤醒结束时会清空）")


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


def send(target: str, final_call: bool = True) -> str:
    """看过打字期间的新消息、确认输入框里的话没问题后，把它真正发进目标窗口，返回发出的消息号（message_id）。

    同一轮输出里刚 type_word 的字不能同一步直接 send，总要隔一步让你先看一眼新消息。发出去的话别人当场就能看见；用空行分段的文字会像真人一样拆成几条依次发出。遇到“未确认”说明多半已经发出去了，先看下一轮聊天记录，别直接重发。

    @param
    target: 要发送的窗口，和 type_word 填的一样
    final_call: 说完这句是不是就结束这一轮，默认 True；如果发完还要接着调工具干活，传 False

    WHY: 同一次输出里刚 type_word 的字不发。那一批调用的参数是一起生成的，她还没看到
    打字之后到的消息；拒绝一次，下一次请求里新消息就在眼前（维护者 2026-10-23）。

    WHY: 它**等**发送结果，不是投递完就返回。这不是谨慎，是终止语义逼出来的：一轮的结束
    由 `final_call` 声明，而"失败时照常再跑一轮"要求这里能分辨成败——成败只有 SendFuture
    完成时才知道。实测代价约 0.3~0.6 秒，来自 `message._work` 每条消息后的节流 sleep，
    不是网络。每个种类的发送函数都要这样等（见 chat/windows.py）；QQ 的三类结果、超时和
    不转义的 WHY 跟着发送代码搬到了 `windows._send_qq`。

    WHY: `final_call` 是**参数**而不是这个工具的静态属性。同一个动作有时是一轮的最后一
    件、有时不是，只有发起调用的那一方知道——所以它是模型的意图声明。别把它推广成一张
    "哪些工具终结一轮"的表，那会把判断从调用点搬到一张猜出来的清单上。

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
    from mods.chat import windows
    from mods.tools import current_binding

    try:
        window, label = _box_window(target)
    except ValueError as error:
        return str(error)
    boxes = _boxes()
    box = boxes.get(label)
    if not box:
        return f"{label} 的输入框是空的，什么都没发。先用 type_word 把要说的话打进去"
    if getattr(current_binding().session, "quiet", False):
        return "现在是交接或反思，不对外说话，什么都没发"
    if box["typed_in"] is not None and box["typed_in"] == _this_output():
        return (f"{label} 的字是这一步刚打的，你还没看过打字之后到的消息，这一步先不发。"
                f"下一步看一眼，再决定 send、改还是不发。输入框里现在是：\n{box['text']}")
    if context.agent_mode():
        current_binding().session.associated_windows.add(window)
    unconfirmed = (
        "先在下一轮的聊天记录里看它在不在，再决定要不要重发——不要直接重发。"
    )
    parts = _split_turns(box["text"])
    sent = []
    try:
        for index, part in enumerate(parts):
            sent.append(windows.sender(window)(window, part))
            if index < len(parts) - 1:
                # 人打下一句要时间：随机停一秒到两秒，别像机器连着吐（维护者 2026-10-24）。
                time.sleep(random.uniform(*BUBBLE_GAP))
    except windows.Unconfirmed as error:
        boxes.pop(label, None)
        _spoke(window)
        return f"未确认：{error}输入框已清空。" + unconfirmed
    except Exception as error:
        if sent:
            boxes.pop(label, None)
            _spoke(window)
            return (f"发送失败（{type(error).__name__}）：{error}。"
                    f"前 {len(sent)} 条已经发出去了，剩下的没发，别再重发前面那些。")
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去，还在输入框里。"
    boxes.pop(label, None)
    _spoke(window)
    if final_call:
        current_binding().session.turn_done = lambda: True
    return "、".join(str(item) for item in sent) if len(sent) > 1 else str(sent[0])
def _spoke(window: tuple) -> None:
    """She said something: that group is listened to for a while (awaker), and dismiss cannot clear past it."""
    from mods.chat import awaker

    awaker.spoke(window)
    current_binding().session.last_sent = time.time()


def write_note(scope: str, text: str) -> str:
    """想记下对某个人或群的新了解、更新待办（todo）、改闹钟（awaker）或写日记时，用新的完整内容替换那份笔记，返回写入结果。

    这是整份覆盖（传空串会删掉整份笔记），虽然改前会自动留一份历史备份，动笔前最好先确认现有内容，免得把原本记着的事冲掉。你对自己的认识（self）白天不在这里改，留到反思时用 edit_policy 改。

    @param
    scope: 笔记名，如 global、recent、todo、awaker、person:<QQ号>、group:<群号> 或 diary:<日期>
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


def wake_me(when: str, reason: str) -> str:
    """想让自己过一会儿再醒、等某个人把话说完、或者临时盯紧/静音某个窗口时，给自己埋一个唤醒条件或窗口覆盖层，写入 awaker 笔记并返回生效的规则。

    平时群里只有 @、叫名字、戳一戳才叫醒你（你说完话后的 5 分钟也会自动跟听）。如果你对正在聊的话题感兴趣，可以用 follow 临时跟听每一条（被叫醒不代表每条都要接话，聊完或插不上话了用 off 关掉）；嫌吵就用 mute 暂时静音，期间消息照常记未读，你自己定的 on、wait 闹钟也照常响。

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


def dismiss(since: str = "") -> str:
    """被叫醒看了一眼、发现没什么需要你插手时，把这次唤醒从当前上下文里无痕收起，并直接结束这一轮。

    收起的消息不会丢，只是退回未读，下次醒来会和新消息一起接着给你看；你在这期间改过的笔记或文件也不会撤回。不过一旦你在这期间发过言、输入框里留着刚打的字、或者跟听（follow）攒了超过 200 条消息，就没法再清了。

    @param
    since: 从哪次唤醒开始清，填「这次唤醒你的是（…）」括号里的时刻（只写 时:分:秒 也行），把那次到现在的唤醒一并清掉；留空只清最近这一次
    """
    from mods import chat

    session = current_binding().session
    problem = chat.agent.request_dismiss(session, since)
    if problem:
        return problem
    session.turn_done = lambda: True
    return "这一轮结束后清掉"


def tidy(note: str = "") -> str:
    """一件事告一段落、或者刚翻了一大堆资料只需要留下结论时，发起上下文交接：这一轮结束后把要记的事安顿进笔记，归档旧上下文，换一段清爽的上下文接着过。

    @param
    note: 给交接时的自己留句提醒，比如"结论是端口没问题，排查细节不用记"；可留空
    """
    from mods import chat

    if not chat.agent.request_tidy(note.strip()):
        return "现在没有进行中的唤醒，不需要交接"
    current_binding().session.turn_done = lambda: True
    return "这一轮结束后交接，之后从新的一段接着做"


def compress(from_time: str = "", to_time: str = "", summary: str = "",
             first: int = 0, last: int = 0) -> str:
    """当前上下文还没到整段交接的时候，但中间有几轮已经了结的事太占地方时，把那几轮唤醒折成你写的一段摘要（原文归档），帮自己腾出视野。

    摘要写给接下来的自己看，交代清楚跟谁、什么事、结果如何、还欠着什么（引原话带上 mid，没 mid 就说明是大意）。还在等回话或没查清的事先留着别收；只总结被收掉的那几轮，刚看完的画面和刚说的话还在眼前，不用写进摘要。

    @param
    from_time: 按时间收时的起点唤醒时刻，如 "07:12:03" 或完整时间；用序号收时留空
    to_time: 按时间收时的终点唤醒时刻（不含那一次）；留空表示收到最近一次唤醒之前
    summary: 用来替换原文的摘要内容，可写多行
    first: 按唤醒序号收时的起始序号（见上下文长度提醒里的数字，含两端）
    last: 按唤醒序号收时的结束序号；留空或填 0 表示只收 first 那一轮
    """
    from mods import chat

    return chat.agent.request_compress(current_binding().session, from_time, to_time, summary, first, last)


def loop(note: str = "", stop: bool = False) -> str:
    """需要一轮接一轮连续干活（比如打游戏、一步步操控手机）时，开启或更新连续循环，让这一轮结束后自动再叫醒你接着做。

    总开关开着时，你只需用它记下当前正在忙什么、做到哪算完，每轮醒来都会带着这句备忘；中途要等加载或等回复就调 wait(秒数) 歇一会儿，整件事彻底忙完了再传 stop=True 停下。

    @param
    note: 记下正在做什么、做到哪一步算完，如"洛克王国里走到传送点"；随时重调更新
    stop: 传 True 结束这一串连续循环
    """
    from mods import chat

    if stop:
        stopped = chat.agent.stop_loop("她自己关的", rest=True)
        return f"循环关了（转了 {stopped['round']} 轮）" if stopped else "循环本来就没开"
    session = current_binding().session
    if getattr(session, "quiet", False) or getattr(session, "segment", None) is None:
        return "只有醒着处理事情的时候能开循环；交接、反思和子代理里不行"
    if not note.strip():
        return "要写在做什么，例如 loop(\"洛克王国里走到传送点\")"
    chat.agent.start_loop(note.strip())
    return "循环开着：这一轮结束后会再叫醒你，一轮接一轮；要等就 wait，彻底做完了 loop(stop=True)"


def wait(seconds: float) -> str:
    """在连续循环（loop）里需要等画面加载、等对方回话或暂时没事可做时，先结束这一轮，隔指定秒数后再叫醒你接着转（不阻塞当前进程，也不关循环）。

    等待期间如果有人来消息，照样会提前叫醒你；如果不是在循环里，只是想设个普通闹钟，用 wake_me。

    @param
    seconds: 歇多少秒再叫醒你，范围 2 到 86400（一天）
    """
    from mods import chat

    seconds = max(2.0, min(float(seconds), 86400.0))
    if chat.agent.wait_loop(seconds) is None:
        return "循环关着（#loop off），wait 是循环里用的；只是想过一会儿再醒，用 wake_me(\"10m\", 原因)"
    current_binding().session.turn_done = lambda: True
    return f"好，这一轮到此为止，{chat.agent._span(seconds)}后再叫你；期间有消息照样叫你"


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
    "tidy",
    "dismiss",
    "compress",
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
    "clear_loop_note",
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
    import json
    from pathlib import Path

    path = Path("data") / "context" / "loop.jsonl"
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return "还没有改过循环备注"
    if not rows:
        return "还没有改过循环备注"
    picked = rows[-max(1, min(50, int(last))):]
    lines = []
    for row in picked:
        when = time.strftime("%m-%d %H:%M", time.localtime(float(row.get("t") or 0)))
        lines.append(f"- 第 {row.get('round', '?')} 轮 {when}：{row.get('note', '')}")
    return f"你最近 {len(picked)} 次写下的循环备注（一共 {len(rows)} 条）：\n" + "\n".join(lines)


def clear_loop_note(confirm: bool = False) -> str:
    """清空循环备注的历史记录；系统不会自动清，只有你自己决定。

    @param
    confirm: 传 True 才真的清，防手滑
    """
    from pathlib import Path

    path = Path("data") / "context" / "loop.jsonl"
    if not confirm:
        return "这会清掉全部备注历史，确定就再调一次 clear_loop_note(confirm=True)"
    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        return f"没清掉：{error}"
    return "循环备注历史清空了"
