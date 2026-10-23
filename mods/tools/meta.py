'''指导模型增删查改统一工具与 Skill 模块，并说明 last-good、显式应用和当前会话激活原理。

同一模型输出的多个工具调用按顺序执行，却看不到彼此结果；整批完成后结果在下一子请求可见。若 `send(final_call=true)` 结束、请求取消或失败，不会只为结果强迫续轮。你一直在同一段上下文里，每次被叫醒只往后接上新的东西，直到交接；这一段之外的要自己召回：按窗口翻聊天记录用 `read_log`，按线索找用 `search`（聊天记录、笔记、日记和归档一起找），自己过去的行动用 `read_actions`，笔记用 `read_note`。笔记是普通文件，`write_note` 随时改；一件事告一段落、想清空眼前时用 `tidy` 交接。

## 直接执行 Python

`exec_code(expr, code, timeout)` 用的是 `.py` 命令那份共享环境：先 `exec(code)`，再 `eval(expr)`，返回 `repr(结果)`。`code` 里的 `print` 输出会被捕获后一起回传，不会发进聊天。环境跨调用持久，上一次定义的变量和函数下一次仍然在。

`timeout` 是必填的秒数上限，`0` 表示不限。代码在一个可以被终止的子线程里执行：到点会 kill 掉这次调用启动的子进程、并给那个线程注入中断，然后照常把已经捕获的 print 输出交回来。时限由调用方负责，所以哪怕代码卡在一个打断不了的系统调用里，你也会在时限内拿回控制权——但那个线程可能还留着，**下一次仍然能看见它留下的痕迹**。默认给一个够用的值，别用 `0` 图省事：这条路的代价是线程和子进程，不是耐心。

只有 Bot 自身拥有 op 权限时可用，否则返回"权限不足"。

环境里预置了 Bot 的全部动态导出名：`send`/`sendmsg` 发消息，`getname`/`setname`/`getstorage`/`getgroupstorage`/`memberlist` 读写身份与存储，`data` 是持久字典，`prompts` 是提示词集合，`run_action` 触发 link 动作，还有 `os`、`json`、`re`、`time`、`random`、`math`、`datetime` 等标准库；`ctx` 是模块名到模块对象的字典，各个 `mods` 模块也能直接按名字使用。不确定有什么就先自查，例如 `exec_code("sorted(k for k in globals() if not k.startswith('_'))")`。

跨较长时间检查自己的经历时，可加载 `self_inspection` Skill，并用 `ctx["oplog"].iter_events(start, stop)` 逐条读取磁盘记录；不要为临时筛选继续增加专用工具，也不要调用会全量恢复历史的内部容器。

它与 Bot 共享同一个宿主信任域，不是沙箱：会真实发消息、读写磁盘、改动运行中的状态。用之前先确认没有现成工具能做这件事，并避免 `input(...)`（会阻塞等待聊天回复）和长时间运行的代码。异常以 traceback 回传，可据此修正重试。需要反复使用的能力应当沉淀成下面的工具模块，而不是每次都 `exec_code`。

## 让模型自己看图

`attach_image(uri, note)` 把一张图片交给**你自己**看：地址写成工具结果里的一行 markdown
引用，`llm._convert_images` 在发出请求前把它展开成真正的图。所以下一个子请求就看得见它
（`✅` 走的就是这条路，`⚠️` 表示这次走不通，退回让视觉模型转述）。

对端支不支持"工具结果带图"看模型的 `tool_images` 能力位；不支持时退回老办法——把图作为
一条 user 消息附加到下一次请求，只活一次子请求、不进历史。支持时图**留在操作记录里**，
哪次调用看到了哪张图以后还查得到，代价是它每轮都要跟着上下文重发。

## 工具模块的三层状态

所有模块源都放在 `mods/tools/` 顶层。`foo.py` 是带函数的工具模块，`foo.md` 是没有函数的 Skill；二者使用同一套生命周期，不能同 stem 共存。

1. 磁盘源码：刚编辑的文件，还不一定生效。
2. 进程级 last-good：最后一次成功初始化或 `reload_tools` 的完整模块版本。
3. 中心 agent 的持久激活态：经 `load_tools` 加入的模块内容和函数。它属于唯一主体而不是最近读到的窗口；下一轮自动从全局名单装回，跨重启保留。

`reload_tools` 从磁盘应用源码；`load_tools` 只激活 last-good，不能混用。没有自动 watcher，也不要等待修改自行生效。

激活会自己到期，你不需要收拾它：超过一段时间没有被装入或调用过的模块，下一轮开局就不再装回；下一次模型请求前会把这项变化追加为正式工具状态 input（确切时限见 `list_tools` 的"空闲回收"一节）。装入本身算一次"用过"，所以只靠读正文的模块（`.md` 技能，以及只提供说明的 `.py`）按装入时刻计时——**阅读不留痕迹**，读得再勤也不会续期。还要用就重新 `load_tools`，那是一次往返，不必省。

因为激活属于会话主体，同一件事不需要每轮重复 `load_tools`，也不会因为一轮聊完就消失。会"自己消失"的情况有三种，下次请求前会与已告知状态对账并追加变化 input：

- **空闲收回**：见上一段，主体的激活记录一并清掉，还要用就重新 `load_tools`。
- **Bot 未获权限**：`op` 这类模块要求 Bot 自身拥有 op 权限。主体的激活记录**仍然留着**，以后修改权限配置并重启即可装回。
- **已不存在**：模块的源码已经没了，主体的激活记录也一并清掉。

三种都表示当前没有激活它——不要照着上一轮的印象直接调它的函数。已发送过的函数定义仍留在请求 schema 中，但调用只会提示先 `load_tools`，不执行已卸载代码。完整最新目录及状态可随时用 `list_tools` 取得；开头不再每轮重发目录。

## 查询

先调用 `list_tools()`。它列出 last-good 模块及一句话描述、当前会话已激活模块、空闲回收规则与时限、磁盘相对 last-good 的差异，以及最近的加载失败 traceback。需要阅读源码时，再用文件能力或 `exec_code` 精确读取 `mods/tools/<name>.py` 或 `.md`。

## 新增 Python 工具模块

选择不以下划线开头、未被占用的模块名，先确认同 stem 的 `.py` 和 `.md` 都不存在。普通模块不需要修改 loader。格式如下：

```python
"""一句话说明这组工具解决什么问题，不能换行。

这里开始的内容在模块激活后的请求边界进入正式工具状态 input，可写使用时机、约束和组合方式。
"""

from some_package import dependency


def lookup(query: str, limit: int = 10) -> str:
    """查询目标并返回文本结果。

    @param
    query: 查询内容
    limit: 最大结果数
    """
    return str(dependency.lookup(query, limit=limit))


__all__ = ["lookup"]
```

一个文件可以通过 `__all__` 导出多个同步函数，也可导出空列表、只提供说明。每个参数都要有类型标注，函数要有 docstring，签名必须能按关键字调用；不要使用位置专用参数、`*args`、`**kwargs` 或异步函数。模型侧函数名带模块命名空间，例如 `foo__lookup`。`meta` 是始终激活的保留模块，它导出的工具不加前缀。

Python 模块可以正常 import 第三方依赖、其它 `mods`，也可以 `from ._helper import value` 引用同目录以下划线开头的 helper。候选加载会执行顶层代码，所以顶层只放 import、常量和定义；它与 Bot 处在同一宿主信任域，不是沙箱。

写入后先调用 `reload_tools(["foo"])` 完成整模块校验并建立 last-good，再调用 `load_tools(["foo"])` 把余下说明和整组函数激活到当前 Chat。

## 新增 Markdown Skill

建立 `mods/tools/foo.md`。第一行必须是一句无需展开就能判断用途的 summary，第二行开始全部是 Skill 正文：

```markdown
指导模型审查发布清单并识别遗漏的部署步骤。

## 使用时机
……
```

Markdown 不需要 front matter、额外 summary 字段或同步机制，也不导出函数。目录不递归扫描；Skill 可在正文中引用子目录资源。写完同样先 `reload_tools(["foo"])`，需要在当前任务使用时再 `load_tools(["foo"])`。

Skill 也是可编辑的长期笔记：把可重复使用的经验、做法、失败教训按主题写进去，首行说明何时值得加载；下次可从模块目录发现，再按需 `load_tools` 把正文带进上下文。要自己落盘时，先 `load_tools(["host"])`，用 `host__read_file`／`host__write_file` 精确编辑源文件，再 `reload_tools` 应用。不要为了记一件事就改始终加载的基础 prompt，也不要把整段聊天照搬进 Skill。Skill 文件可被中心 agent 发现；私人聊天原话仍保留原来源窗口，中心 agent 可按旧正式号反查。Skill 只写可复用的做法，临时待办不要塞进 Skill，写进 `todo` 笔记。

## 修改

先精确读取现有源文件，只修改目标模块，再调用 `reload_tools(["foo"])`。成功后 last-good 才替换；如果模块已在当前 Chat 激活，内容和函数会为下一次模型子请求更新。失败时根据返回的完整 traceback 修复并再次 reload，旧 last-good 和旧活动版本继续服务。仅调用 `load_tools` 不会读取刚改的磁盘文件。

下划线 helper 不是独立模块，它的变化不会单独出现在 `list_tools` 中。修改 helper 后要显式 reload 所有 import 它的模块。

`reload_tools` 换得动的只有 `mods/tools/` 下的那一个文件（外加它的下划线 helper）。它 `import` 的 `mods.*`——包括这套机制自己的 `mods/tools/__init__.py`——是进程启动时的那份，改了要重启才生效，典型症状是“说明书和工具清单里有、调用时 AttributeError”。这条边界的完整说明在仓库的 `docs/runtime.md`（“热更换得动什么”一节），需要重启时用 op 工具集的 `send_command` 注入 `.reboot`；重启后先检查未读通知与信源状态，不要假设旧调用会重放。

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数，当前会话的激活记录一并清除。只删文件但不 reload 时旧 last-good 仍有效。`meta.py` 是恢复入口，不能删除。删除 helper 前先检查并 reload 所有受影响模块。

## 经历与笔记

每次模型输出（正文、多个行动）记进 oplog，有一个 `YYYYMMDD-N` 行动号；工具结果随它记下。你一直在同一段上下文里：开头是做法笔记（global）、上次交接留下的 recent、最近一篇日记和一份目录，之后每次唤醒往后接上新消息和第一次出现的人或群的笔记，末尾是待办（todo）。交接之后这一段归档，没出现的东西没有丢，召回是你自己的事。

- 聊天记录就是 `chatlog/` 的原文，`read_log(window, count, before)` 读任意窗口，格式与画面相同；`before` 写 `YYYY-MM-DD HH:MM:SS` 往前翻页。
- 记得一点线索（名字、话题、某个词）就用 `search(keyword, window, days)`：一次找遍聊天记录（默认最近 30 天）、所有笔记和日记、归档的上下文，按出处分组；再用 `read_log` 读聊天前后文，`read_note` 读笔记全文。
- 自己做过的事用 `read_actions(ids)` 按行动号读，或不给号读最近几条，`keyword` 按词筛；太长的结果按结尾提示用 `start` 接着读。
- 笔记都是 `data/notes/` 下的普通文件，你随时能读能写：global 是你的做法，recent 是短期记忆，todo 是待办，awaker 是你的闹钟规则，person:<QQ号>、group:<群号> 记认识与经历，diary:<日期> 是当天的日记，每次交接改写成到目前为止的这一天（月、年各有汇总）。`read_note(scope)` 读，`write_note(scope, text)` 整份替换，改前自动备份。认识了新的人、答应了什么、被纠正了什么，想记就当场记。
- 交接：你调用 `tidy`、这一段上下文太长、或者到了夜里，这段就要被移除。你会在这一段末尾收到交接指令，确认眼下的事、关于人和群的、做法的教训、答应的事、今天的事都在各自的笔记里，然后这段归档，下一段从新的笔记开始。
- 想定时醒来（比如每 10 分钟看一眼，或者某个时刻提醒谁），就改 awaker 笔记，每行一条：`every 10m 理由`、`daily 23:30 理由`、`at 2026-10-23 08:00 理由`；`daily 04:00 reflect` 是每晚的交接与反思，删掉它就不再反思。到点的闹钟会把理由交给你，`at` 规则响过后记得删掉。
- 记忆系统本身怎么运作、怎么像人一样排查自己、你能怎么改它，见 `memory_system`；每晚反思会自动加载它。
- 更长时间跨度的自查可加载 `self_inspection` Skill，用 `exec_code` 读磁盘记录。

## 待办

待办就是 `todo` 笔记：`write_note("todo", 全文)` 整体替换，传空串清空。每次模型子请求的末尾都会显示最新内容。同一模型输出中的多个工具调用彼此看不到结果：先等工具返回进入下一子请求，再记录依赖成败的待办。改动不会发送 QQ 消息；要对人说话仍须 `type_word` 再 `send`。它与聊天结束后向 QQ 发状态消息的 `#hint` 命令不是一回事。只放尚待处理的事，做完及时更新；可复用经验放 Skill。

## 说话

说话像人用手机打字：先 `type_word(target, text)` 把话打进那个窗口的输入框，下一步再 `send(target)` 发出去。这是你**唯一**的发言方式——直接写在回复正文里的内容不会发出去，那是你的自言自语；它会作为自己的输出轨迹保留，但聊天参与者收不到。每次都要写明 `target="g<群号>"` 或 `target="u<私聊对端号>"`，不能凭最近读到谁来猜接收窗口。

- `type_word` 覆盖输入框里原来的内容（想改就整句再打一遍，传空串清空），返回框里现在的字。不想说了就不发，这次唤醒结束时没发的会清空；中途交接了，接着做时会告诉你框里还有什么。
- 同一次输出里刚打的字 `send` 不发：打字和发送之间你至少要再看一眼，这期间到的新消息会出现在下一次请求里，看完再决定发、改还是不发。
- `send` 返回这条消息的 `message_id`，可用于与聊天记录里的回声核对。
- `final_call` 默认 **true**：发完这句，当前工具循环结束；同一批里其它工具的结果这次看不到。只有新召唤会让中心 agent 接着运行。若要在当前循环接着干活，显式传 `final_call=false`。
- 发送**失败**或**未确认**时，不管 `final_call` 传了什么，都会照常再跑一轮，让你看到发生了什么。失败时输入框里的字还在；"未确认"的意思是请求被收下了但没给回号码，消息很可能已经发出去——输入框已清空，别直接重发，先看下一轮的聊天记录。
- 一个输入框一次发一条消息。要发几条就打一条、发一条，最后一条传默认的 `final_call=true`。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。同一模型输出里的多个工具调用虽按序执行，参数却已一起生成，彼此看不到返回；依赖前一工具成败的判断须等下一子请求。
'''

