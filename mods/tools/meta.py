'''指导模型增删查改统一工具与 Skill 模块，并说明 last-good、显式应用和当前会话激活原理。

同一模型输出的多个工具调用按顺序执行，却看不到彼此结果；整批完成后立即成为一个正式 result R，正常下一子请求完整读取，不按工具拆分或自动分页。若 `say(final_call=true)` 结束、请求取消或失败，不会只为结果强迫续轮；下次激活按普通历史预算看到 R，需要全文可用 `recall_events`。`take` 是未读消息正式阅读的主名，`pull` 仅为前缀别名；`mentions` 是提及筛选别名。`read_messages` 选中的档案消息也在下一请求作为正式 input 阅读；`recall_events` 仍同步返回旧经历，不消费未读。

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

`reload_tools` 换得动的只有 `mods/tools/` 下的那一个文件（外加它的下划线 helper）。它 `import` 的 `mods.*`——包括这套机制自己的 `mods/tools/__init__.py`——是进程启动时的那份，改了要重启才生效，典型症状是“说明书和工具清单里有、调用时 AttributeError”。这条边界的完整说明在仓库的 `docs/runtime.md`（“热更换得动什么”一节），需要重启时用 op 工具集的 `send_command` 注入 `.reboot`；重启后先检查未读通知与信源状态，不要假设旧调用会重放。

## 删除

先确认精确模块名和源文件，再删除对应的单个 `mods/tools/foo.py` 或 `.md`，不要宽泛递归删除。随后调用 `reload_tools(["foo"])`；registry 发现源文件缺失后才删除 last-good，并从当前 Chat 移除模块内容和函数，当前会话的激活记录一并清除。只删文件但不 reload 时旧 last-good 仍有效。`meta.py` 是恢复入口，不能删除。删除 helper 前先检查并 reload 所有受影响模块。

## 统一经历与覆盖反查

每次模型输出（思考、正文、多个行动）只有一个 `YYYYMMDD-N` 号；该输出内部行动用 `YYYYMMDD-N#位置` 指名。新的中心输出保存完整原生思考，旧输出可能只有“曾有思考”标记。消息和工具结果也在被读到时进入同一信息流。结果可能在下一次模型请求才读到，未读不占正式号。

这些记录跨轮按读到的先后重建。中心会话统一用 `cover_events` 总结输入、输出和结果，不再维护另一套按原生工具调用位置收缩的历史。

结论写在 `conclusion` 参数里就够了，工具不会把它再返回一遍：这次调用本身留在上下文里，参数里的结论就是它的记录。

已读消息和输出也能总结：中心会话调用 `cover_events(["20260923-4", "20260923-5"], "结论")`，参数用的是上文方括号里的正式事件号，不是 QQ `message_id` 或行动位置。也可用 `cover_events(ids=[], conclusion="结论", anchor="20260923-4", before=2, after=3, kinds="input,result")`，或用 `start`／`end` 指定含端点的区间。覆盖范围没有条数上限；先从唯一已读顺序取原始种子，再筛选种类和来源，不按命中数补足。输出、整批返回、已确认的 `say`／回声是强绑定闭包，实际覆盖成员可能越过范围和筛选条件。闭包里有任一成员不可覆盖，整次失败，不丢掉那个成员后继续。覆盖使成员从本轮和后续自动上下文中消失；原号可用 `recall_events` 反查而不解除覆盖，反查总结会显示实际冻结的所有成员，不只是你传入的号。中心会话可跨窗口点名已读或反查过的旧号；未读成员不会被补进覆盖。摘要与聊天消息、输出、返回一样占历史事件数和 token 预算；私有 `.chat` 和子代理不能替中心会话写覆盖。

眼前历史只是全局已读信息流按事件数和 token 选出的可见部分，不是完整记录。新消息先留在有名字的有序未读信源；通知是创建时快照，尾部 hint 显示当前未读提及的时间和未读序号。`status(source)` 看数量与缺口，`take(source, start=1, count=8)` 在工具执行时按当前未读成员序号选连续范围，已读/跳过不计数；`ids`、`arrival`、`origin`、`message_id` 是高级精确入口。`mentions(source)` 正式消费至多 500 条未读提及，不是只看通知；`pull(source, count)` 是前缀别名。正式 input 保留自己的号，并记录发起输出 `read_by` 和实际工具 `read_via`；来源顺序中跨过的已读/跳过桥附在本次新 input 内，已读桥保留旧正式号，跳过桥显示 `skipped_by` 而没有旧 input 号。`mark_read(source)` 把调用时已有的成员跳过，不伪造 input；`fetch(source)` 从 NapCat 向旧端补取。通知已看见不等于消息已读；红点本身不会反复启动你，只有后来出现新唤醒时才再次叫你。窗口名是 `g<群号>` 或 `u<私聊对端号>`，主动 fetch 的旧档有自己的信源 key。启动补回先于该窗口新实时消息进入原信源；未收束前不能正式阅读或标为已读。已经读过的旧档信源不会倒插内容，后续 fetch 另开信源。远端历史不保证无缺口。用 `exec_code` 可调用 `ctx["chat"].unread_members(source)` 取得脱离内部权威的 `list[dict]`，用普通 Python 筛选后把其中 `key` 列表交给 `take(ids=...)`；修改快照不会修改未读事实。

聊天档案不属于未读信源。`read_messages(window, message_id, before, after)` 可在指定窗口中按 QQ `message_id` 选前后文；若号码歧义，加 `timestamp` 或改用 `origin`。它的同步 R 只确认安排，正文在下一子请求作为逐条正式 input 出现；命中当前 live/source 未读成员也一并消费。已读档案可再次阅读，产生新的 archive input，但不冒充 live 回声；`say_links` 只确认实际接收的回声。已读 input 也可用 `recall_events` 反查。总结只改变默认显示，不删除原文。

总结要方便反查：在结论里留下关键来源的正式号，别只写一段没有出处的大块故事。后续总结可以再次引用前一层总结；同一来源也可被多个不同主题的总结引用，不必强行归到唯一父节点。已被覆盖的正式号也可再次点名，与新的可见成员组成另一份总结；这不解除原覆盖。`event_links` 可查输入、输出和工具返回中明确出现的正式号，以及各节点实际覆盖的成员；“出现过编号”、“被覆盖”和“确实支撑某个结论”是三回事，核对原话仍须 `recall_events`。

普通输入、完整输出及整批工具返回都可用 `recall_events(["20260923-4"])` 按正式号反查；也可用 `recall_events(start="20260923-4", end="20260923-8", source="g123")` 直接读取短范围。它一次同步返回完整选择，取得一个新的 result 号，不进入未读信源、不做字符分页，也不把正文里的旧事件移动、复制或重新编号。范围返回列出实际冻结的 `resolved_ids`。若总结这次探索，通常覆盖这次 recall 的输出和结果，并在结论中引用旧号；结果真正进入上下文后，直接覆盖其中旧号也是允许的另一次明确选择。

`recall_events` 和 `event_links` 都能用 `anchor` 加前后数量，或用 `start`／`end` 指定有界区间；`kinds` 和 `source` 只在选定范围内筛选，不改变经历顺序。`event_links` 的输出 `reads` 是该输出实际导致正式读入的 input 号，不靠位置推断；其它一跳关系不读取正文、不授予覆盖信用。不要把这些选择器用于未读信源、原生操作号或 `say` 回声关联。

中心会话只载入一条全局经历的可见后缀；旧窗口正式号仍能按原号跨窗口反查，不复制或改号。更早的结果没被删除，仍能按引用取回。

## 全局待办

`edit_hint(text)` 整体替换中心 agent 的全局待办；传空字符串清空。它持久保存，下一次模型子请求会在末尾 hint 看到最新内容；hint 自身不逐版追加，但 `edit_hint` 行动仍照常留在信息流里。同一模型输出中的多个工具调用彼此看不到结果：即使 `cover_events` 排在 `edit_hint` 前，后者的文本也不能先写“覆盖已成功”。先等工具返回进入下一子请求，再记录依赖成败的待办。改动不会发送 QQ 消息；要对人说话仍须调用 `say`。这里的模型可见 hint 与聊天结束后向 QQ 发状态消息的 `#hint` 命令不是一回事。只放尚待处理的事，做完及时更新；可复用经验放 Skill，聊天证据放可反查的信息流。

## 说话

`say(text, target, final_call)` 是你**唯一**的发言方式——直接写在回复正文里的内容不会发出去，那是你的自言自语；它会作为自己的输出轨迹保留，但聊天参与者收不到。中心会话每次发言必须写明 `target="g<群号>"` 或 `target="u<私聊对端号>"`，不能凭最近读到谁来猜接收窗口。

- 返回值就是这条消息的 `message_id`。它可用于与聊天记录里的回声核对；要覆盖这句话，用回声读入后的信息流正式号，不用此 `message_id` 当 `cover_events` 的参数。
- `final_call` 默认 **true**：说完这句，当前工具循环结束；同一批里其它工具的结果这次看不到。只有已产生但尚未读到的工具结果、新召唤或显式安排的 take 会让中心 reader 接着运行；信源里仍有未读本身不会强迫续轮。若要在当前循环接着干活，显式传 `final_call=false`。
- 发送**失败**或**未确认**时，不管 `final_call` 传了什么，都会照常再跑一轮，让你看到发生了什么。"未确认"的意思是请求被收下了但没给回号码，消息很可能已经发出去——别直接重发，先看下一轮的聊天记录。
- 一次 `say` 发一条消息。要发几条就调几次，最后一条传默认的 `final_call=true`。

## 原子性与请求边界

每个模块单独校验和提交：任一导出失败，整个模块保留旧版；一次 reload 多个名称时，其它成功模块仍可独立提交。单个 LLM 子请求发送前会冻结工具 schema 与 callable 的同一份快照，所以 load/reload 只从下一次模型子请求起生效，不改变已发请求，也不改变同一响应中的其它工具调用。同一模型输出里的多个工具调用虽按序执行，参数却已一起生成，彼此看不到返回；依赖前一工具成败的判断须等下一子请求。
'''

