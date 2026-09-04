'''指导模型增删查改统一工具与 Skill 模块，并说明 last-good、显式应用和当前会话激活原理。

## 直接执行 Python

`exec_code(expr, code)` 用的是 `.py` 命令那份共享环境：先 `exec(code)`，再 `eval(expr)`，返回 `repr(结果)`。`code` 里的 `print` 输出会被捕获后一起回传，不会发进聊天。环境跨调用持久，上一次定义的变量和函数下一次仍然在。

只有当前发送者是管理员时可用，否则返回"权限不足"。

环境里预置了 Bot 的全部动态导出名：`send`/`sendmsg` 发消息，`getname`/`setname`/`getstorage`/`getgroupstorage`/`memberlist` 读写身份与存储，`data` 是持久字典，`prompts` 是提示词集合，`run_action` 触发 link 动作，还有 `os`、`json`、`re`、`time`、`random`、`math`、`datetime` 等标准库；`ctx` 是模块名到模块对象的字典，各个 `mods` 模块也能直接按名字使用。不确定有什么就先自查，例如 `exec_code("sorted(k for k in globals() if not k.startswith('_'))")`。

它与 Bot 共享同一个宿主信任域，不是沙箱：会真实发消息、读写磁盘、改动运行中的状态。用之前先确认没有现成工具能做这件事，并避免 `input(...)`（会阻塞等待聊天回复）和长时间运行的代码。异常以 traceback 回传，可据此修正重试。需要反复使用的能力应当沉淀成下面的工具模块，而不是每次都 `exec_code`。

## 工具模块的三层状态

所有模块源都放在 `mods/tools/` 顶层。`foo.py` 是带函数的工具模块，`foo.md` 是没有函数的 Skill；二者使用同一套生命周期，不能同 stem 共存。

1. 磁盘源码：刚编辑的文件，还不一定生效。
2. 进程级 last-good：最后一次成功初始化或 `reload_tools` 的完整模块版本。
3. 当前 Chat 激活态：经 `load_tools` 加入当前任务的模块内容和函数。

`reload_tools` 从磁盘应用源码；`load_tools` 只激活 last-good，不能混用。没有自动 watcher，也不要等待修改自行生效。

## 查询

先调用 `list_tools()`。它列出 last-good 模块及一句话描述、当前 Chat 已激活模块、磁盘相对 last-good 的新增/修改/删除，以及最近的加载失败 traceback。需要阅读源码时，再用文件能力或 `exec_code` 精确读取 `mods/tools/<name>.py` 或 `.md`。

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

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数。只删文件但不 reload 时，旧 last-good 仍然有效。`meta.py` 是恢复入口，不能删除。删除 helper 前要先检查并 reload 所有受影响模块。

## 操作历史与结论收缩

每次工具调用的结果开头都有一个 `[opN]`，那是这次调用的上下文 id（cid）。这些调用会记进本窗口的操作历史，**跨轮保留**：下一轮开始时你能看到自己上一轮改过什么、加载过什么，而聊天记录里并没有这些。

这些记录会以**原样的调用记录**重建，不是摘要，所以上下文会随调用增长。压缩靠你自己：一组调用往往是为某个目的服务的，结论一旦得出中间过程就只剩噪音——查完资料、确认完状态、修完一个文件之后，调用 `condense_ops(["op3", "op4"], "结论")` 把它们移除。

结论写在 `conclusion` 参数里就够了，工具不会把它再返回一遍：这次调用本身留在上下文里，参数里的结论就是它的记录。

收缩是**可逆**的：被收缩的调用只是离开上下文，原文按保留期继续留着。你那次 `condense_ops` 调用会跟着重建回到后面每一轮的上下文里，`cids` 参数里写的就是被收掉的那几个 cid——什么时候觉得当初的结论不够用、或者要核对当初到底看到了什么，用 `recall_ops(["op3"])` 按 cid 把完整原文取回来。所以收缩不必犹豫，它不销毁任何东西。

三条规则：同一条 assistant 消息里并发的多个调用必须一起点名收缩，只点其中一个会被拒绝；正在执行、结果还没回来的那一轮不能收缩；后来的收缩可以把更早那次 `condense_ops` 也收掉，那次的结论随之从上下文消失——需要保留就在新结论里带上。

上下文能装下多少由聊天历史和工具记录**共用的 token 预算**决定，装不下的、以及早于最老那条聊天消息的，都不会自动载入。这些不会有清单列给你——记录最长保留 7 天，量太大，列出来本身就是浪费。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。
'''

from __future__ import annotations

import io
from typing import Mapping

from mods.tools import current_binding


