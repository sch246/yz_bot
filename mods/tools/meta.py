'''指导模型增删查改统一工具与 Skill 模块，并说明 last-good、显式应用和当前会话激活原理。

## 直接执行 Python

`exec_code(expr, code, timeout)` 用的是 `.py` 命令那份共享环境：先 `exec(code)`，再 `eval(expr)`，返回 `repr(结果)`。`code` 里的 `print` 输出会被捕获后一起回传，不会发进聊天。环境跨调用持久，上一次定义的变量和函数下一次仍然在。

`timeout` 是必填的秒数上限，`0` 表示不限。代码在一个可以被终止的子线程里执行：到点会 kill 掉这次调用启动的子进程、并给那个线程注入中断，然后照常把已经捕获的 print 输出交回来。时限由调用方负责，所以哪怕代码卡在一个打断不了的系统调用里，你也会在时限内拿回控制权——但那个线程可能还留着，**下一次仍然能看见它留下的痕迹**。默认给一个够用的值，别用 `0` 图省事：这条路的代价是线程和子进程，不是耐心。

只有 Bot 自身拥有 op 权限时可用，否则返回"权限不足"。

环境里预置了 Bot 的全部动态导出名：`send`/`sendmsg` 发消息，`getname`/`setname`/`getstorage`/`getgroupstorage`/`memberlist` 读写身份与存储，`data` 是持久字典，`prompts` 是提示词集合，`run_action` 触发 link 动作，还有 `os`、`json`、`re`、`time`、`random`、`math`、`datetime` 等标准库；`ctx` 是模块名到模块对象的字典，各个 `mods` 模块也能直接按名字使用。不确定有什么就先自查，例如 `exec_code("sorted(k for k in globals() if not k.startswith('_'))")`。

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
3. 中心 agent 的持久激活态：经 `load_tools` 加入的模块内容和函数。它属于唯一主体而不是最近读到的窗口；下一轮自动从全局名单装回，跨重启保留。私有 `.chat` 仍有自己的窗口激活态。

`reload_tools` 从磁盘应用源码；`load_tools` 只激活 last-good，不能混用。没有自动 watcher，也不要等待修改自行生效。

激活会自己到期，你不需要收拾它：超过一段时间没有被装入或调用过的模块，下一轮开局就不再装回，并给你一条"已停用（空闲收回）"的通告，里面写着被收走的是哪个模块、干什么用的（确切时限见 `list_tools` 的"空闲回收"一节）。装入本身算一次"用过"，所以只靠读正文的模块（`.md` 技能，以及只提供说明的 `.py`）按装入时刻计时——**阅读不留痕迹**，读得再勤也不会续期。还要用就照通告说的重新 `load_tools`，那是一次往返，不必省。

因为激活属于会话主体，同一件事不需要每轮重复 `load_tools`，也不会因为一轮聊完就消失。会"自己消失"的情况有三种，都会以一条系统通告告诉你，形如 `- 已停用（原因）：模块名`；通告只报状态和原因，三个原因的意思在这里：

- **空闲收回**：见上一段，主体的激活记录一并清掉，还要用就重新 `load_tools`。
- **Bot 未获权限**：`op` 这类模块要求 Bot 自身拥有 op 权限。主体的激活记录**仍然留着**，以后修改权限配置并重启即可装回。
- **已不存在**：模块的源码已经没了，主体的激活记录也一并清掉。

三种都只是"这一轮它不在了"——不要照着上一轮的印象直接调它的函数，本轮快照里没有那个名字，你会白跑一次。要确认还有什么、叫什么，看会话开头那份模块目录：它无条件列出每个模块的名字和一句话描述，与激活与否无关，被收掉的模块照样在里面。

## 查询

先调用 `list_tools()`。它列出 last-good 模块及一句话描述、当前会话已激活模块、空闲回收规则与时限、磁盘相对 last-good 的差异，以及最近的加载失败 traceback。需要阅读源码时，再用文件能力或 `exec_code` 精确读取 `mods/tools/<name>.py` 或 `.md`。

## 新增 Python 工具模块

选择不以下划线开头、未被占用的模块名，先确认同 stem 的 `.py` 和 `.md` 都不存在。普通模块不需要修改 loader。格式如下：

```python
"""一句话说明这组工具解决什么问题，不能换行。

这里开始的内容只在模块激活后进入 system 提示，可写使用时机、约束和组合方式。
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

Skill 也是可编辑的长期笔记：把可重复使用的经验、做法、失败教训按主题写进去，首行说明何时值得加载；下次可从模块目录发现，再按需 `load_tools` 把正文带进上下文。要自己落盘时，先 `load_tools(["host"])`，用 `host__read_file`／`host__write_file` 精确编辑源文件，再 `reload_tools` 应用。不要为了记一件事就改始终加载的基础 prompt，也不要把整段聊天照搬进 Skill。Skill 文件可被中心 agent 发现；私人聊天原话仍保留原来源窗口，中心 agent 可按旧正式号反查。Skill 只写可复用的做法，临时待办不要塞进 Skill，用 `edit_hint`。

## 修改

先精确读取现有源文件，只修改目标模块，再调用 `reload_tools(["foo"])`。成功后 last-good 才替换；如果模块已在当前 Chat 激活，内容和函数会为下一次模型子请求更新。失败时根据返回的完整 traceback 修复并再次 reload，旧 last-good 和旧活动版本继续服务。仅调用 `load_tools` 不会读取刚改的磁盘文件。

下划线 helper 不是独立模块，它的变化不会单独出现在 `list_tools` 中。修改 helper 后要显式 reload 所有 import 它的模块。

`reload_tools` 换得动的只有 `mods/tools/` 下的那一个文件（外加它的下划线 helper）。它 `import` 的 `mods.*`——包括这套机制自己的 `mods/tools/__init__.py`——是进程启动时的那份，改了要重启才生效，典型症状是“说明书和工具清单里有、调用时 AttributeError”。这条边界的完整说明在仓库的 `docs/runtime.md`（“热更换得动什么”一节），需要重启时用 op 工具集的 `send_command` 注入 `.reboot`；重启后先检查未读通知与待续读页，不要假设旧调用会重放。

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数，当前会话的激活记录一并清除。只删文件但不 reload 时旧 last-good 仍有效。`meta.py` 是恢复入口，不能删除。删除 helper 前先检查并 reload 所有受影响模块。

## 操作历史与结论收缩

每次完整模型输出（思考、正文、多个行动）只有一个 `YYYYMMDD-N` 号；该输出内部行动用 `YYYYMMDD-N#位置` 指名。消息和工具结果也在被读到时进入同一信息流。结果可能在下一次模型请求才读到，未读不占正式号。

这些记录跨轮按读到的先后重建，未收缩的结果保留原文。中心会话用 `cover_events` 总结已读事件；私有 `.chat` 仍可用 `condense_ops(["20260923-4#1", "20260923-4#2"], "结论")` 收缩原生工具配对。

结论写在 `conclusion` 参数里就够了，工具不会把它再返回一遍：这次调用本身留在上下文里，参数里的结论就是它的记录。

已读消息和输出也能总结：中心会话调用 `cover_events(["20260923-4", "20260923-5"], "结论")`，参数用的是上文方括号里的正式事件号，不是 QQ `message_id` 或行动位置。覆盖使成员从本轮和后续自动上下文中消失；原号可用 `recall_events` 反查而不解除覆盖，反查总结会显示实际冻结的所有成员，不只是你传入的号。中心会话可跨窗口点名已读或反查过的旧号；整个输出与其返回批次不可拆，已确认的 `say`／回声也必须成组。未读成员不会被补进覆盖。摘要与聊天消息、输出、返回一样占历史事件数和 token 预算；私有 `.chat` 和子代理不能替中心会话写覆盖。

眼前历史只是全局已读信息流按事件数和 token 选出的可见部分，不是完整记录。新消息先留在各窗口的未读队列；通知只告诉你哪里有消息。`peek(target)` 看本地档案，`peek_napcat(target)` 只读预览远端，均不消未读。启动时 NapCat 补回先于该窗口新实时消息进入原 FIFO；补回未收束前该窗口不能正式拉取。`pull_mail(target)` 才从窗口 FIFO 正式读一小页。主动查更早历史用 `fetch_source(target)` 开启或扩展有名信源，`source_status` 看状态与缺口，`peek_source` 预览，`pull_source` 正式读；已经读过的信源不会倒插旧内容，后续 fetch 另开 FIFO。远端历史不保证无缺口。回复可以先于补读；回复后仍须从原队首逐页追到固定终点。总结改变默认显示，不删除原文。反查和预览的返回会成为新的阅读经历，旧记录本身不变。

总结要方便反查：在结论里留下关键来源的正式号，别只写一段没有出处的大块故事。后续总结可以再次引用前一层总结；同一来源也可被多个不同主题的总结引用，不必强行归到唯一父节点。已被覆盖的正式号也可再次点名，与新的可见成员组成另一份总结；这不解除原覆盖。`event_links` 可查输入、输出和工具返回中明确出现的正式号，以及各节点实际覆盖的成员；“出现过编号”、“被覆盖”和“确实支撑某个结论”是三回事，核对原话仍须 `recall_events`。

收缩是**可逆**的：结果离开后续模型视图，原文仍可用 `recall_ops(["20260923-4#1"])` 取回。`#ops clear` 清空操作视图，但不重用号码或物理删除事件。

普通输入、完整输出及整批工具返回都可用 `recall_events(["20260923-4"])` 按正式号反查；这不自动把整个载荷放回后续模型上下文。

不必穷举相邻编号：`event_span(anchor="20260923-4", before=5, after=5)` 按唯一的已读经历顺序列出中心前后的小段正式号；也可用 `start`／`end` 指定有界区间。`kinds` 和 `source` 只在选定范围内筛选，不改变顺序，也不会自动读出正文。拿返回的正式号再调用 `recall_events` 或 `cover_events`；两者仍由你决定具体要读或覆盖哪些成员。

同一输出里的多个行动必须一起点名收缩；尚未返回的行动不能收缩。

中心会话只载入一条全局经历的可见后缀；旧窗口正式号仍能按原号跨窗口反查，不复制或改号。更早的结果没被删除，仍能按引用取回。

## 全局待办

`edit_hint(text)` 整体替换中心 agent 的全局待办；传空字符串清空。它持久保存，下一次模型子请求会在末尾 hint 看到最新内容；hint 自身不逐版追加，但 `edit_hint` 行动仍照常留在信息流里。改动不会发送 QQ 消息；要对人说话仍须调用 `say`。这里的模型可见 hint 与聊天结束后向 QQ 发状态消息的 `#hint` 命令不是一回事。只放尚待处理的事，做完及时更新；可复用经验放 Skill，聊天证据放可反查的信息流。

## 说话

`say(text, target, final_call)` 是你**唯一**的发言方式——直接写在回复正文里的内容不会发出去，那是你的自言自语，只留在这一轮里，人看得见但收不到。中心会话每次发言必须写明 `target="g<群号>"` 或 `target="u<私聊对端号>"`，不能凭最近读到谁来猜接收窗口。

- 返回值就是这条消息的 `message_id`。它可用于与聊天记录里的回声核对；要覆盖这句话，用回声读入后的信息流正式号，不用此 `message_id` 当 `cover_events` 的参数。
- `final_call` 默认 **true**：说完这句，当前工具循环结束；同一批里其它工具的结果这次看不到。仍有未读结果或固定追读任务时，中心 reader 会另开一轮继续。若要在当前循环接着干活，显式传 `final_call=false`。
- 发送**失败**或**未确认**时，不管 `final_call` 传了什么，都会照常再跑一轮，让你看到发生了什么。"未确认"的意思是请求被收下了但没给回号码，消息很可能已经发出去——别直接重发，先看下一轮的聊天记录。
- 一次 `say` 发一条消息。要发几条就调几次，最后一条传默认的 `final_call=true`。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。
'''