from __future__ import annotations

from contextvars import ContextVar
import io
from typing import Mapping

from mods.tools import current_binding


_offline_send_sink: ContextVar[object | None] = ContextVar("meta_offline_send_sink", default=None)
_DEFAULT_PULL_COUNT = 8


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


def _select_events(window, *, ids=None, anchor: str = "", before: int = 0, after: int = 0,
                   start: str = "", end: str = "", kinds: str = "", source: str = ""):
    from mods import chat, oplog

    if not isinstance(kinds, str):
        raise ValueError("kinds 只能包含 input、output、result、notification")
    if not isinstance(source, str):
        raise ValueError("source 必须是 g<群号> 或 u<私聊对端号>")
    selected_kinds = [kind.strip() for kind in kinds.split(",")] if kinds else []
    if any(not kind for kind in selected_kinds):
        raise ValueError("kinds 只能包含 input、output、result、notification")
    source_window = chat.parse_target(source) if source else None
    return oplog.select_events(window, ids=ids, anchor=anchor, before=before, after=after,
                               start=start, end=end, kinds=selected_kinds,
                               source_window=source_window)


def recall_events(ids: list[str] | None = None, anchor: str = "",
                  before: int = 0, after: int = 0, start: str = "", end: str = "",
                  kinds: str = "", source: str = "") -> str:
    """按正式号或一段已读经历直接返回输入、完整输出和结果；中心会话可跨窗口查旧号。整次调用只有一个 result，正文中的旧事件不重新编号。

    @param
    ids: 显式正式号列表；与 anchor 或 start/end 二选一，范围调用可省略
    anchor: 中心正式号；可用 before/after 取邻近已读事件
    before: anchor 之前的原始事件数，不按筛选命中补足
    after: anchor 之后的原始事件数，不按筛选命中补足
    start: 含端点的区间起点；须同时给 end
    end: 含端点的区间终点；须同时给 start
    kinds: 可选逗号分隔 input、output、result、notification；只筛选种子
    source: 可选 g<群号> 或 u<私聊对端号> 来源窗口；只筛选种子
    """
    import json

    from mods import oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里"
    try:
        selected, missing = _select_events(window, ids=ids, anchor=anchor, before=before,
                                           after=after, start=start, end=end,
                                           kinds=kinds, source=source)
    except ValueError as error:
        return f"未反查：{error}"
    resolved_ids = [entry["id"] for entry in selected]
    found, unavailable = oplog.recall_events(window, resolved_ids)
    missing = list(dict.fromkeys([*missing, *unavailable]))
    if not found and not missing:
        return "范围内没有符合筛选条件的已读事件"
    return json.dumps({"resolved_ids": resolved_ids, "events": found, "missing": missing},
                      ensure_ascii=False)


