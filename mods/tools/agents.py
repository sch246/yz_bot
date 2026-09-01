"""提供并发分派独立 LLM 子任务的能力。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from mods import context, llm, tools as tool_modules


def assign_tasks(prompt: str, tasks: str, tools: str, model: str = "deepseek/deepseek-v4-flash", max_workers: int = 5) -> str:
    """并发分派互不依赖的 LLM 子任务。

    @param
    prompt: 每个子任务共享的提示
    tasks: 每行一个任务
    tools: 每行一个要激活的工具模块名
    model: provider/model 模型名
    max_workers: 最大并发数
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