def exec_code(expr: str, code: str = "") -> str:
    """在 Bot 进程的共享 Python 环境中先执行 code、再求值 expr，返回 repr 结果和被捕获的 print 输出；需要管理员权限。

    @param
    expr: 在 code 之后求值并返回的单个表达式；只想执行 code、不关心返回值时传字符串 None
    code: 先执行的 Python 语句，可以多行；不需要时传空字符串
    """
    from mods import context, op, py

    if not op.require_op(context.current()):
        return "权限不足"
    buffer = io.StringIO()
    missing = object()
    original = py.loc.get("print", missing)
    py.loc["print"] = lambda *values, sep=" ", end="\n": buffer.write(
        sep.join(map(str, values)) + end
    )
    try:
        exec(code, py.loc)
        result = repr(eval(expr, py.loc))
    finally:
        if original is missing:
            py.loc.pop("print", None)
        else:
            py.loc["print"] = original
    printed = buffer.getvalue().rstrip()
    return f"[print输出]\n{printed}\n[结果] {result}" if printed else result


def list_tools() -> str:
    """列出全部 last-good 工具模块及其一句话描述、当前聊天已激活的模块、磁盘相对 last-good 的新增/修改/删除，以及最近的加载失败 traceback。想知道有哪些模块名可用时先调用它。"""
    return current_binding().list_text()


def reload_tools(names: list[str]) -> str:
    """从磁盘读取并应用指定模块的源码改动，逐个模块返回成功或完整 traceback；失败的模块继续沿用旧 last-good 版本。改完文件必须调用它，改动才会生效。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；源文件已删除的模块要显式列在这里才会被卸载
    """
    return _format_results(current_binding().reload(names))


def load_tools(names: list[str]) -> str:
    """把已有的 last-good 模块激活到当前聊天，让它的说明和整组函数可用；不读磁盘，因此不会应用刚改的源码。新激活的工具从下一次模型请求起才可调用。

    @param
    names: 模块名列表，不带 .py/.md 后缀，也不带 模块名__ 前缀；名字来自 list_tools
    """
    return _format_results(current_binding().load(names))


def condense_ops(cids: list[str], conclusion: str) -> str:
    """把已经得出结论的几次工具调用移出上下文，只留下你在 conclusion 里写的结论。查完资料、确认完状态、修完一个文件之后调用它。原文不会被删除，之后可以用 recall_ops 按同样的 cid 取回。

    @param
    cids: 要收缩的调用 id 列表，形如 ["op3", "op4"]；每条工具结果开头的 [opN] 就是它
    conclusion: 这几次调用得出的结论，写成后面还用得上的一句话；它留在这次调用里，不会被再返回一遍
    """
    from mods import context, history, oplog

    window = history.window(context.current() or {})
    if window is None:
        return "当前不在聊天窗口里，没有操作历史"
    if not cids:
        return "没有指定要收缩的调用"
    tool_call_ids, unknown = oplog.call_ids(window, cids)
    if unknown:
        return f"没有可收缩的（已经收缩过、或已过保留期）: {', '.join(unknown)}"
    removed = current_binding().session.condense_calls(tool_call_ids)
    dropped = oplog.condense(window, cids)
    # 这里刻意不回显 conclusion：它已经在这次调用的 arguments 里，回显就是第二个副本。
    return f"已收缩 {dropped} 条操作，当前上下文移除 {removed} 条消息；需要时可用 recall_ops 取回原文"


def recall_ops(cids: list[str]) -> str:
    """按 cid 取回工具调用的完整原文，包括已经被 condense_ops 收缩掉的、以及因为上下文预算或时间太早而没有载入的那些。需要核对某次收缩当初到底看到了什么时用它，cid 就写在那次 condense_ops 调用的 cids 参数里。

    @param
    cids: 要取回的调用 id 列表，形如 ["op3", "op4"]
    """
    from mods import context, history, oplog

    window = history.window(context.current() or {})
    if window is None:
        return "当前不在聊天窗口里，没有操作历史"
    if not cids:
        return "没有指定要取回的调用"
    found, unknown = oplog.recall(window, cids)
    lines = [
        f"[{entry['cid']}]{'(已收缩)' if entry.get('condensed') else ''} "
        f"{entry['name']}({entry['arguments']})\n{entry['content']}"
        for entry in found
    ]
    if unknown:
        lines.append(f"找不到（已过保留期或从未存在）: {', '.join(unknown)}")
    return "\n\n".join(lines) if lines else "没有取回任何内容"


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


__all__ = ["exec_code", "list_tools", "reload_tools", "load_tools", "condense_ops", "recall_ops"]