def event_links(ids: list[str] | None = None, anchor: str = "", before: int = 0,
                after: int = 0, start: str = "", end: str = "", kinds: str = "",
                source: str = "") -> str:
    """按正式号查看一跳关系；输出的 reads 是实际读入的 input 号，非位置推断。

    @param
    ids: 显式正式号列表；与 anchor 或 start/end 二选一，范围调用可省略
    anchor: 中心正式号；可用 before/after 取邻近已读事件
    before: anchor 之前的原始事件数
    after: anchor 之后的原始事件数
    start: 含端点的区间起点；须同时给 end
    end: 含端点的区间终点；须同时给 start
    kinds: 可选逗号分隔 input、output、result、notification；只筛选查询根
    source: 可选 g<群号> 或 u<私聊对端号> 来源窗口；只筛选查询根
    """
    import json

    from mods import oplog

    window = _tool_window()
    if window is None:
        return "当前不在聊天窗口里"
    try:
        selected, missing = _select_events(window, ids=ids, anchor=anchor, before=before,
                                           after=after, start=start, end=end,
                                           kinds=kinds, source=source)
    except ValueError as error:
        return f"未查询关系：{error}"
    found = oplog.reference_links(window, [entry["id"] for entry in selected])
    if missing:
        found["找不到"] = missing
    return json.dumps(found, ensure_ascii=False)