from __future__ import annotations

import io
from typing import Mapping

from mods.tools import current_binding


def _tool_window():
    from mods import chat, context, history

    session = current_binding().session
    return chat.AGENT_WINDOW if getattr(session, "reads_window_mail", False) and context.agent_mode() else history.window(context.current() or {})


def exec_code(expr: str, code: str, timeout: float) -> str:
    """在 Bot 进程的共享 Python 环境中先执行 code、再求值 expr，返回 repr 结果和被捕获的 print 输出；需要管理员权限。

    @param
    expr: 在 code 之后求值并返回的单个表达式；只想执行 code、不关心返回值时传字符串 None
    code: 先执行的 Python 语句，可以多行；不需要时传空字符串
    timeout: 秒数上限，必填；0 表示不限，到点会终止这次调用启动的子进程并中断执行线程
    """
    from mods import context, op, py, watchdog

    if not op.bot_is_op():
        return "权限不足"
    # WHY: 代码现在跑在一个子线程里（见 watchdog.run），所以它在代码里改
    # `context.set_current` 再也影响不到本线程。工具本身仍然可能改，所以照旧进来先快照、
    # 出去无条件还原：工具可以读路由，但不该在自己脚下把它换掉——2026-09-17 就因此把私聊的
    # 回复发进了群。
    original_event = context.current()
    buffer = io.StringIO()
    missing = object()
    original = py.loc.get("print", missing)
    py.loc["print"] = lambda *values, sep=" ", end="\n": buffer.write(
        sep.join(map(str, values)) + end
    )

    def perform():
        exec(code, py.loc)
        return eval(expr, py.loc)

    try:
        try:
            result = repr(watchdog.run(perform, timeout))
        except TimeoutError as error:
            result = f"超时：{error}"
        except watchdog.Interrupted:
            result = "已被 ^C 中断"
    finally:
        context.set_current(original_event)
        if original is missing:
            py.loc.pop("print", None)
        else:
            py.loc["print"] = original
    printed = buffer.getvalue().rstrip()
    return f"[print输出]\n{printed}\n[结果] {result}" if printed else result