from __future__ import annotations

from contextvars import ContextVar
import io
from typing import Mapping

from mods.tools import current_binding


_offline_send_sink: ContextVar[object | None] = ContextVar("meta_offline_send_sink", default=None)


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
    """列出 last-good 模块、当前会话的激活状态、空闲回收时限、磁盘差异和最近失败；查可用模块名先调用它。"""
    return current_binding().list_text()


def reload_tools(names: list[str]) -> str:
    """从磁盘读取并应用指定模块的源码改动，逐个模块返回成功或完整 traceback；失败的模块继续沿用旧 last-good 版本。改完文件必须调用它，改动才会生效。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；源文件已删除的模块要显式列在这里才会被卸载
    """
    return _format_results(current_binding().reload(names))


def load_tools(names: list[str]) -> str:
    """激活 last-good 模块到当前 Chat；中心会话的名单全局持久，私有会话按窗口保存，不读取磁盘。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；名字来自 list_tools
    """
    return _format_results(current_binding().load(names))


def condense_ops(cids: list[str], conclusion: str) -> str:
    """把已经得出结论的几次工具调用移出上下文，只留下你在 conclusion 里写的结论。查完资料、确认完状态、修完一个文件之后调用它。原文不会被删除，之后可以用 recall_ops 按同样的 cid 取回。

    @param
    cids: 要收缩的行动引用，形如 ["20260923-4#1", "20260923-4#2"]
    conclusion: 这几次调用得出的结论，写成后面还用得上的一句话；它留在这次调用里，不会被再返回一遍
    """
    from mods import context, history, oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里，没有操作历史"
    if not cids:
        return "没有指定要收缩的调用"
    session = current_binding().session
    found, unknown = oplog.recall(window, cids)
    pending = oplog.pending_calls(window, unknown)
    visible_native = {(str(item.get("tool_call_id")), str(item.get("content")))
                      for item in session.messages if item.get("role") == "tool"}
    pending = [item for item in pending if (str(item.get("tool_call_id")), str(item["content"])) in visible_native]
    pending_ids = {item["cid"] for item in pending}
    native_ids = [item["tool_call_id"] for item in [*found, *pending] if item.get("tool_call_id")]
    sources = {cid.partition("#")[0] for cid in cids}
    if pending and not session.condense_native_calls(native_ids, sources=sources, apply=False):
        pending_ids.clear()
    unknown = sorted(set(unknown) - pending_ids)
    if unknown:
        return f"操作历史里找不到（已被 #ops clear 清掉，或从未存在）: {', '.join(unknown)}"
    # WHY: 要写两处，因为"上下文"在这一刻有两副身体：当前这轮的 Chat.messages 是活的、
    # 正在被工具循环追加，而 oplog 是明天重建时读的那份。只动 store 的话，这一轮不会变短
    # ——而长工具循环恰恰是最需要当场省下 token 的场景；只动 messages 的话，明天重建又
    # 把它们原样搬回来。两步没有先后要求：recall 看的是全量条目，不受标记影响。
    dropped = oplog.condense(window, cids)
    if not dropped:
        # 已经收缩过：当前上下文里那几条早就不在了，不必再去动活的那份。
        return "这几次调用已经收缩过了"
    from mods import chat

    if window == chat.AGENT_WINDOW:
        remove_ids = sources | {entry["id"] for entry in oplog.events(window, True)
                                if entry["kind"] == "result" and entry["source"] in sources}
        removed = sum(chat._stream_id(session, message) in remove_ids
                      for message in session.messages)
        session.messages[:] = [message for message in session.messages
                               if chat._stream_id(session, message) not in remove_ids]
        chat._prune_stream_ids(session)
    else:
        removed = chat._condense_projection(session.messages, window, sources)
    removed += session.condense_native_calls(native_ids, sources=sources)
    # 这里刻意不回显 conclusion：它已经在这次调用的 arguments 里，回显就是第二个副本。
    return f"已收缩 {dropped} 条操作，当前上下文移除 {removed} 条消息；需要时可用 recall_ops 取回原文"