def cover_events(ids: list[str], conclusion: str, anchor: str = "", before: int = 0,
                 after: int = 0, start: str = "", end: str = "", kinds: str = "",
                 source: str = "") -> str:
    """将本次主窗口可见或此前已覆盖的事件归入这次行动的结论；成员不再自动载入，但仍可按原编号反查。关联的整批输出、返回和已确认 say 回声必须一同覆盖。

    @param
    ids: 显式正式号列表；与 anchor 或 start/end 二选一，范围调用请传 []
    conclusion: 你从这些事件得出的结论，写出供后续子请求保留的摘要
    anchor: 中心正式号；可用 before/after 取邻近已读事件
    before: anchor 之前的原始事件数
    after: anchor 之后的原始事件数
    start: 含端点的区间起点；须同时给 end
    end: 含端点的区间终点；须同时给 start
    kinds: 可选逗号分隔 input、output、result、notification；只筛选种子
    source: 可选 g<群号> 或 u<私聊对端号> 来源窗口；只筛选种子
    """
    from mods import chat, oplog

    window = _tool_window()
    session = current_binding().session
    if window is None or not session.reads_window_mail or not session.active_action:
        return "仅主窗口正在读取 mail 的会话能覆盖信息流；私有 .chat 和子代理不可覆盖"
    if not conclusion.strip():
        return "请给出非空结论"
    try:
        selected, missing = _select_events(window, ids=ids, anchor=anchor, before=before,
                                           after=after, start=start, end=end,
                                           kinds=kinds, source=source)
        if missing:
            raise ValueError("不是本窗口已读事件: " + ", ".join(missing))
        visible = (chat.agent._trusted_stream_ids(session) | getattr(session, "recalled_ids", set())
                   if window == chat.AGENT_WINDOW
                   else chat.agent._visible_stream_ids(session.messages))
        members = oplog.cover(window, session.active_action,
                              [entry["id"] for entry in selected], visible)
    except ValueError as error:
        return f"未覆盖：{error}"
    if window == chat.AGENT_WINDOW:
        chat.agent._cover_agent_projection(session, members)
    else:
        chat.agent._cover_projection(session.messages, members)
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
    发，往返因此闭合。在这里 `cq.escape` 会让它写的 at 和图片变成字面文本。

    WHY: 它住在 meta 而不是自己一个模块，因为发言是模型**永远**该有的能力，而
    `tools._BASE_MODULE_NAME` 是单数、基础模块只有一个。meta 早就不只是"管工具模块"了
    （事件反查和覆盖工具也管上下文），它实际是那组不可卸载的基础能力，`say`
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