def list_tools() -> str:
    """列出完整当前工具状态（目录、已激活说明）、空闲回收、磁盘差异和最近失败；查可用模块名先调用它。"""
    return current_binding().list_text()


def reload_tools(names: list[str]) -> str:
    """从磁盘读取并应用指定模块的源码改动，逐个模块返回成功或完整 traceback；失败的模块继续沿用旧 last-good 版本。改完文件必须调用它，改动才会生效。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；源文件已删除的模块要显式列在这里才会被卸载
    """
    return _format_results(current_binding().reload(names))


def load_tools(names: list[str]) -> str:
    """激活 last-good 模块到当前 Chat；中心会话的名单全局持久，不读取磁盘。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；名字来自 list_tools
    """
    return _format_results(current_binding().load(names))


def read_log(window: str, count: int = 30, before: str = "") -> str:
    """读一个聊天窗口的聊天记录原文，格式与眼前的画面相同，从最新往前取；before 用来往前翻页。

    @param
    window: g<群号> 或 u<私聊对端号>
    count: 取多少条记录，1 到 200
    before: 只取这个时刻之前的记录，写 YYYY-MM-DD HH:MM:SS（画面里的日期加时间）或 Unix 秒；留空表示到最新
    """
    from mods import chat

    return chat.roles.read_log(window, count, before)


