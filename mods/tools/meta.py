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
3. 本窗口的激活态：经 `load_tools` 加入的模块内容和函数。它属于**窗口**，不是单轮：`load_tools` 一成功就记进本窗口，下一轮开局自动装回来，跨重启也还在。

`reload_tools` 从磁盘应用源码；`load_tools` 只激活 last-good，不能混用。没有自动 watcher，也不要等待修改自行生效。

激活会自己到期，你不需要收拾它：超过一段时间没有被装入或调用过的模块，下一轮开局就不再装回，并给你一条"已停用（空闲收回）"的通告，里面写着被收走的是哪个模块、干什么用的（确切时限见 `list_tools` 的"空闲回收"一节）。装入本身算一次"用过"，所以只靠读正文的模块（`.md` 技能，以及只提供说明的 `.py`）按装入时刻计时——**阅读不留痕迹**，读得再勤也不会续期。还要用就照通告说的重新 `load_tools`，那是一次往返，不必省。

因为激活属于窗口，同一件事不需要每轮重复 `load_tools`，也不会因为一轮聊完就消失。会"自己消失"的情况有三种，都会以一条系统通告告诉你，形如 `- 已停用（原因）：模块名`；通告只报状态和原因，三个原因的意思在这里：

- **空闲收回**：见上一段，窗口里的记录一并清掉，还要用就重新 `load_tools`。
- **Bot 未获权限**：`op` 这类模块要求 Bot 自身拥有 op 权限。窗口的激活记录**仍然留着**，以后修改权限配置并重启即可装回。
- **已不存在**：模块的源码已经没了，窗口里的记录也一并清掉。

三种都只是"这一轮它不在了"——不要照着上一轮的印象直接调它的函数，本轮快照里没有那个名字，你会白跑一次。要确认还有什么、叫什么，看会话开头那份模块目录：它无条件列出每个模块的名字和一句话描述，与激活与否无关，被收掉的模块照样在里面。

## 查询

先调用 `list_tools()`。它列出 last-good 模块及一句话描述、本窗口已激活模块、空闲回收的确切规则与时限、磁盘相对 last-good 的新增/修改/删除，以及最近的加载失败 traceback。需要阅读源码时，再用文件能力或 `exec_code` 精确读取 `mods/tools/<name>.py` 或 `.md`。

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

## 修改

先精确读取现有源文件，只修改目标模块，再调用 `reload_tools(["foo"])`。成功后 last-good 才替换；如果模块已在当前 Chat 激活，内容和函数会为下一次模型子请求更新。失败时根据返回的完整 traceback 修复并再次 reload，旧 last-good 和旧活动版本继续服务。仅调用 `load_tools` 不会读取刚改的磁盘文件。

下划线 helper 不是独立模块，它的变化不会单独出现在 `list_tools` 中。修改 helper 后要显式 reload 所有 import 它的模块。

`reload_tools` 换得动的只有 `mods/tools/` 下的那一个文件（外加它的下划线 helper）。它 `import` 的 `mods.*`——包括这套机制自己的 `mods/tools/__init__.py`——是进程启动时的那份，改了要重启才生效，典型症状是"说明书和工具清单里有、调用时 AttributeError"。这条边界的完整说明在仓库的 `docs/runtime.md`（"热更换得动什么"一节），需要重启时用 op 工具集的 `send_command` 注入 `.reboot`，重启后会在这个窗口接着开一轮，手上的事不会断。

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数，本窗口的激活记录一并清除。只删文件但不 reload 时，旧 last-good 仍然有效。`meta.py` 是恢复入口，不能删除。删除 helper 前要先检查并 reload 所有受影响模块。

## 操作历史与结论收缩

每次完整模型输出（思考、正文、多个行动）只有一个 `YYYYMMDD-N` 号；该输出内部行动用 `YYYYMMDD-N#位置` 指名。消息和工具结果也在被读到时进入同一信息流。结果可能在下一次模型请求才读到，未读不占正式号。

这些记录跨轮按读到的先后重建，未收缩的结果保留原文。压缩靠你自己：一组调用得到结论后，可调用 `condense_ops(["20260923-4#1", "20260923-4#2"], "结论")` 将同一输出内的行动一起移出上下文。

结论写在 `conclusion` 参数里就够了，工具不会把它再返回一遍：这次调用本身留在上下文里，参数里的结论就是它的记录。

已读消息和输出也能总结：主窗口调用 `cover_events(["20260923-4", "20260923-5"], "结论")`，参数用的是上文方括号里的正式事件号，不是 QQ `message_id` 或行动位置。覆盖使成员从本轮和后续自动上下文中消失；原号可用 `recall_events` 反查而不解除覆盖，反查总结会显示实际冻结的所有成员，不只是你传入的号。整个输出与其返回批次不可拆，已确认的 `say`／回声也必须成组；未确认的自发回声不能先单独覆盖，未读成员不会被补进覆盖。摘要与聊天消息、输出、返回一样占历史事件数和 token 预算；私有 `.chat` 和子代理不能替主窗口写覆盖，只能继续使用 `condense_ops`。

## 说话

`say(text, final_call)` 是你**唯一**的发言方式——直接写在回复正文里的内容不会发出去，那是你的自言自语，只留在这一轮里，人看得见但收不到。