def recall_ops(cids: list[str]) -> str:
    """按 cid 取回工具调用的原文，包括已收缩的结果；私有会话已见的原生返回在窗口 mail 尚未读到时也可取回，但不能提前读取别的会话的未读结果。

    @param
    cids: 要取回的行动引用，形如 ["20260923-4#1", "20260923-4#2"]
    """
    from mods import context, history, oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里，没有操作历史"
    if not cids:
        return "没有指定要取回的调用"
    found, unknown = oplog.recall(window, cids)
    if unknown:
        try:
            native_seen = current_binding().session.native_seen_calls
        except RuntimeError:
            native_seen = set()
        pending = oplog.pending_calls(window, set(unknown) & native_seen)
        found.extend(pending)
        unknown = sorted(set(unknown) - {item["cid"] for item in pending})
    lines = []
    for entry in found:
        members = oplog.coverage_members(window, entry["cid"])
        lines.append(
            f"[{entry['cid']}]{'(已收缩)' if entry.get('condensed') else ''} "
            f"{entry['name']}({entry['arguments']})\n{entry['content']}"
            + ("\n已冻结覆盖成员: " + ", ".join(members) if members is not None else "")
        )
    if unknown:
        lines.append(f"找不到（已被 #ops clear 清掉，或从未存在）: {', '.join(unknown)}")
    return "\n\n".join(lines) if lines else "没有取回任何内容"


