"""找几个分身帮手并发干活：把一批互不相干的同类小任务同时派给子模型，干完了统一交还给你。

## 什么时候叫分身

最适合**"同一件事，要对好多条内容分别做一遍"**的体力活：比如一口气翻译十几段话、分别给几篇长文写摘要、顺着十几个关键词各自去查资料、或者给一堆候选方案打分。
**不适合叫分身的事**：总共就一两件小事（你自己随手做掉反而更快）、下一步得等上一步结果才能定（分身之间是各干各的，互相看不见）、或者需要跟聊天里的人来回搭话的事。

## 怎么交代任务

调用时，`prompt` 是你给所有分身写的公共交代（要干什么、按什么格式交差），`tasks` 里每行写一个具体条目；每个分身收到的就是 `prompt` 加上它自己分到的那一行。

**一定要把背景喂饱**：
- 分身醒来时是在一间**完全空白的屋子**里：它看不到你现在的聊天记录、看不到你的笔记、也不知道前因后果，眼里只有你塞给它的那段 `prompt` 和它那一行任务。所以背景信息千万别省，得在 `prompt` 里写全。
- 它没法开口问别人，默认手里也只有最基础的 `meta` 工具。如果你需要它动用别的工具（比如搜索或读文件），得在 `tools` 参数里把模块名一行行写清楚授权给它。

最多同时跑 5 个分身，任务太多就会排队分批跑，所以一次最好控制在十几条以内，免得等太久、花太多 token。干完活后，结果会按原来的顺序一条条打包还给你；万一其中某一条卡壳报错了（`ERROR: ...`），不会拖累别的条目，你挑出没成的那条单独补做一下就好。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

from mods import context, llm, log, oplog, tools as tool_modules


_stream = log.stream("agent")


def assign_tasks(prompt: str, tasks: str, tools: str, model: str = "deepseek/deepseek-flash", max_workers: int = 5) -> str:
    """并发分派互不依赖的 LLM 子任务，返回 [(任务, 回答), ...] 的字符串形式；失败项的回答以 ERROR: 开头。

    @param
    prompt: 每个子任务共享的完整说明；子模型看不到当前对话，背景和输出格式都要写在这里
    tasks: 每行一个子任务；该行会接在 prompt 后面发给子模型
    tools: 每行一个要在子任务中激活的工具模块名，不需要额外工具就传空字符串；慎给会发消息的模块
    model: provider/model 形式的模型名，例如 deepseek/deepseek-flash；需要子任务调用工具时要选支持函数调用的模型
    max_workers: 最大并发数，1 到 5，超过按 5 处理
    """
    task_list = [value.strip() for value in tasks.splitlines() if value.strip()]
    requested = list(dict.fromkeys(value.strip() for value in tools.splitlines() if value.strip()))
    workers = max(1, min(int(max_workers), 5))

    def execute(task_value: str) -> tuple[str, str]:
        context.clear_current()
        worker_id = threading.get_ident()
        _stream.info(f"线程 {worker_id}: 开始处理 LLM 子任务")
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

                    chat.inc_call_cost(model, chunk.prompt_tokens, chunk.completion_tokens, chunk.cached_tokens,
                                       chunk.requested_at)

            session.chat(recall_func=collect)
            _stream.info(f"线程 {worker_id}: LLM 子任务完成")
            return task_value, "".join(pieces).strip()
        except oplog.PersistenceError:
            raise
        except Exception as error:
            _stream.info(f"线程 {worker_id}: LLM 子任务失败：{error}")
            return task_value, f"ERROR: {error}"
        finally:
            context.clear_current()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(execute, task_list))
    return repr(results)


__all__ = ["assign_tasks"]
