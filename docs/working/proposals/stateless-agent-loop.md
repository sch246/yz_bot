# 无状态主循环：每次唤醒重建上下文

> 状态：提案，尚未实现。维护者 2026-10-19 提出方向，并裁定了第 6、7 节的全部问题。本文不描述当前运行行为；现行合同仍以[交互模型](../../interaction-model.md)、[运行架构](../../architecture.md)和[LLM 文档](../../llm.md)为准。
>
> 维护者已确认推翻[中心 reader 简化](reader-cleanup-and-split.md#21-上下文上限只决定从哪里开始读)里 2026-09-26 的决定（固定起点、不滑窗、模型自己压缩），也推翻[给 agent 做一个聊天软件](chat-client-for-an-agent.md)的阅读模型：维护者认为"通知 + 拉取 + 正式阅读"这套聊天软件反直觉。

## 1. 要解决的问题

维护者报告的两个实际问题：

- **token 消耗高**
- **记忆不好**：总是忘记刚刚发生了什么，把握不住时间顺序。

下面是从代码读出的机制。本文没有查看生产日志或用量数据，数字只是估计；第 8 节说明怎样测量。

| 现状机制 | 代码位置 | 后果 |
|---|---|---|
| 上下文是中心 agent 自己的**工作日志**：固定起点之后所有未覆盖的 O/R/input/通知按序重放 | `view.agent_rows`、`agent._run_agent` | 上下文随经历增长，直到模型 cover；每次调用都要重发整段 |
| 开局只给通知，正文要模型 `status`/`take` 拉取，下一次请求才正式读入 | `_base_prompt`、`agent._agent_provider` | 回一句话至少要两三次调用，每次都带完整历史 |
| 输出和行动返回不带时间，通知里是 unix 秒 | `view._output_projection`、`_result_projection`、`_activation_text` | 先后顺序只能靠位置推断 |
| 自己说的话以 `say(...)` 参数的形式出现，和群里的消息分在两处 | `view._output_projection` | "刚才那个群里发生了什么"要从散落的片段拼起来 |
| 模型看到的消息是 XML 元数据块，chatlog 是另一种行格式 | `view.msg2chat`、[chatlog 格式](../../chatlog-format.md) | 两套格式；模型不能"直接去翻文件"，只能经过专用阅读工具 |
| 压缩全靠模型自觉 cover | `cover_events`；回放实验见[离线回放](memory-replay-evaluation.md) | 默认提示只会形成深度 1 的线性摘要；不压缩就撑到上限 |

## 2. 参照

**QQ-agent**（[`src/prompt.js`](https://github.com/K0nd1us/QQ-agent/blob/main/src/prompt.js)，2026-10-19 读取 main，与[调查](qqbot-persistent-memory-research.md)核对的 `11577ba` 行数一致）文件开头写的就是"无状态 + 每次新开会话"。每次唤醒重建：

- 系统提示（静态）；
- 角色设定 → 引导说明 → 相关成员印象 → 该会话最近 80 条已读消息 → 未读消息 → 当前时间。

每条消息一行，形如 `[时间] 名字(QQ:号)：内容`，自己的发言标成"我"。本次运行内的工具调用和思考用完即弃，段落按"越稳定越靠前"排列以命中前缀缓存。

**SLA**（[arXiv 2610.07625](https://arxiv.org/abs/2610.07625)，见[研究地图](memory-and-self-evolution-research.md#状态与上下文分离)）：

- 跨调用的状态归 harness；
- 无状态的 Advisor 读证据摘要、给 Worker 下发具体任务；
- Worker 只看到自己的任务、保留候选和局部反馈；
- 进展由产物携带，而不是由对话携带。

**mattpocock 的 [retro skill](https://github.com/mattpocock/skills/blob/main/skills/engineering/retro/SKILL.md)**：复盘的对象是 agent 所处的**环境**，不是会话摘要。

- 读一手材料，不凭记忆；
- 每次都会加载进上下文的文件只放导航指针，细节放在被指向的文件里；
- 找出"不改变行为的指令"并删掉；
- 上下文压力最大的执行者不负责维护规范，由压力最小的复核者负责。

这正是图书管理员与聊天 agent 的分工（3.5）。

## 3. 目标形态

### 3.1 三个角色，都无状态

一次唤醒的流程：

```
唤醒 → 前置 agent（整理情景、挑选材料）→ 聊天 agent（行动）→ 图书管理员（复盘、改笔记）
```

三者都不跨调用继承会话；跨调用的东西只有文件：chatlog、行动记录、笔记、待办、Skill。

### 3.2 统一消息格式：模型看到的就是 chatlog

模型看到的消息直接采用 [chatlog 行格式](../../chatlog-format.md)：

```
# g群号 2026-10-19
【头衔】名字(QQ号) HH:MM:SS | 消息号
    正文
```

- 日期和窗口在文件路径里，画面用一行标题补上。
- 正文是 OneBot 原始串（含 CQ 码），和现在模型看到的一样。
- 自己的发言就是 chatlog 里 Bot 自己 QQ 号的那几行；系统提示告诉它自己的 QQ 号。

格式统一以后，"读更多消息"不再需要专用阅读工具：聊天 agent 和前置 agent 都可以直接按路径读 chatlog 文件（经 `exec_code`，或一个按窗口和日期读取的小函数），读到的和画面里的是同一种文本。

### 3.3 前置 agent

参照 SLA 的 Advisor。每次唤醒先跑一次便宜模型的无状态调用。

- **输入**：
  - 唤醒信息：哪些窗口、谁、因为什么；
  - 被唤醒窗口最近的画面；
  - 笔记目录（3.5）和全局待办；
  - 最近几次行动记录的标题。
- **能力**：可以读 chatlog、行动记录和笔记文件。
- **输出**：
  - 几行情景说明；
  - 一组**引用**：哪个窗口的哪段时间、哪份笔记、哪几条行动记录。

由 harness 按引用取出原文放进聊天 agent 的上下文。前置 agent 只给指针，不复写内容：为了挑选 4k 材料而生成 4k token 是浪费，这和[基础讨论](memory-foundations-discussion.md)里的成本论证是同一件事。

默认情况下，被唤醒窗口最近 N 条直接进画面，不需要前置 agent 选择；它负责的是"这次还需要看什么"，例如另一个群里的相关对话、某人的笔记、自己前天做过的事。

### 3.4 聊天 agent 的上下文

按"稳定 → 易变"排列：

1. **系统提示**：改写后的 `_base_prompt`，明确"你没有跨次运行的对话记忆；要记住的东西写进待办或 Skill"。
2. **笔记**：前置 agent 引用的笔记，以及被唤醒窗口和相关人物的笔记。
3. **全局待办**（`_agent_hint`）和已加载的 Skill。
4. **前置 agent 的情景说明和引用材料**。
5. **窗口画面**：被唤醒窗口最近 N 条（默认值，参照 QQ-agent 的 80），按 3.2 的格式，在"上次看到的位置"处分开已读和新消息。
6. **其它窗口的新消息计数**。
7. **当前时间**，放最末。

一次唤醒内的多轮工具调用照旧在 `session.messages` 中累积，结束即丢弃；思考内容不保留（D4）。画面按各窗口的实际时间排列（D6）。

### 3.5 图书管理员

- **时机**：每次唤醒结束后都触发。
- **输入**：这次唤醒的一手材料，包括看到的画面、行动记录和工具返回；以及相关范围的现有笔记。
- **范围**：全局、个人、群三种笔记（D9）；个人笔记同时承担与此人私聊的笔记，也记录维护者给此人设的自定义名（D7）。
- **权限**：可以新增、修改、删除条目。

参照 retro 的工作方式：

- 先读现有笔记，去重；每条写进最合适的范围。
- 优先级：被人纠正的地方 > 工具失败和返工 > 花费过大的操作 > 缺失的信息 > 一般事实。
- 删除不再改变行为的条目，以及已经解决、过时的条目。
- 每次都会注入的部分保持简短，细节写进可按需读取的文件。
- 未完成的事项写进全局待办，而不是笔记。

**存储**：普通 Markdown 文件，人能直接看和改。每条带日期和来源（窗口与消息号，或行动记录号）。

**安全**：没有评测器替它把关，所以每次改动前保留上一版，删除留墓碑，不直接抹掉。

**并发**：它和下一次唤醒互不等待。笔记按文件原子替换；下一次唤醒读到的是已经写完的那一版。

## 4. 统一事件流的去向

oplog 现在同时承担四件事：上下文来源、阅读状态、压缩树、行动记录。改造后只剩**行动记录**：聊天 agent 的输出、工具调用和返回，供前置 agent、图书管理员和维护者查阅。

它也改成和 chatlog 同类、按日期分文件的可读行动日志（D8），让"读文件"成为唯一的查阅方式。

## 5. 要同步移除的记忆与阅读机制

按 2026-10-19 的代码盘点。"移除"指实现无状态循环时一并删除，删前逐个确认没有其它消费者。

### 5.1 以经历流为上下文

| 机制 | 位置 |
|---|---|
| 固定起点 `agent.history_start`、`#reset_start`、`#limit` 的起点语义 | `view.agent_rows`、`subcommands` |
| 压力提示 `_pressure_hint`、"达到上限先 cover" | `agent.py`、`_base_prompt` |
| 正式号投影 `[YYYYMMDD-N]`、`stream_ids`、`_remember_stream`、`_visible_stream_ids` | `view.py`、`agent.py` |
| 工具状态差量通告 `_TOLD_TOOLS_KEY`（绑定起点） | `agent._agent_provider` |
| 旧结果桥 `_stream_results`、`_drain_legacy_results` | `agent.py`、`reader.py` |
| `_closing_hint`（为补齐重放尾部的 assistant） | `view.py`；它的 `WHY:` 记录的 DeepSeek 约束在单次唤醒内仍可能适用，删前复核 |

### 5.2 压缩与反查

| 机制 | 位置 |
|---|---|
| `cover_events` 工具 | `tools/meta.py`、`agent.cover_stream` |
| 覆盖树：`oplog.cover`、`covered`、`coverage_members`、condensed 事件 | `oplog/__init__.py` |
| `recall_events`、`event_links`、`select_events`、`reference_links`、`_credit_recall`、`recalled_ids` | `meta.py`、`agent.py`、`oplog` |

后两项的用途由"读行动日志文件"取代。

### 5.3 聊天软件式阅读

| 机制 | 位置 |
|---|---|
| 工具 `take`、`status`、`fetch`、`mentions`、`read_messages`、`mark_read`、`pull` | `tools/meta.py` |
| 未读到达与正式阅读：`arrive`、`unread`、`mark_arrivals_read`、`input*`、`read_by`/`read_via`、来源桥 | `oplog`、`chat/reader.py` |
| 通知 v2 的交付与确认：`deliver_notifications`、`acknowledge_notification`、`has_unnotified` | `oplog`、`agent._drive_agent` |
| 未读提示 `_pending_hint`、Python 接口 `chat.unread_members` | `reader.py` |
| 回声冻结 `sent_by`（只服务正式阅读里的 `<sent_by>`） | `oplog`、`reader._formal_input` |

**替代**：唤醒仍由 `activation_signal` 判断；每个窗口只记一个"上次看到的位置"，画面据此分开已读和新消息。

### 5.4 恢复与回放

| 机制 | 去向 |
|---|---|
| NapCat 离线补回 `_backfill`、`_backfill_archive`、`_napcat_history` | 保留"补进 chatlog"；删除 oplog 信源页 `_source_pages`、`start_source` 等，补回的消息就是 chatlog 里的普通行 |
| 离线回放 `oplog/replay.py`、`run_offline`、`_offline_scope`、`oplog_legacy` | 按新循环重写：回放 chatlog 而不是 oplog。验证（第 8 节）需要它，所以不能只删不建 |

### 5.5 保留

- `edit_hint` 全局待办：跨唤醒任务唯一的直接写入通道。
- Markdown Skill 与 `load_tools`。
- `say`、`exec_code`、`attach_image`、图片台账，以及 `assign_tasks` 子任务。
- `#hint` 窗口收尾机制。

### 5.6 随之退役或改写的文档

- **退役为历史记录**：
  - [统一信息流](unified-event-stream.md)；
  - [压缩与反查](chat-condense.md)及其[历史](chat-condense-history.md)；
  - [交接索引](condense-and-unify-handoff.md)；
  - [中心化阅读计划](chat-client-reading.md)、[聊天软件论点](chat-client-for-an-agent.md)；
  - [mail 与激活](mail-and-activation.md)、[检索索引](retrieval-index.md)。
- **部分改写**：
  - [离线消息恢复](offline-message-backfill.md)；
  - [第二轮简化](agent-simplify-round2.md)、[reader 简化](reader-cleanup-and-split.md)；
  - [自发消息回流](self-message-echo.md)的 `sent_by` 部分；
  - [离线回放实验](memory-replay-evaluation.md)；
  - [记忆估值](memory-valuation-and-index-evolution.md)。
- **方向一致、保留**：[可编程经历数据](programmable-experience-data.md)（用 `exec_code` 直接查磁盘历史）。
- **正式文档**：`interaction-model.md`、`architecture.md`、`llm.md`、`commands.md` 随实现一起改。

`_run_agent` 中"不滑窗"的 `WHY:` 按维护者的新决定改写，不静默删除。

## 6. 维护者的裁定（2026-10-19）

| 编号 | 问题 | 裁定 |
|---|---|---|
| D1 | 推翻固定起点、画面只显示最近 N 条 | 同意 |
| D2 | N 与窗口范围 | N 取默认；跨窗口内容由前置 agent 挑选 |
| D3 | 拉取式阅读 | 移除聊天软件式阅读；统一 chatlog 与模型可见格式，读消息直接读文件 |
| D4 | 思考内容 | 不保留 |
| D5 | 图书管理员 | 全局、每人、每窗口都有笔记；每次唤醒结束都触发；必须能删改旧条目；参照 retro skill；`cover_events` 移除 |
| D6 | 跨窗口顺序 | 画面按实际时间排列，可以接受 |

## 7. 后续裁定（2026-10-19）

| 编号 | 问题 | 裁定 |
|---|---|---|
| D7 | 画面里用哪种名字 | 用 chatlog 记录的 QQ 侧名字；`.setname` 自定义名改为写进对这个人的笔记 |
| D8 | 行动记录的形式 | 改成和 chatlog 同类的可读日志文件 |
| D9 | 私聊笔记 | 私聊窗口与这个人的笔记合成一份，即个人笔记；群聊另有群笔记 |
| D10 | 前置 agent 的模型 | 默认用和 `assign_tasks` 相同的便宜模型 |

随之确定的笔记范围只有三种：全局、个人（含与此人的私聊）、群。

D7 让 [chatlog 格式](../../chatlog-format.md)里"模型看到的名字是另一回事，两条路刻意分开"这一段失效；实施时要改写该段，并迁移现有 `.setname` 数据（只写进个人笔记，不改写 chatlog）。

## 8. 验证

**离线回放对照**：用重写后的回放（5.4）在同一段 chatlog 上比较：

1. 现状；
2. 无状态（无前置 agent、无图书管理员）；
3. 加前置 agent；
4. 再加图书管理员。

至少在两个累计 token 预算点上分别报告。

**观察指标**

- 每次回复的调用次数，以及普通输入、缓存输入、输出 token 和实际费用；
- 人工标注的探针："刚才发生了什么、先后顺序"、承诺有没有兑现、跨窗口的事有没有接上；
- 笔记后来有没有被用到、有没有用错，删掉的条目有没有被需要过。

**估计（未测）**：现状一次回复要两三次调用，每次带最多约 40k token 的历史。无状态后，一次回复是一次便宜的前置调用，加上通常一次的聊天调用，画面和笔记大约在 10k 以内；另外加一次图书管理员调用。

**代码检查**：每次改动后运行 `run.py --check` 和 `--smoke`。

## 9. 实施切片

1. **S1 画面与格式**：写纯函数，把 chatlog 渲染成画面并支持按路径读取；用"系统提示 + 待办 + 画面 + 时间"替换 `_run_agent` 的重放；每窗口记"上次看到的位置"；删除 5.1 与 5.3。
2. **S2 回放**：按新循环重写离线回放，先用它对照 S1 与现状。
3. **S3 图书管理员**：笔记文件、触发、版本与墓碑。
4. **S4 前置 agent**：情景说明与引用，由 harness 取原文。
5. **S5 清理**：5.2、5.4 中的 oplog 部分、5.6 的文档。

S1 之后系统就能单独运行；S3、S4 的效果用 S2 的回放逐个对照。

## 10. 评审问题对照

按[设计原则](../../design-principles.md#评审新方案时必须回答)逐条回答：

1. **解决什么已观察到的问题**：token 高、记不住刚刚发生的事和先后顺序（第 1 节）。
2. **现有机制为什么不够**：现有机制本身就是原因——重放日志加拉取阅读。
3. **新增概念**：画面渲染函数、前置 agent、图书管理员、笔记文件。
4. **直接组合能力**：读文件比专用阅读工具更直接；`.py`、link、命令不受影响。
5. **少见需求是否变成固定成本**：前置 agent 和图书管理员是每次唤醒的固定成本，用便宜模型，并由第 8 节的测量决定是否保留。
6. **写入权威**：chatlog 不变；笔记的写入者是图书管理员（维护者也可直接改），保留上一版；待办仍由聊天 agent 写。
7. **坏了从哪里修**：笔记和日志都是普通文件；画面函数可以在 `.py` 里直接调用查看。
8. **保留、迁移、删除**：见第 5 节。
9. **能否先做纵向切片**：S1 单独可运行、可验证。
10. **删除新抽象会失去什么**：删掉前置 agent，就只剩当前窗口的画面；删掉图书管理员，就退回只靠待办、写入缺口重新出现。