def recall_events(ids: list[str], offset: int = 0) -> str:
    """按正式号反查已读取的输入、完整输出或结果；中心会话可跨窗口查旧号。

    @param
    ids: 要反查的信息流正式号，形如 ["20260923-4", "20260923-5"]
    offset: 结果过长时返回的 next_offset；继续时传回相同 ids
    """
    import json

    from mods import chat, context, history, oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里"
    if not isinstance(offset, int) or offset < 0:
        return "offset 必须是非负整数"
    found, missing = oplog.recall_events(window, ids)
    items = [json.dumps(item, ensure_ascii=False) for item in found]
    if missing:
        items.append("找不到: " + ", ".join(missing))
    if not items:
        return "没有指定信息流编号"
    rendered = "\n".join(items)
    if offset >= len(rendered):
        return "已经读到这些事件的末尾"
    segment = rendered[offset:]
    excerpt = chat.bounded_excerpt(segment, chat.MAIL_PAGE_TOKENS - 1000)
    if len(excerpt) < len(segment):
        return excerpt + f"\n结果未读完；相同 ids 继续 recall_events(offset={offset + len(excerpt)})"
    return excerpt


def event_span(anchor: str = "", before: int = 5, after: int = 5, start: str = "", end: str = "",
               kinds: str = "", source: str = "") -> str:
    """按唯一已读顺序列出某个正式号附近或两个正式号之间的小段；先选范围再筛选，不读取正文。

    @param
    anchor: 中心正式事件号；与 start/end 二选一
    before: 中心之前的已读事件数，默认 5；不按编号数字差或筛选命中数计算
    after: 中心之后的已读事件数，默认 5
    start: 区间起点正式号；与 end 一起使用，含端点
    end: 区间终点正式号；与 start 一起使用，含端点
    kinds: 可选，逗号分隔 input、output、result、notification；先定范围再筛选
    source: 可选来源窗口，g<群号> 或 u<私聊对端号>；只筛选，不决定排列
    """
    import json

    from mods import chat, oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里"
    try:
        source_window = chat.parse_target(source) if source else None
        selected = oplog.event_span(window, anchor=anchor, before=before, after=after,
                                    start=start, end=end, kinds=[kind.strip() for kind in kinds.split(",") if kind.strip()],
                                    source_window=source_window)
    except ValueError as error:
        return f"未找到范围：{error}"
    if not selected:
        return "范围内没有符合筛选条件的已读事件"
    return json.dumps([{"id": entry["id"], "kind": entry["kind"],
                        "window": entry["window"],
                        "source_window": entry.get("source_window")}
                       for entry in selected], ensure_ascii=False)