def _take(source: str, count: int, start: int, ids: list[str] | None,
          arrival: str, origin: str, message_id: str,
          mentions_only: bool, via: str) -> str:
    from mods import chat, context

    session = current_binding().session
    if not context.agent_mode() or not session.reads_window_mail:
        return "只有中心 reader 可以正式阅读信源"
    if type(count) is not int or not 1 <= count <= chat.MAX_PULL_EVENTS:
        return f"count 必须是 1..{chat.MAX_PULL_EVENTS}"
    if type(start) is not int or start < 1:
        return "start 必须是从 1 起的当前未读序号"
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(key, str) for key in ids)):
        return "ids 必须是稳定成员 key 列表"
    if sum((ids is not None, bool(arrival), bool(origin), bool(message_id))) > 1:
        return "ids、arrival、origin、message_id 只能指定一种"
    if start != 1 and any((ids is not None, arrival, origin, message_id)):
        return "start 只能用于未读范围选择，不能与精确定位并用"
    selected = []
    try:
        if ids is not None or arrival:
            for key in dict.fromkeys(ids if ids is not None else [arrival]):
                member = chat.reader._unread_member_by_key(source, key)
                if member is not None and (not mentions_only or member["mentioned"]):
                    selected.append(member)
        else:
            for ordinal, (member, _event) in enumerate(chat.reader._iter_unread_metadata(source), 1):
                if ordinal < start:
                    continue
                if mentions_only and not member["mentioned"]:
                    continue
                if origin and member["origin"] != origin:
                    continue
                if message_id and str(member["message_id"]) != message_id:
                    continue
                selected.append(member)
                if not origin and not message_id and len(selected) >= count:
                    break
    except ValueError as error:
        return f"未安排正式阅读：{error}"
    if message_id and len(selected) > 1:
        return "message_id 命中多条，请用 origin 或稳定成员 key 消歧义"
    if not selected:
        return "该信源没有匹配的未读成员；未安排阅读"
    if ids is not None and len(selected) > 1:
        keys = {member["key"] for member in selected}
        ranks = {member["key"]: position
                 for position, member in enumerate(chat.reader._all_source_members(source))
                 if member["key"] in keys}
        selected.sort(key=lambda member: ranks[member["key"]])
    window = tuple(selected[0]["window"])
    session.associated_windows.add(window)
    session.requested_reads.append({"window": list(window), "source": source,
                                    "members": selected, "read_by": _output_id(session),
                                    "read_via": via})
    return f"已安排下一次模型请求前正式阅读 {len(selected)} 条；正文不在工具结果中返回"


def _output_id(session) -> str | None:
    action = getattr(session, "active_action", None)
    return str(action).partition("#")[0] if action else None


def take(source: str, count: int = _DEFAULT_PULL_COUNT, ids: list[str] | None = None,
         arrival: str = "", origin: str = "", message_id: str = "",
         mentions_only: bool = False, start: int = 1) -> str:
    """按执行时当前未读序号选择连续范围，并在下一请求正式阅读；精确选择是高级入口。

    @param
    source: g<群号>、u<私聊对端号> 或独立信源 key
    count: 从 start 起读多少个当前未读成员，1 到 500
    start: 当前未读信源中从 1 起的位置；已读和跳过项不计数
    ids: 高级入口：稳定成员 key 列表，可由 chat.unread_members 快照取得
    arrival: 高级入口：实时消息的精确 arrival
    origin: 高级入口：精确档案位置
    message_id: 高级入口：QQ 消息号；歧义时改用 ids 或 origin
    mentions_only: 只正式读取当前未读提及；mentions 是此选项的薄入口
    """
    return _take(source, count, start, ids, arrival, origin, message_id,
                 mentions_only, "take")


