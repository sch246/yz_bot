"""把一批互不依赖的子任务并发交给另一个模型处理，并一次性取回全部结果。

适合"同一件事对很多条目各做一遍"：批量翻译、逐条摘要、给一组关键词分别查资料、对多个候选分别打分。**不适合**：只有一件事（直接自己做更快）、后一步依赖前一步结果（子任务之间看不见彼此）、需要多轮追问的任务。

调用方式：`prompt` 是所有子任务共享的说明（角色、要求、输出格式），`tasks` 每行一个具体条目，实际发给子模型的内容是 `prompt` + 换行 + 该行。写 `prompt` 时要把子模型需要的背景全部写进去。

子任务的环境很干净，务必按这个前提写提示：

- 它**看不到**当前聊天记录、当前提示词和你已经掌握的上下文，只能看到 `prompt` 和自己那一行；
- 它**不能**向用户提问，也不会等待回复；
- 它默认只有 `meta` 模块的工具，需要别的能力要用 `tools` 参数逐行列出模块名（先 `list_tools` 确认名字），子任务里那些函数同样带 `模块名__` 前缀；
- 它**和你处在同一个 QQ 聊天里**：一旦给它 `image`、`later`、`common` 这类会发消息、发图、建任务的模块，效果会直接落在当前聊天里，而不是只返回给你。只在确实需要时授权。

返回值是 `repr` 后的列表，每项是 `(原任务行, 子模型的回答文本)`，顺序与 `tasks` 相同。某个子任务失败时该项回答是 `ERROR: <原因>`，其余任务照常返回；这时按需重试那一条，或如实说明这条没完成。

并发上限是 5（`max_workers` 超过 5 也按 5 算），任务多时会分批跑完再一起返回，所以任务行数越多等待越久，一次尽量控制在十几条以内。每个子任务都按所用模型正常计费。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from mods import context, llm, tools as tool_modules


def assign_tasks(prompt: str, tasks: str, tools: str, model: str = "deepseek/deepseek-v4-flash", max_workers: int = 5) -> str:
    """并发分派互不依赖的 LLM 子任务，返回 [(任务, 回答), ...] 的字符串形式；失败项的回答以 ERROR: 开头。

    @param
    prompt: 每个子任务共享的完整说明；子模型看不到当前对话，背景和输出格式都要写在这里
    tasks: 每行一个子任务；该行会接在 prompt 后面发给子模型
    tools: 每行一个要在子任务中激活的工具模块名，不需要额外工具就传空字符串；子任务与当前聊天共享上下文，慎给会发消息的模块
    model: provider/model 形式的模型名，例如 deepseek/deepseek-v4-flash；需要子任务调用工具时要选支持函数调用的模型
    max_workers: 最大并发数，1 到 5，超过按 5 处理
    """
    task_list = [value.strip() for value in tasks.splitlines() if value.strip()]
    requested = list(dict.fromkeys(value.strip() for value in tools.splitlines() if value.strip()))
    workers = max(1, min(int(max_workers), 5))
    origin = context.current()

    def execute(task_value: str) -> tuple[str, str]:
        context.set_current(origin)
        worker_id = threading.get_ident()
        print(f"线程 {worker_id}: 开始处理 LLM 子任务", flush=True)
        try:
            session = llm.Chat(model=model, chat_client=llm.get_client())
            tool_context = tool_modules.create_context_message()
            session.set_messages([tool_context, f"{prompt}\n{task_value}"])
            tool_modules.bind_session(session, tool_context, requested)
            pieces = []

            def collect(chunk: llm.LLMResponse) -> None:
                if chunk.role == "assistant" and chunk.content:
                    pieces.append(str(chunk.content))
                if chunk.total_tokens:
                    # chat remains the sole owner of model pricing and usage state.
                    from mods import chat

                    chat.inc_call_tokens_cost(model, (chunk.prompt_tokens, chunk.completion_tokens))

            session.chat(recall_func=collect)
            print(f"线程 {worker_id}: LLM 子任务完成", flush=True)
            return task_value, "".join(pieces).strip()
        except Exception as error:
            print(f"线程 {worker_id}: LLM 子任务失败：{error}", flush=True)
            return task_value, f"ERROR: {error}"
        finally:
            context.clear_current()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(execute, task_list))
    return repr(results)


__all__ = ["assign_tasks"]