def search(keyword: str, window: str = "", days: int = 30) -> str:
    """按线索一次找遍：聊天记录、笔记、日记和归档的上下文，按出处分组列出，每条带出处（窗口和日期、笔记名、日记日期、段名）。找到后用对应的工具读全文：聊天记录用 read_log 的 before 读前后文，笔记和日记用 read_note，归档的段加载 memory_system 后用 read_context。

    @param
    keyword: 要找的词（不区分大小写，按原文子串匹配；名字、QQ号、话题里的词都行）
    window: 聊天记录只找这个窗口（g<群号> 或 u<私聊对端号>）；留空找所有窗口。笔记、日记和归档照样都找
    days: 聊天记录和归档往前找多少天，1 到 365；笔记和日记不限时间
    """
    from mods import chat

    return chat.roles.search(keyword, window, days)


def read_actions(ids: list[str] | None = None, count: int = 10, keyword: str = "", start: int = 0) -> str:
    """读自己过去的行动记录原文：说过的话、调用过的工具及其结果。一次最多 8000 字，更长的按 start 往后翻。

    @param
    ids: 行动号列表，如 20261019-12；留空则取最近 count 条
    count: 不给 ids 时取最近多少条，1 到 50
    keyword: 只取原文里含这个词的行动（不区分大小写，在完整原文里找）
    start: 从第几个字开始读，用上一次结尾提示的位置接着读
    """
    from mods import chat

    return chat.roles.read_actions(ids, count, keyword, start)