def _source_status_line(source: dict) -> str:
    return (f"{source['key']} {source['name']} 窗口={tuple(source['window'])} "
            f"状态={source['state']} 未读={source['remaining']} "
            f"提及={source['mention_count'] - source['read_mention_count']} "
            f"已拉取={source['pulled']}"
            + (f" 缺口={source['gap']}" if source['gap'] else ""))


def status(source: str = "") -> str:
    """查看未读通知栏；可查看全部活跃信源，也可精确查看一个窗口或历史信源，不读取正文。

    @param
    source: 留空查看全部活跃信源；或填 g<群号>、u<私聊对端号>、fetch 返回的信源 key
    """
    from mods import chat, context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以查看信源状态"
    details = chat.unread_details()
    sources = oplog.sources()
    if source:
        try:
            window = chat.parse_target(source)
        except ValueError:
            state = oplog.resolve_source(source)
            if state is None or state["key"] != source:
                return "找不到该窗口或信源 key"
            lines = [_source_status_line(state)]
        else:
            lines = [chat.reader._unread_detail_text(detail) for detail in details
                     if tuple(detail["window"]) == window]
            lines.extend(_source_status_line(state) for state in sources
                         if tuple(state["window"]) == window
                         and (state["remaining"] or state["state"] != "complete"))
            if not lines:
                lines = [f"{source} 当前没有未读、补回缺口或活跃历史信源"]
    else:
        lines = []
        gap_counts = {}
        history_gap_counts = {}
        for detail in details:
            recovery = detail.get("recovery")
            if (recovery and recovery.get("gap") and not detail["unread"]
                    and not detail["mentions"] and not detail["other_wakes"]
                    and not recovery["remaining"]):
                reason = recovery["gap"].split("; cursor=", 1)[0]
                gap_counts[reason] = gap_counts.get(reason, 0) + 1
            else:
                lines.append(chat.reader._unread_detail_text(detail))
        for state in sources:
            if state["source_type"] != "napcat_history":
                continue
            if (state["remaining"] or state["mention_count"] > state["read_mention_count"]
                    or state["state"] == "fetching"):
                lines.append(_source_status_line(state))
            elif state["gap"]:
                reason = state["gap"].split("; cursor=", 1)[0]
                history_gap_counts[reason] = history_gap_counts.get(reason, 0) + 1
            elif state["state"] != "complete":
                lines.append(_source_status_line(state))
        lines.extend(f"无待读内容的窗口缺口：{reason}，{count} 个窗口"
                     for reason, count in gap_counts.items())
        lines.extend(f"无待读内容的历史信源缺口：{reason}，{count} 个信源"
                     for reason, count in history_gap_counts.items())
        if not lines:
            return "当前没有待处理的未读信源；未读未减少"
        lines.append("可用 status(source) 按具体 g/u 窗口或历史信源 key 查看完整状态")
    rendered = "\n".join(lines)
    excerpt = chat.bounded_excerpt(rendered, chat.MAIL_PULL_TOKENS - 1000)
    if len(excerpt) < len(rendered):
        complete_lines = excerpt.splitlines()
        if excerpt and not excerpt.endswith("\n") and rendered[len(excerpt)] != "\n":
            complete_lines.pop()
        return "\n".join([*complete_lines,
                          "状态过多，返回已截断；可用 status(source) 按具体窗口或信源 key 查询",
                          "未读未减少"])
    return rendered + "\n未读未减少"


def fetch(source: str) -> str:
    """从 NapCat 向前补一个信源；未读缺口可续接，已读信源会从旧端另开信源。

    @param
    source: g<群号>、u<私聊对端号>，或已有历史信源 key
    """
    from mods import chat, context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以拉取信源"
    try:
        window = chat.parse_target(source)
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source:
            return "找不到该窗口或信源 key"
        window = tuple(state["window"])
        source_key = state["key"]
    else:
        source_key = None
    current_binding().session.associated_windows.add(window)
    source = chat.fetch_remote_source(window, source_key)
    return (f"信源 {source['key']}（{source['name']}）状态={source['state']}；"
            "后台持续追到锚点或上游尽头，完成前不能正式 take；用 status 查看进度")