def event_links(ids: list[str]) -> str:
    """查看正式事件的文本引用、被谁引用、覆盖关系与结果来源；中心会话可跨窗口查旧号。

    @param
    ids: 要查看直接关系的正式事件号，形如 ["20260923-4"]；按返回的编号可继续逐层查询
    """
    import json

    from mods import context, history, oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里"
    if not ids:
        return "没有指定信息流编号"
    found = oplog.reference_links(window, ids)
    missing = [event_id for event_id in dict.fromkeys(ids) if event_id not in found]
    if missing:
        found["找不到"] = missing
    return json.dumps(found, ensure_ascii=False)


def cover_events(ids: list[str], conclusion: str) -> str:
    """将本次主窗口可见或此前已覆盖的事件归入这次行动的结论；成员不再自动载入，但仍可按原编号反查。关联的整批输出、返回和已确认 say 回声必须一同覆盖。

    @param
    ids: 当前主窗口可见或此前已覆盖的正式事件号，形如 ["20260923-4", "20260923-5"]；不要填写行动位置
    conclusion: 你从这些事件得出的结论，写出供后续子请求保留的摘要
    """
    from mods import chat, context, history, oplog

    window = _tool_window()
    session = current_binding().session
    if window is None or not session.reads_window_mail or not session.active_action:
        return "仅主窗口正在读取 mail 的会话能覆盖信息流；私有 .chat 和子代理不可覆盖"
    if not conclusion.strip() or not ids:
        return "请给出要覆盖的事件号及非空结论"
    try:
        visible = (chat._trusted_stream_ids(session) | getattr(session, "recalled_legacy_ids", set())
                   if window == chat.AGENT_WINDOW
                   else chat._visible_stream_ids(session.messages))
        members = oplog.cover(window, session.active_action, ids, visible)
    except ValueError as error:
        return f"未覆盖：{error}"
    if window == chat.AGENT_WINDOW:
        chat._cover_agent_projection(session, members)
    else:
        chat._cover_projection(session.messages, members)
    return f"已覆盖 {len(members)} 条已读事件；原编号仍可用 recall_events 反查"


def attach_image(uri: str, note: str = "") -> str:
    """把一张图片交给**你自己**看——截图、图表、验证码、扫描件这类"只有看了才知道"的东西用它，不要让你自己靠猜。图随这条工具结果一起送到，下一个子请求里就看得见（`✅`/`⚠️` 是结果说明）。

    @param
    uri: 图片地址，`file://`、`http(s)://` 或已经是 `data:` 都行；浏览器截图给出的 `file://` 直接传进来即可
    note: 附在图前面的一句话，例如"读出图中的字"
    """
    from ._vision import attach

    return attach(uri, note)


_SAY_TIMEOUT = 30.0


def say(text: str, final_call: bool = True, target: str = "") -> str:
    """发一句话并返回 message_id；中心会话必须显式指定目标，默认说完这一轮就结束。

    @param
    text: 要说的话。CQ 码原样写，at、reply、图片都照常生效
    final_call: 这次发言是不是本轮最后一个动作。默认 true；要接着干活就显式传 false
    target: 中心会话必填 g<群号> 或 u<私聊对端号>；私有 .chat 可留空用当前窗口

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
    发，往返因此闭合。在这里 `cq.escape` 会让它写的 at 和图片变成字面文本。这与
    `op._receipt` 那条相反的规矩不冲突——那里转义的是**命令原文**，是记录，不是发言。

    WHY: 它住在 meta 而不是自己一个模块，因为发言是模型**永远**该有的能力，而
    `tools._BASE_MODULE_NAME` 是单数、基础模块只有一个。meta 早就不只是"管工具模块"了
    （`condense_ops`/`recall_ops` 管的是上下文），它实际是那组不可卸载的基础能力，`say`
    属于这一组。改成支持多个基础模块也行，代价是那条"基础模块的导出不加模块名前缀"的
    规则会多出命名冲突的可能，眼下不值得。

    WHY?: 子代理（`tools/agents.py`）因此也拿得到 `say`，于是它能直接往窗口里说话——这是
    工具化带来的**新**能力，以前子代理的正文只回给主模型。没有给它加门控，因为"子代理该
    不该能说话"还没有人拍过；先记在这里，真出现不想要的发言再决定是挡掉还是保留。
    """
    from mods import message
    from mods import chat, context
    from mods.tools import current_binding

    body = str(text)
    sink = _offline_send_sink.get()
    if chat._offline_scope.get() is not None and sink is None:
        raise RuntimeError("offline send sink is missing")
    if not body.strip():
        if sink is not None:
            raise ValueError("offline say text is empty")
        return "text 为空，什么都没发。要说话就给出正文"
    destination = {}
    if context.agent_mode():
        try:
            window = chat.parse_target(target)
        except ValueError as error:
            if sink is not None:
                raise
            return str(error)
        destination = {"group_id" if window[0] == "group" else "user_id": window[1]}
        current_binding().session.associated_windows.add(window)
    elif target:
        try:
            window = chat.parse_target(target)
        except ValueError as error:
            if sink is not None:
                raise
            return str(error)
        destination = {"group_id" if window[0] == "group" else "user_id": window[1]}
    unconfirmed = (
        "先在下一轮的聊天记录里看它在不在，再决定要不要重发——不要直接重发。"
    )
    try:
        message_id = (sink(body, window) if sink is not None
                      else message.sendmsg(body, **destination).result(timeout=_SAY_TIMEOUT))
    except TimeoutError:
        if sink is not None:
            raise
        return f"未确认：等了 {_SAY_TIMEOUT:.0f} 秒还没有结果，这句话可能正在发、也可能已经发出去。" + unconfirmed
    except Exception as error:
        if sink is not None:
            raise
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去。"
    if message_id is None:
        return "未确认：对端收下了请求，却没有回一个 message_id，所以这句话可能已经发出去了。" + unconfirmed
    if final_call:
        current_binding().session.turn_done = lambda: True
    return str(message_id)