def read_note(scope: str) -> str:
    """读一份笔记全文。

    @param
    scope: self、global（做法）、recent（上次交接留下的）、todo、awaker、person:<QQ号>、group:<群号> 或 diary:<日期>
    """
    from mods import chat

    return chat.roles.read_note(scope)


def attach_image(uri: str, note: str = "") -> str:
    """把一张图片交给**你自己**看——截图、图表、验证码、扫描件这类"只有看了才知道"的东西用它，不要让你自己靠猜。图随这条工具结果一起送到，下一个子请求里就看得见（`✅`/`⚠️` 是结果说明）。

    @param
    uri: 图片地址，`file://`、`http(s)://` 或已经是 `data:` 都行；浏览器截图给出的 `file://` 直接传进来即可
    note: 附在图前面的一句话，例如"读出图中的字"
    """
    from ._vision import attach

    return attach(uri, note)


_SAY_TIMEOUT = 30.0


def _box_label(target: str) -> str:
    from mods import chat

    kind, number = chat.parse_target(target)
    return f"{'g' if kind == 'group' else 'u'}{number}"


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
    """把要说的话打进 target 的输入框，覆盖原来的内容；不发送，下一步看过新消息再 send。

    @param
    target: g<群号> 或 u<私聊对端号>
    text: 输入框里的完整内容。CQ 码原样写，at、reply、图片都照常生效；空串清空输入框

    WHY: 维护者 2026-10-23：说话先打字、再发送，参照 Cortico 的起草-确认门，但用"输入框"
    的说法——她打出来的字自己看得见。打字和发送之间至少隔一次请求（见 send），于是这期间
    到的消息她一定看过；不靠延迟，人可能隔几分钟才接上下一句。
    """
    from mods.tools import current_binding

    if getattr(current_binding().session, "quiet", False):
        return "现在是交接或反思，不对外说话，什么都没打"
    try:
        label = _box_label(target)
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