def mentions(source: str) -> str:
    """正式读取当前未读 @／提及，最多 500 条；其它未读成员仍留在信源。

    @param
    source: g<群号>、u<私聊对端号>，或 fetch 返回的独立信源 key
    """
    from mods import chat

    return _take(source, chat.MAX_PULL_EVENTS, 1, None, "", "", "", True, "mentions")


def read_messages(window: str, message_id: str = "", origin: str = "", timestamp: int = 0,
                  before: int = 4, after: int = 4) -> str:
    """从本地档案选消息，在下一次模型请求前作为正式 input 阅读；已读档案可再次阅读。

    @param
    window: g<群号> 或 u<私聊对端号>
    message_id: QQ 消息号；与 origin 二选一
    origin: 稳定档案位置；与 message_id 二选一
    timestamp: message_id 命中多条时用来消歧义的 Unix 整数秒；0 表示不指定
    before: 锚点之前返回多少条档案记录，非负整数
    after: 锚点之后返回多少条档案记录，非负整数
    """
    from mods import chat, chatlog, context

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以查阅聊天档案"
    try:
        target = chat.parse_target(window)
    except ValueError as error:
        return str(error)
    if not message_id and not origin:
        return "请指定 message_id 或 origin"
    if message_id and origin:
        return "message_id 与 origin 只能指定一个"
    current_binding().session.associated_windows.add(target)
    try:
        records = chatlog.read_around(
            *target,
            message_id=message_id or None,
            origin=origin or None,
            timestamp=timestamp or None,
            before=before,
            after=after,
        )
    except ValueError as error:
        return f"未查看：{error}"
    selected = [record for record in records
                if chat.view._model_event(record, target[0] == "group") is not None]
    if not selected:
        return "本地档案没有命中可见记录；未安排阅读"
    current_binding().session.requested_reads.append(
        {"window": list(target), "records": selected,
         "read_by": _output_id(current_binding().session), "read_via": "read_messages"})
    return f"已安排下一次模型请求前从档案正式阅读 {len(selected)} 条；正文不在工具结果中返回"


def mark_read(source: str) -> str:
    """将一个信源在调用时已存在的全部未读设为已读。这些内容不取得经历号，但仍可用 read_messages 再读档案。

    @param
    source: g<群号>、u<私聊对端号>，或 fetch 返回的独立信源 key
    """
    import json

    from mods import chat, context, oplog

    if not context.agent_mode() or not current_binding().session.reads_window_mail:
        return "只有中心 reader 可以设为已读"
    try:
        window = chat.parse_target(source)
        state = None
    except ValueError:
        state = oplog.resolve_source(source)
        if state is None or state["key"] != source:
            return "找不到该窗口或信源 key"
        if state["source_type"] == "napcat_boot":
            return "启动补回属于原窗口信源，请用对应的 g/u 窗口名标为已读"
        window = tuple(state["window"])
    current_binding().session.associated_windows.add(window)
    try:
        read_by = _output_id(current_binding().session)
        result = (chat.mark_window_read(window, read_by=read_by) if state is None
                  else chat.mark_source_read(state, read_by=read_by))
    except ValueError as error:
        return f"未标为已读：{error}"
    return json.dumps(result, ensure_ascii=False)


def pull(source: str, count: int = _DEFAULT_PULL_COUNT) -> str:
    """take(source, count) 的前缀薄别名；新调用请优先使用 take。

    @param
    source: g<群号>、u<私聊对端号>，或 fetch 返回的信源 key
    count: 希望读取的事件数，1 到 500；单次输入预算可能使实际数量更少
    """
    return _take(source, count, 1, None, "", "", "", False, "pull")


def edit_hint(text: str) -> str:
    """整体替换中心 agent 的全局待办 hint；传空字符串清空，不会向 QQ 发送消息。同批工具互不可见结果，别在同批宣称 cover_events 已成功。

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
    "status",
    "take",
    "mentions",
    "fetch",
    "pull",
    "mark_read",
    "read_messages",
    "edit_hint",
    "exec_code",
    "list_tools",
    "reload_tools",
    "load_tools",
    "recall_events",
    "event_links",
    "attach_image",
]