def pull_mail(target: str) -> str:
    """安排在下一次模型请求前正式读取指定窗口的队首一小页；只减少该窗口的 FIFO 未读。

    @param
    target: g<群号> 或 u<私聊对端号>，必须明确指定
    """
    from mods import chat, context

    session = current_binding().session
    if not context.agent_mode() or not session.reads_window_mail:
        return "只有中心 reader 可以正式拉取 mail"
    try:
        window = chat.parse_target(target)
    except ValueError as error:
        return str(error)
    if len(session.requested_pulls) >= 4:
        return "本轮已安排四页，请先阅读结果再继续"
    from mods import oplog

    through = oplog.work_targets(chat.AGENT_WINDOW).get(window)
    if through is None:
        through = oplog.latest_pending_arrival(window)
    sources = chat._recovery_sources(window)
    if any(source["state"] == "fetching" for source in sources):
        return f"{target} 的离线补回仍在进行，首次正式拉取须等它收束"
    if through is None and not any(source["remaining"] for source in sources):
        return f"{target} 当前没有待正式读取的 mail"
    session.associated_windows.add(window)
    session.requested_pulls.append((window, through))
    return f"已安排下一次请求前读取 {target} 的队首最多 {chat.MAIL_PAGE_EVENTS} 条；实际页随后进入信息流"


def list_unread(offset: int = 0, limit: int = 10) -> str:
    """分页列出各窗口未读数量及唤醒来源；不读取正文，也不减少未读。

    @param
    offset: 从第几个未读窗口开始，默认 0；列表可能随新消息变化
    limit: 最多列出多少窗口，1 到 10
    """
    from mods import chat, context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以列出未读窗口"
    if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 10:
        return "offset 必须非负且 limit 必须是 1..10"
    details = chat.unread_details()
    lines = []
    for detail in details[offset:offset + limit]:
        line = chat._unread_detail_text(detail)
        if chat.count_tokens("\n".join([*lines, line])) > chat.MAIL_PAGE_TOKENS - 1000:
            if not lines:
                lines.append(chat.bounded_excerpt(line, chat.MAIL_PAGE_TOKENS - 1100)
                             + "（来源元数据过长，已截断）")
            break
        lines.append(line)
    next_offset = offset + len(lines)
    return ("\n".join(lines) if lines else "没有更多未读窗口") + (
        f"\nnext_offset={next_offset if next_offset < len(details) else '(end)'}; "
        f"total={len(details)}; 未读未减少；列表变化时从 0 重新查")


def source_status(offset: int = 0, limit: int = 10) -> str:
    """列出有名字的持久信源、未读数量和缺口，不读取正文。

    @param
    offset: 从第几个信源开始，默认 0
    limit: 最多列出多少信源，1 到 10
    """
    from mods import context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以查看信源"
    if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 10:
        return "offset 必须非负且 limit 必须是 1..10"
    sources = oplog.sources()
    lines = []
    for source in sources[offset:offset + limit]:
        lines.append(f"{source['key']} {source['name']} 窗口={tuple(source['window'])} "
                     f"状态={source['state']} 未读={source['remaining']} "
                     f"提及={source['mention_count'] - source['read_mention_count']} "
                     f"已拉取={source['pulled']}"
                     + (f" 缺口={source['gap']}" if source['gap'] else ""))
    next_offset = offset + len(lines)
    return ("\n".join(lines) if lines else "没有更多信源") + (
        f"\nnext_offset={next_offset if next_offset < len(sources) else '(end)'}; 未读未减少")


def fetch_source(target: str) -> str:
    """从 NapCat 向前补一个窗口的历史；未读信源仍可扩展，已读则另开 FIFO。

    @param
    target: g<群号> 或 u<私聊对端号>
    """
    from mods import chat, context

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以拉取信源"
    try:
        window = chat.parse_target(target)
    except ValueError as error:
        return str(error)
    current_binding().session.associated_windows.add(window)
    source = chat.fetch_remote_source(window)
    return (f"信源 {source['key']}（{source['name']}）状态={source['state']}；"
            "后台逐页追到锚点或上游尽头，完成前不能正式 pull；用 source_status 查看进度")