def send(target: str, final_call: bool = True) -> str:
    """把 target 输入框里打好的字发出去，返回 message_id；默认发完这一轮就结束。

    @param
    target: g<群号> 或 u<私聊对端号>，要和 type_word 用的一样
    final_call: 这次发言是不是本轮最后一个动作。默认 true；要接着干活就显式传 false

    WHY: 同一次输出里刚 type_word 的字不发。那一批调用的参数是一起生成的，她还没看到
    打字之后到的消息；拒绝一次，下一次请求里新消息就在眼前（维护者 2026-10-23）。

    WHY: 它**等**发送结果，不是投递完就返回。这不是谨慎，是终止语义逼出来的：一轮的结束
    由 `final_call` 声明，而"失败时照常再跑一轮"要求这里能分辨成败——成败只有 SendFuture
    完成时才知道。实测代价约 0.3~0.6 秒，来自 `message._work` 每条消息后的节流 sleep，
    不是网络。

    WHY: 三类结果，不是两类。异常＝没发出去；拿到 id＝发出去了；而 `retcode == 0` 却没有
    message_id 是**第三类**——`_send_now` 此时既不抛也没有号码，消息很可能已经发出去。
    把它当失败会让模型重发一条已经在窗口里的话，当成功又会让这次发言在 oplog 里没有
    可反查的号码。所以单列成"未确认"，照常触发下一轮，由模型自己去看聊天记录。
    删除条件：send_msg 在 retcode 为 0 时被证明总是带 message_id。

    WHY: **超时归"未确认"，不归"失败"。** 等待超时说明 future 还没有结果，而不是结果是
    失败——消息可能正排在发送队列里，也可能已经发出去了。把它写成"没有发出去"是一句会
    骗到模型的断言，它会照着重发。队列上限 20、每条节流最多 0.6 秒，最坏约 12 秒，所以
    30 秒是留了一倍余量的"大概率不是排队问题"。

    WHY: `final_call` 是**参数**而不是这个工具的静态属性。同一个动作有时是一轮的最后一
    件、有时不是，只有发起调用的那一方知道——所以它是模型的意图声明。别把它推广成一张
    "哪些工具终结一轮"的表，那会把判断从调用点搬到一张猜出来的清单上。

    WHY: 正文**不转义**。模型是 CQ 原生的一方：收到的消息原样带 CQ 码，写出去的也照原样
    发，往返因此闭合。在这里 `cq.escape` 会让它写的 at 和图片变成字面文本。

    WHY: 它住在 meta 而不是自己一个模块，因为发言是模型**永远**该有的能力，而
    `tools._BASE_MODULE_NAME` 是单数、基础模块只有一个。meta 早就不只是"管工具模块"了
    （事件反查和覆盖工具也管上下文），它实际是那组不可卸载的基础能力，`type_word`/`send`
    属于这一组。改成支持多个基础模块也行，代价是那条"基础模块的导出不加模块名前缀"的
    规则会多出命名冲突的可能，眼下不值得。

    WHY?: 子代理（`tools/agents.py`）因此也拿得到 `type_word`/`send`（2026-10-23 以前是 `say`），于是它能直接往窗口里说话——这是
    工具化带来的**新**能力，以前子代理的正文只回给主模型。没有给它加门控，因为"子代理该
    不该能说话"还没有人拍过；先记在这里，真出现不想要的发言再决定是挡掉还是保留。
    """
    from mods import message
    from mods import chat, context
    from mods.tools import current_binding

    try:
        label = _box_label(target)
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
    window = chat.parse_target(target)
    destination = {"group_id" if window[0] == "group" else "user_id": window[1]}
    if context.agent_mode():
        current_binding().session.associated_windows.add(window)
    unconfirmed = (
        "先在下一轮的聊天记录里看它在不在，再决定要不要重发——不要直接重发。"
    )
    try:
        message_id = message.sendmsg(box["text"], **destination).result(timeout=_SAY_TIMEOUT)
    except TimeoutError:
        boxes.pop(label, None)
        return f"未确认：等了 {_SAY_TIMEOUT:.0f} 秒还没有结果，这句话可能正在发、也可能已经发出去。输入框已清空。" + unconfirmed
    except Exception as error:
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去，还在输入框里。"
    boxes.pop(label, None)
    if message_id is None:
        return "未确认：对端收下了请求，却没有回一个 message_id，所以这句话可能已经发出去了。输入框已清空。" + unconfirmed
    if final_call:
        current_binding().session.turn_done = lambda: True
    return str(message_id)