- 返回值就是这条消息的 `message_id`。它可用于与聊天记录里的回声核对；要覆盖这句话，用回声读入后的信息流正式号，不用此 `message_id` 当 `cover_events` 的参数。
- `final_call` 默认 **true**：说完这句，这一轮就结束了，同一批里其它工具的结果你这一轮也看不到。说完还要接着干活，就显式传 `final_call=false`。
- 发送**失败**或**未确认**时，不管 `final_call` 传了什么，都会照常再跑一轮，让你看到发生了什么。"未确认"的意思是请求被收下了但没给回号码，消息很可能已经发出去——别直接重发，先看下一轮的聊天记录。
- 一次 `say` 发一条消息。要发几条就调几次，最后一条传默认的 `final_call=true`。

收缩是**可逆**的：结果离开后续模型视图，原文仍可用 `recall_ops(["20260923-4#1"])` 取回。`#ops clear` 清空操作视图，但不重用号码或物理删除事件。

普通输入、完整输出及整批工具返回都可用 `recall_events(["20260923-4"])` 按正式号反查；这不自动把整个载荷放回后续模型上下文。

同一输出里的多个行动必须一起点名收缩；尚未返回的行动不能收缩。

操作记录跟着聊天窗口走**载入**：已读可见事件和 token 共同决定最新历史后缀，不靠聊天消息作为锚点。更早的结果没被删除，仍能按引用取回。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。
'''

from __future__ import annotations

import io
from typing import Mapping

from mods.tools import current_binding


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
    """列出全部 last-good 工具模块及其一句话描述、本窗口已激活的模块、空闲回收的规则与确切时限、磁盘相对 last-good 的新增/修改/删除，以及最近的加载失败 traceback。想知道有哪些模块名可用时先调用它。"""
    return current_binding().list_text()


def reload_tools(names: list[str]) -> str:
    """从磁盘读取并应用指定模块的源码改动，逐个模块返回成功或完整 traceback；失败的模块继续沿用旧 last-good 版本。改完文件必须调用它，改动才会生效。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；源文件已删除的模块要显式列在这里才会被卸载
    """
    return _format_results(current_binding().reload(names))


def load_tools(names: list[str]) -> str:
    """把已有的 last-good 模块激活到当前聊天，让它的说明和整组函数可用；不读磁盘，因此不会应用刚改的源码。新激活的工具从下一次模型请求起才可调用；激活属于本窗口，下一轮开局会自动装回，长期没有被装入或调用过则自动收回（规则与时限见 list_tools）。

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

    window = history.window(context.current() or {})
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

    window = history.window(context.current() or {})
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


def recall_events(ids: list[str]) -> str:
    """按信息流正式号反查本窗口已读取的输入、完整输出或结果批次；旧档只在主模型读到时取得正式号。

    @param
    ids: 要反查的信息流正式号，形如 ["20260923-4", "20260923-5"]
    """
    import json

    from mods import context, history, oplog

    window = history.window(context.current() or {})
    if window is None:
        return "当前不在聊天窗口里"
    found, missing = oplog.recall_events(window, ids)
    items = [json.dumps(item, ensure_ascii=False) for item in found]
    if missing:
        items.append("找不到: " + ", ".join(missing))
    return "\n".join(items) if items else "没有指定信息流编号"


def cover_events(ids: list[str], conclusion: str) -> str:
    """将本次主窗口已读的事件归入这次行动的结论；成员不再自动载入，但仍可按原编号反查。关联的整批输出、返回和已确认 say 回声必须一同覆盖。

    @param
    ids: 当前主窗口可见信息流的正式事件号，形如 ["20260923-4", "20260923-5"]；不要填写行动位置
    conclusion: 你从这些事件得出的结论，写出供后续子请求保留的摘要
    """
    from mods import chat, context, history, oplog

    window = history.window(context.current() or {})
    session = current_binding().session
    if window is None or not session.reads_window_mail or not session.active_action:
        return "仅主窗口正在读取 mail 的会话能覆盖信息流；私有 .chat 和子代理不可覆盖"
    if not conclusion.strip() or not ids:
        return "请给出要覆盖的事件号及非空结论"
    try:
        members = oplog.cover(window, session.active_action, ids,
                              chat._visible_stream_ids(session.messages))
    except ValueError as error:
        return f"未覆盖：{error}"
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


def say(text: str, final_call: bool = True) -> str:
    """把一句话发进当前窗口，返回它的 message_id；默认说完这一轮就结束。

    @param
    text: 要说的话。CQ 码原样写，at、reply、图片都照常生效
    final_call: 这次发言是不是本轮最后一个动作。默认 true；要接着干活就显式传 false

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
    from mods.tools import current_binding

    body = str(text)
    if not body.strip():
        return "text 为空，什么都没发。要说话就给出正文"
    unconfirmed = (
        "先在下一轮的聊天记录里看它在不在，再决定要不要重发——不要直接重发。"
    )
    try:
        message_id = message.sendmsg(body).result(timeout=_SAY_TIMEOUT)
    except TimeoutError:
        return f"未确认：等了 {_SAY_TIMEOUT:.0f} 秒还没有结果，这句话可能正在发、也可能已经发出去。" + unconfirmed
    except Exception as error:
        return f"发送失败（{type(error).__name__}）：{error}。这句话没有发出去。"
    if message_id is None:
        return "未确认：对端收下了请求，却没有回一个 message_id，所以这句话可能已经发出去了。" + unconfirmed
    if final_call:
        current_binding().session.turn_done = lambda: True
    return str(message_id)


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
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
    "condense_ops",
    "recall_ops",
    "recall_events",
    "attach_image",
]