def pull_source(source: str) -> str:
    """安排下次请求前从指定独立信源 FIFO 正式读取一小页。

    @param
    source: source_status 返回的信源 key
    """
    from mods import chat, context, oplog

    session = current_binding().session
    if not context.agent_mode() or not session.reads_window_mail:
        return "只有中心 reader 可以正式拉取信源"
    state = oplog.resolve_source(source)
    if state is None or state["key"] != source:
        return "找不到该信源 key"
    if state["source_type"] == "napcat_boot":
        return "启动补回属于原窗口 FIFO，请用 pull_mail(target)"
    if state["state"] == "fetching":
        return "该信源还在拉取，队首尚未固定"
    if not state["remaining"]:
        return "该信源已经读完"
    if len(session.requested_pulls) >= 4:
        return "本轮已安排四页，请先阅读结果再继续"
    session.associated_windows.add(tuple(state["window"]))
    session.requested_pulls.append((("source", source), None))
    return f"已安排下一次请求前正式读取信源 {source} 的队首小页"


def peek_source(source: str, limit: int = 8) -> str:
    """预览独立信源 FIFO 当前队首，不消耗未读；长正文请按返回的 origin 调 peek。

    @param
    source: source_status 返回的信源 key
    limit: 最多预览多少条，1 到 8
    """
    from mods import _source_pages, chat, chatlog, context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以预览信源"
    if not isinstance(limit, int) or not 1 <= limit <= 8:
        return "limit 必须是 1..8"
    state = oplog.resolve_source(source)
    if state is None or state["key"] != source:
        return "找不到该信源 key"
    if state["state"] == "fetching":
        return "信源仍在拉取；可先用 peek_napcat 预览远端"
    current_binding().session.associated_windows.add(tuple(state["window"]))
    page_number = state["read_page"]
    offset = state["read_offset"]
    lines = []
    while page_number is not None and page_number >= 0 and len(lines) < limit:
        members = _source_pages.read_page(oplog.source_page_root(), source, page_number)
        for member in members[offset:]:
            record = chatlog.read_origin(*state["window"], member["origin"])
            if record is None:
                return "信源档案位置丢失；未读未减少"
            projection = chat._model_event(record, state["window"][0] == "group")
            if projection is not None:
                body = chat.bounded_excerpt(str(projection["content"]), 900)
                lines.append(f"origin={member['origin']} message_id={member['message_id']}: {body}")
            if len(lines) >= limit:
                break
        page_number -= 1
        offset = 0
    return ("\n".join(lines) if lines else "没有可预览的未读内容") + "\n未读未减少"


def peek(target: str, before: str = "", limit: int = 8, origin: str = "", offset: int = 0) -> str:
    """预览指定窗口最近或旧档的一小页，不减少未读；用 next_before 继续向前翻。

    @param
    target: g<群号> 或 u<私聊对端号>，必须明确指定
    before: 上页返回的 next_before 档案位置；留空预览最新
    limit: 最多查看的记录数，1 到 8
    origin: 单条过长时返回的 continue_origin，续读这一条时填入；通常留空
    offset: 与 origin 一起使用，继续读取同一记录的字符偏移；通常为 0
    """
    import json

    from mods import chat, chatlog, context

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以预览聊天档案"
    try:
        window = chat.parse_target(target)
    except ValueError as error:
        return str(error)
    current_binding().session.associated_windows.add(window)
    if (not isinstance(limit, int) or not 1 <= limit <= 8
            or not isinstance(offset, int) or offset < 0 or offset and not origin):
        return "limit 必须是 1..8；offset 必须配合 continue_origin 且为非负整数"
    if origin:
        record = chatlog.read_origin(*window, origin)
        if record is None:
            return "找不到该窗口的精确档案位置；未读未减少"
        records = [record]
    else:
        records = chatlog.read_range(*window, limit=limit, before=before or None)
    if not records:
        return "没有更早的档案记录；未读未减少"
    lines = []
    used = 0
    cursor = before
    for record in records:
        origin = record.get("_log_origin")
        if not origin:
            return "档案记录缺少稳定位置，拒绝无游标预览"
        projection = chat._model_event(record, window[0] == "group")
        if projection is None:
            cursor = origin
            continue
        rendered = json.dumps(projection["content"], ensure_ascii=False)
        segment = rendered[offset:] if not lines else rendered
        if not segment and origin:
            return f"该记录已读到末尾；next_before={origin}; 未读未减少"
        excerpt = chat.bounded_excerpt(segment, max(1, chat.MAIL_PAGE_TOKENS - used - 1000))
        if len(excerpt) < len(segment):
            if lines:
                break
            return (f"{target} origin={origin} 预览片段：{excerpt}\n"
                    f"continue_origin={origin}; next_offset={offset + len(excerpt)}; 未读未减少")
        lines.append(f"{target} origin={origin}: {segment}")
        used += chat.count_tokens(segment)
        cursor = origin
    return ("\n".join(reversed(lines)) if lines else "这一页没有可见聊天记录") + (
        f"\nnext_before={cursor or '(end)'}; next_offset=0; 未读未减少")


