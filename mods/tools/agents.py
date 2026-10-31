"""把独立任务交给分身并发处理，按原顺序返回任务、上下文编号和结果。

分身只看你通过 refs 引用的材料和本次 goal，不继承主运行的工具或唤醒状态。
refs 每行可写唤醒编号 #n、步骤 n.m、笔记名、上下文名或日志编号；引用保留原始来源。
tasks 每行一个独立任务，接在公共 goal 后；留空就只运行一次 goal。最多同时运行五项。

工具模块在 tools 中逐行指定，meta 始终可用。默认不暴露 send、type_word；确实需要
对外发言时可逐行显式写这两个函数名（me 表示同时授权两者）。授权只属于本次分身，
不会改变主运行。每项成功或失败都返回独立 ctx，可用 read_context 查看已完成的部分。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from mods import context, log, tools as tool_modules


_stream = log.stream("agent")


def fork(goal: str, refs: str = "", tasks: str = "", tools: str = "",
         model: str = "deepseek/deepseek-flash") -> str:
    """并发处理独立任务，返回 [(任务, ctx, 结果), ...]；失败结果以 ERROR: 开头。

    @param
    goal: 所有分身共同的明确目的，包含任务要求和输出格式
    refs: 每行一份已有材料引用：#唤醒、n.m 步骤、笔记名、ctx 或日志编号；留空不带材料
    tasks: 每行一个独立任务；留空只运行一次 goal
    tools: 每行一个要激活的工具模块，默认只有 meta；send、type_word 为显式发言授权，me 同时授权两者
    model: provider/model 形式的模型名；调用工具时需支持函数调用
    """
    from mods.chat import segment
    from mods.chat.run import run

    goal = goal.strip()
    if not goal:
        raise ValueError("fork goal cannot be empty")
    try:
        parent = tool_modules.current_binding()
    except RuntimeError:
        parent = None
    parent_run = parent["run"] if parent is not None else None
    lookup = parent_run["view"] if parent_run is not None else segment.fresh()
    items = segment.resolve_refs(lookup, refs)
    task_list = [value.strip() for value in tasks.splitlines() if value.strip()] or [""]
    tokens = list(dict.fromkeys(value.strip() for value in tools.splitlines() if value.strip()))
    grants = [name for name in tokens if name in {"send", "type_word"}]
    if "me" in tokens:
        grants = ["send", "type_word"]
    requested = ["meta", *(name for name in tokens if name not in {"meta", "send", "type_word", "me"})]
    parent_stop = getattr(parent["session"], "should_stop", None) if parent is not None else None

    def stopped():
        return bool(parent_run is not None and (parent_run.get("cancelled") or parent_run.get("closed"))
                    or callable(parent_stop) and parent_stop())

    def execute(task_value: str) -> tuple[str, str, str]:
        previous = context.current()
        context.set_current(None)
        state = {}
        worker_id = threading.get_ident()
        _stream.info(f"线程 {worker_id}: 开始处理 LLM 分身任务")
        try:
            purpose = goal + ("\n\n" + task_value if task_value else "")
            ctx, text = run(items, purpose, tools=requested, model=model or None,
                            kind="fork", state=state, stop=stopped, grant=grants)
            _stream.info(f"线程 {worker_id}: LLM 分身任务完成")
            return task_value or goal, ctx, text
        except Exception as error:
            _stream.info(f"线程 {worker_id}: LLM 分身任务失败：{error}")
            return task_value or goal, state.get("view", {}).get("ctx", ""), f"ERROR: {error}"
        finally:
            context.set_current(previous)

    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(execute, task_list))
    return repr(results)


__all__ = ["fork"]