def write_note(scope: str, text: str) -> str:
    """用新全文替换一份笔记（普通 Markdown 文件，改前自动备份）；空串删除整份笔记。

    @param
    scope: global、recent、todo、awaker、person:<QQ号>、group:<群号> 或 diary:<日期>（self 只能在夜间反思时用 edit_policy 改）
    text: 这份笔记更新后的完整内容；改之前先 read_note 看现有内容，别把别人写的覆盖掉
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
    """给自己埋一个唤醒：过一会儿、到某个时刻、某个窗口来消息、等某人的后续、或者某个条件成立时叫醒你。

    @param
    when: 下面一种写法——"10m"/"2h"：过这么久；"2026-10-23 08:00"：到这个时刻；"on g123"、"on g123:30001"、"on u456"：这个窗口（或窗口里这个人）下一条消息来时；"wait g123:30001 10m"：等这个人的后续，来了或者 10 分钟都没来都叫你，只叫一次；"when <Python 表达式>"：每分钟用 exec_code 那份环境求值，第一次为真时叫你，例如 when mcversion.ver["release"] != "1.21.4"；"every 30m"、"daily 23:30"：反复叫你
    reason: 叫醒时你会看到的原因，写给到时候的自己，比如"他刚才问到一半"

    WHY: 维护者 2026-10-23：她要能自己决定什么时候再醒。最常用的是 wait——对方话说了一半、
    她决定先不接，后续一直不来那半句就挂着；死板的延迟唤醒也不行，对方接上了延迟到点还会再
    叫一遍。规则写进笔记 awaker（相对时间换成时刻），她用 read_note/write_note 能看能改。
    想在某个函数被调用时醒，用 exec_code 包一层它，里面调 chat.wake(reason=..., kind=...)，
    重启后要重新包。
    """
    from mods.chat import awaker

    try:
        line = awaker.plant(when, reason)
    except ValueError as error:
        return str(error)
    once = line.split(" ", 1)[0] in ("at", "on", "wait", "when")
    return f"已写进 awaker：{line}" + ("（响过就划掉；不要了用 write_note 改 awaker）" if once else "（不要了用 write_note 改 awaker）")


def tidy(note: str = "") -> str:
    """交接：这一轮结束后，把这一段上下文要记的东西确认到各自的笔记里，然后清空眼前、从新的一段接着做。一件事告一段落、或刚读了一大堆材料只需要记住结论时用。

    @param
    note: 交接的原因或要特别注意的地方，比如"结论是端口没问题，细节不用记"；可以留空
    """
    from mods import chat

    if not chat.agent.request_tidy(note.strip()):
        return "现在没有进行中的唤醒，不需要交接"
    current_binding().session.turn_done = lambda: True
    return "这一轮结束后交接，之后从新的一段接着做"


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
    "wake_me",
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
    "attach_image",
]