def peek_napcat(target: str, before: str = "", limit: int = 8, seq: str = "", offset: int = 0) -> str:
    """只读预览 NapCat 当前可提供的一页群聊或私聊历史；不消未读、不补写本地 chatlog。

    @param
    target: g<群号> 或 u<私聊对端号>
    before: 上页返回的 next_before 消息序号；留空取远端最新页
    limit: 最多查看的记录数，1 到 8
    seq: 单条过长时返回的 continue_seq；通常留空
    offset: 与 seq 一起使用的字符偏移；通常为 0
    """
    import json

    from mods import _napcat_history, chat, connect, context

    session = current_binding().session
    if not context.agent_mode() or not session.reads_window_mail:
        return "只有中心 reader 可以预览 NapCat 历史"
    try:
        window = chat.parse_target(target)
    except ValueError as error:
        return str(error)
    if (not isinstance(limit, int) or not 1 <= limit <= 8 or not isinstance(offset, int)
            or offset < 0 or offset and not seq or any(value and not str(value).isdecimal()
                                                     for value in (before, seq))):
        return "limit 必须是 1..8；before/seq 必须是消息序号；offset 必须配合 seq 且非负"
    session.associated_windows.add(window)
    action = "get_group_msg_history" if window[0] == "group" else "get_friend_msg_history"
    params = {"group_id" if window[0] == "group" else "user_id": window[1],
              "count": 1 if seq else limit + (1 if before else 0), "disable_get_url": True,
              "parse_mult_msg": False}
    if seq or before:
        # WHY: 本机 NapCat 4.18.28 的锚点参数是 message_seq，不是 message_id；返回
        # 包含锚点自身。向旧页翻时须显式 reverse_order=True 并在此剔除锚点。
        params.update(message_seq=seq or before, reverse_order=not bool(seq))
    try:
        response = connect.call_api(action, **params)
    except Exception as error:
        return f"NapCat 历史查询失败（{type(error).__name__}）；未读未减少"
    if response.get("retcode") != 0:
        return f"NapCat 历史查询失败（retcode={response.get('retcode')}）；未读未减少"
    data = response.get("data")
    rows = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return "NapCat 历史响应缺少 messages 列表；未读未减少"
    try:
        sequences = [_napcat_history._number(row.get("message_seq"), "message_seq")
                     for row in rows if isinstance(row, dict)]
        if len(sequences) != len(rows) or len(sequences) != len(set(sequences)):
            raise ValueError("duplicate or malformed message_seq")
    except ValueError:
        return "NapCat 历史序号重复或畸形；未读未减少"
    rows.sort(key=lambda row: int(row["message_seq"]))
    if before and not seq:
        rows = [row for row in rows if int(row["message_seq"]) != int(before)]
    if seq:
        rows = [row for row in rows if int(row["message_seq"]) == int(seq)]
    else:
        rows = rows[-limit:]
    if not rows:
        return "没有更早的 NapCat 历史记录；未读未减少"
    lines = []
    used = 0
    cursor = before
    for row in reversed(rows):
        if not isinstance(row, dict) or row.get("message_seq") is None or not isinstance(row.get("raw_message"), str):
            return "NapCat 历史记录缺少可续读的序号或原文；未读未减少"
        remote_seq = str(row["message_seq"])
        event = {**row, "message": row["raw_message"], "_history_source": "napcat",
                 "_history_seq": remote_seq}
        if window[0] == "private":
            event["target_id"] = window[1]
        projection = chat._model_event(event, window[0] == "group")
        if projection is None:
            cursor = remote_seq
            continue
        rendered = json.dumps(projection["content"], ensure_ascii=False)
        segment = rendered[offset:] if seq else rendered
        if not segment:
            return f"该远端记录已读到末尾；next_before={remote_seq}; 未读未减少"
        excerpt = chat.bounded_excerpt(segment, max(1, chat.MAIL_PAGE_TOKENS - used - 1000))
        if len(excerpt) < len(segment):
            if lines:
                break
            return (f"{target} remote_seq={remote_seq} 预览片段：{excerpt}\n"
                    f"continue_seq={remote_seq}; next_offset={offset + len(excerpt)}; 未读未减少")
        lines.append(f"{target} remote_seq={remote_seq}: {segment}")
        used += chat.count_tokens(segment)
        cursor = remote_seq
    return ("\n".join(reversed(lines)) if lines else "这一页没有可见远端聊天记录") + (
        f"\nnext_before={cursor or '(end)'}; next_offset=0; 未读未减少")


def edit_hint(text: str) -> str:
    """整体替换中心 agent 的全局待办 hint；传空字符串清空，不会向 QQ 发送消息。

    @param
    text: 更新后的完整待办文本；请保留仍未完成的事项，空字符串表示清空
    """
    from mods import chat, context, history

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里，无法编辑待办 hint"
    chat.set_agent_hint(window, text)
    return "已更新待办 hint；下次模型请求会看到新内容" if text.strip() else "已清空待办 hint"


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
    "cover_events",
    "say",
    "peek",
    "peek_napcat",
    "peek_source",
    "source_status",
    "fetch_source",
    "pull_source",
    "list_unread",
    "pull_mail",
    "edit_hint",
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
    "condense_ops",
    "recall_ops",
    "recall_events",
    "event_span",
    "event_links",
    "attach_image",
]
