# 中心 agent 重写：经历日志、视图与内存里的注意力

> **已被后续规格替代（2026-10-31）**：[经历日志、视图与上下文运行](../tasks/chat-core-rewrite/spec.md)。本文是实施前提案；其中短期规则、工具激活与切片部署等选择以最终规格裁定为准。 现行行为见[LLM 文档](../../llm.md)，本轮代码交付不表示生产迁移已经执行。


> 状态：提案，未实现。2026-10-31 维护者要求写一份“足够激进”的方案交给 Codex 评审：不考虑兼容，旧数据有问题就跑一次性转换；维护者没表态的地方按作者（Claude）的偏好写，需要拍板的集中在[第 8 节](#8-需要维护者拍板)。
>
> 维护者已经表态：消息带编号（本文 3.1 的形式更彻底）、“读到哪”只留一份、oplog 那套“写不进就全局停手”可以拿掉；loop 和 wait **不落盘**，重启就丢。
>
> 现行行为以 [LLM 文档](../../llm.md)、[交互模型](../../interaction-model.md)为准；评审标准见[设计原则](../../design-principles.md)。

## 1. 为什么要动到根上

前一轮提的修法（记号挪进消息、三份读位置合一、唤醒原语合一）都对，但都是在现有形状上修。它们背后是三个更根本的问题。

### 1.1 “发生过什么”和“现在看什么”是同一份可变列表

`data/context/current.json` 里的 `messages` 既是她这一段的经历记录，又是下一次请求的上下文。改上下文的操作有五种：dismiss、整轮 compress、中段 compress、子步骤 compress、decompress。每种都在原地改这份列表，于是：

- **要另存一份 `marks`**：记着下标和 6 项状态快照，用来回答“哪里是一次唤醒”“那时给过什么”。每次改列表都要挪下标、回滚快照。
  - `_fold_since`、`_fold_between`、`_fold_slice` 三套各做一遍，而且做得不一样：收子步骤时不退回区间里给过的笔记，收中段时会退。
  - decompress 遇到没存记号的归档，用正则从正文里把记号拼回来。
  - 10-29 审查发现的“跨唤醒子步骤弄丢记号”、d5de90f 修的 KeyError，都出在这里。
- **派生状态被单独存**：给过哪些笔记（`scopes`）、工具状态（`told`）、各窗口给到哪（`last_shown`），本来都能从列表推出来，却单独存着，成了第二个写入权威。
- **同一件事存了三四份**：被收走的原文另存成 `archive/*-folded.json`；她的输出和结果在 oplog 里又存一份；反思再存一份 `reflect` 归档。

### 1.2 每种上下文都是手搭的，目的散在各处

- **会话各搭各的**：主唤醒、交接、反思走 `agent._run_session`；旁观（`observer._do_observe`）、月记年记（`roles.summarize`）、分身（`tools/agents.assign_tasks`）各自 `llm.Chat(...)` 再搭一遍。
- **目的和组装都写死在代码里**：每个上下文“为了什么”写在各自的提示词里，主唤醒的组装顺序写死在 `_run_agent`。
- **她只能往槽里填字**：compress 摘要、`recent`、loop 备忘、send/dismiss 的 wait 理由、wake_me 理由、assign_tasks 的 prompt，一共六个槽。分身看不到她眼前的任何东西，背景只能靠她重新打一遍字。

### 1.3 “叫醒”“读到哪”“这一轮结束”都各有好几份

- **叫醒有六处**：oplog 的到达/激活/通知、awaker 文件规则、loop 的内存 `threading.Timer`、防抖计时、`push` 起的线程、`pick_up_pending`。
- **读到哪有三份**：oplog 的未读、storage 的 `seen`（精确到秒）、这一段的 `last_shown`（记原文，因为按秒会把同一秒后到的当成看过）。dismiss 回滚不了 oplog 那份，`_drop_since` 的注释自己承认未读数会少算。
- **结束这一轮靠换回调**：7 处 `session.turn_done = lambda: True`，把 `_run_session` 装的回调整个换掉。
- **已经出了 bug**：`#loop` 总开关开着时（默认开），在循环外调 `wait(600, "理由")` 走的是循环计时器。到点她看到的是“你上一轮没留下要做什么”，理由丢了，重启也会丢。这是读代码得出的，没跑脚本验证。

## 2. 判断依据

按设计原则的理解闸门，压成四条。

**优化目标。** 少量透明的机制，现场能看懂、能修。她的记录是人能 grep 的文件。

**必须保留的用户可见行为：**

- 默认叫醒判据：at、名字开头、私聊对方每条、戳一戳。`#` 子命令就地执行，不进上下文。`^C` 打断这一轮，并杀掉它起的子进程。
- 同一段上下文一直往后追加，直到交接（[D20](stateless-agent-loop.md#72-同一段上下文与交接2026-10-22)）。交接成功的判据（最后一条回复不再调工具）、失败计数、强制归档都保留。
- dismiss 的三个拦截照旧：这期间发过话、输入框里有新字、follow 积压超过 60 条。清掉的消息退回未读。
- compress 的 `#n` 和 `n.m` 两种地址、decompress、白醒自动 dismiss 都保留。
- 私聊“正在输入”；send 按空行拆成几条发，wait 的三种取值。
- 夜里 4 点反思；旁观助手；面包屑。
- 笔记是她能直接改的普通文件；`data/notes/awaker.md` 仍是她的长期闹钟。
- `chat.wake()`、`chat.push()` 的签名不变，`.py` 和 link 里的调用照常能用。

**保留的小机制：** 普通 dict 事件、`msgs.body()` 投影、从函数签名生成工具、tool 与 skill 二合一、用 `.py` 共享环境求值的 tidy 条件（D17）、完整重启、WHY 注释。

**明确删掉的历史偶然：**

- 记号和快照、三套折叠；
- oplog 整套，它是为已经推翻的 reader 重放模型建的；
- loop 的独立计时器；
- 把会话对象当口袋；
- “潜意识”这个名字，它现在只剩一个增长提醒。

## 3. 目标形态

一句话：**给她看过的每条消息只往经历日志里写一次；上下文是一串条目编号；所有模型调用都是“拿一串编号加一个目的跑一次”。**

### 3.1 经历日志（journal）

`data/chat/journal/YYYYMMDD.jsonl`，只追加。每行一个条目：

```json
{"id": "20261031-42", "t": 1761900000.5, "ctx": "v20261031-3", "kind": "act", "messages": [...]}
```

- **`id`**：沿用 oplog 的正式号形状 `YYYYMMDD-N`，她笔记里已有的引用不失效。编号里带日期，按编号就能直接找到那天的文件。启动时不再重放全部历史，只读今天文件里的最大号。
- **`ctx`**：这条属于哪个上下文，可以是主视图、某次交接、反思、旁观或分身。
- **`messages`**：原样的模型消息，包括原生 assistant/tool 配对和思考内容。渲染时直接拿来用，不重新拼字。

条目种类：

| kind | 存什么 | 渲染成 |
|---|---|---|
| `wake` | 唤醒序号 `n`、原因、谁叫的、此刻各窗口读位置的快照、输入框快照 | system：“这次唤醒你的是（#n · 时间）…” |
| `screen` | 窗口、渲染好的画面文字、这块画面读到的位置 | user |
| `note` | 笔记名、当时渲染出的全文 | system |
| `system` | `what` 字段说明是什么：目录、年表、工具状态、通知、交接提醒、输入框提醒、旁观结果…… | system |
| `act` | 一次输出：一条 assistant 消息，加上它的全部 tool 结果 | assistant + tool |
| `fold` | 摘要、`covers`（被收掉的条目编号）、层级 | system：“【收起 Ln】…” |
| `drop` | 被 dismiss 的条目编号 | 不渲染 |

前一轮说的“消息带私有字段”在这里变得更彻底：编号和快照都在条目上，渲染出来的消息是干净的，发请求前也不用剥字段。

**写失败怎么办：** 先写日志，写成功了才把编号加进视图，所以视图里不会出现指向不存在条目的编号。写失败时，这次唤醒失败、视图不变、终端报错，下次唤醒照常再试；不会像 oplog 的 `PersistenceError` 那样全局停手。

### 3.2 视图（view）

`data/chat/view.json`：

```json
{"ctx": "v20261031-3", "started": 1761890000,
 "items": ["20261031-1", "20261031-2", "..."],
 "next_n": 13, "tokens": [182345, 57], "grace": 0, "handoff_failures": 0, "settled": 150000,
 "last_sent": 1761899000, "tools": {"active": {"bili": 1761899000}, "schema": ["bili"]}}
```

- **渲染**：`render(view)` 按顺序取出条目，拼成 messages。条目内容按编号缓存在内存里。`#reasoning drop` 在渲染时去掉早先唤醒的思考，不改数据。截短 compress 调用里的 summary 参数（原 `_truncate_compress_call`）也改在渲染时做，`act` 条目本身保持原样。
- **能推出来的就不存**：
  - 给过哪些笔记：看视图里的 `note` 条目；
  - 看过哪些窗口：看 `screen` 条目；
  - 能收的唤醒和步骤：`wake` 条目，以及它们之间的 `act` 条目（第 m 个就是 `n.m`）；
  - 工具状态已经告诉到哪：看视图里最后一条工具状态。
- **token 数**：`tokens` 仍是“接口上次报的数 + 报到第几条”。任何改到那一条之前的编辑都让它作废，改回按字数估，和现在一样。
- **前缀缓存**：平时只往后追加；只有她明确收、展开、清或交接时才改中间。
- **归档**：交接后旧视图存成 `data/chat/views/<ctx>.json`，里面只有编号列表、原因和起止时间；`read_context(<ctx>)` 把它渲染出来。

### 3.3 视图上的操作

全都是对编号列表的操作，日志一个字不改。

- **清（dismiss）**：
  - 找到 `wake` 条目 `#n`，把它之后的编号截掉；按它存的读位置快照，把这期间看过的窗口退回未读。
  - 三个拦截照旧，判据都从条目里算。白醒自动清照旧。
  - 日志里追加一条 `drop`，被清掉的条目仍然能搜到、能在行动记录里查到。
- **收（compress）**：只有一个 `fold(view, first, last, summary)`。
  - `#n` 展开成“从 wake n 到下一个 wake 之前”，`n.m` 展开成第 m 个 `act`；跨唤醒的子步骤照样拒绝。
  - 写一条 `fold` 条目，用它的编号替换整个区间。
  - 区间里给过的笔记和工具状态随之从视图消失，下次组装时按推导结果自然补上。三套回滚逻辑不再需要。
- **展开（decompress）**：把 `fold` 编号换回它的 `covers`，可以嵌套。不再需要拼记号。
- **交接（tidy）**：跑一次 `run(视图 + 交接提示, goal=交接)`。成功判据、失败计数、强制归档、每次唤醒最多 3 次交接，这些逻辑和它们的 WHY 原样搬过来。成功后归档视图，开一个新视图。

### 3.4 一个执行原语：`run`

```python
run(items, goal, *, tools=(), model=None, kind="fork", view=None, stop=None, timeout=None) -> tuple[str, str]
```

- **请求内容**：系统提示 + `items` 渲染出的消息 + 最后一条写明目的的 system（“这次要做的是：……”）。
- **记录**：产出的每个 `act` 都写进日志，`ctx` 记为这次运行；返回最后一段文字和 ctx 名。
- **只写一次的东西**：计费（`_charge`）、工具绑定、`^C`、超时，都只在这里写一次。

现在的各处调用改成：

| 现在 | 之后 |
|---|---|
| 主唤醒 `_run_session` | `run(视图 + 本次材料, goal=唤醒原因, view=view)` |
| 交接 `_handoff` | `run(视图 + 交接提示, goal=交接, tools=memory_system)` |
| 反思 `_run_reflection` | `run(笔记 + 当天归档的视图列表 + 反思提示, goal=反思)` |
| 旁观 `observer._do_observe` | `run(上一轮的条目 + 新材料, goal=旁观提示, timeout=15)`，不给工具 |
| 月记年记 `roles.summarize` | `run(日记条目, goal=汇总)` |
| 分身 `assign_tasks` | `fork` 工具，见下 |

**`fork(goal, refs="", tasks="", tools="", model="")` 取代 `assign_tasks`。**

- `refs` 每行一个引用：`#12`、`12.3`、笔记名、归档的 ctx 名或日志编号。这些条目直接放进分身的视图，不用她再打一遍字。
- `tasks` 有值时照旧按行并发，最多 5 个。
- 分身的经历也写进日志，她能用 `read_context` 看分身是怎么做的。
- 分身默认拿不到 `send` 和 `type_word`（维护者 2026-10-31 定：分身是帮她想事的）；要让它说话，就在 `tools` 里显式给。这条答掉了 `tools/meta.py` 里 send 那条 `WHY?`，实施时把它改写成 `WHY:`。

**新视图的开头由她决定。**

- 开头带什么写在 `data/notes/policy/opening` 里：普通文本，一行一个引用。默认内容就是现在 `_head` 写死的那几样：global、recent、最近 7 篇日记、年表、目录。
- 交接提示里告诉她这份文件在哪，她可以在交接时改。下一段从什么开始，由上一段的她写下来。这就是讨论里“组装上下文的也是上下文”的第一步。
- 人和群的笔记照旧在首次出现时自动给（每次最多 20 份），面包屑也照旧。目录只列 60 份、人和群笔记每次最多 20 份这两处截断，要在给她看的文字里明说。

### 3.5 读到哪

storage 里只留一个键 `read`：

```json
{"g123": [1761900000, ["mid1", "mid2"]]}
```

值是最后看到那条的时间（秒），以及这一秒里已经看过的消息号。

- **未读**：chatlog 里排在这个位置之后的记录。离线补回会按时间插进日档，只要时间晚于这个位置就算未读，不受行号变化影响。
- **只动这一份**：画面、迟到消息、标已读、dismiss 退回都只改它。`seen`、`last_shown`、oplog 的未读全部删掉。
- **未读计数**：每个窗口的计数存在内存里，来一条加一、标已读清零，启动时从 chatlog 重算。它只是缓存，权威是 chatlog 加上这个位置。

### 3.6 注意力：全在内存

新文件 `mods/chat/attention.py` 收拢以下这些：

- 覆盖层：follow/mute，带到期时刻和起点；
- 等待：哪个窗口（可以只等某个作者）、到期时刻、理由；来源包括 send/dismiss 的 wait、`wake_me("wait …")` 和 `wake_me("on g…")`；
- 循环：备忘、转了几轮、下一轮时刻、空转计数；
- 防抖会话；
- 待处理的唤醒队列。

运作方式：

- **计时**：一个计时线程，睡到最早到期的那一项，精确到秒；状态一改就重算。loop 两轮之间的 2 秒、`wait(30)` 都走它。
- **消息到达**：`on_record(window, event)` 在 chatlog 写完后调用，决定叫不叫醒、要不要防抖，然后入队并启动这一轮。它替代 `record_event` 里的 oplog 登记和 `capture_chat` 的后半段。
- **取队列**：每次唤醒开始时、每两次子请求之间取走队列，渲染成“这次唤醒你的是”和迟到消息。它替代 oplog 的通知和 `late_arrivals` 里的 oplog 查询。
- **重启即清空**：这是维护者 2026-10-31 的决定，loop 和 wait 不落盘，弊大于利。启动时、离线补回之后各调一次 `rescan()`：把每个窗口读位置之后、符合默认判据的记录重新入队，原因写“离线时叫你”。它替代 `pick_up_pending` 和 `_backfill._register_calls`。

和现有入口的关系：

- **awaker.md 只留长期闹钟**：`every`、`daily`、`at`、`when`，仍然精确到分钟、每分钟查一次。
- **`wake_me` 照收全部写法**：短期的进内存，返回里写明“重启会丢”。`wake_me("")` 列出内存里挂着的东西，`#awaker` 把文件和内存两边都列出来。画面上的徽章照旧。
- **`wait` 工具只剩一种行为**：在内存里排一次唤醒，理由原样带上；在循环里就是把下一轮推迟。`#loop` 总开关删掉。

### 3.7 唤醒状态：一个普通 dict

主唤醒期间，`chat.current_wake()` 返回一个普通 dict；不在主唤醒里时返回 None。它装下现在散在 `turn` 上的十几个属性，以及 `session` 上的 `segment`、`quiet`、`dismiss`、`pending_compress`、`pending_decompress`、`boxes`、`last_sent`、`associated_windows` 等。

- **结束这一轮**：工具写 `wake["end"] = "原因"`。`run` 在每轮检查点先存盘、算压力，再看这个标志。7 处替换回调删掉。
- **醒着才能用的工具**：dismiss、compress、loop 等改判 `current_wake() is None`，代替 `getattr(session, "quiet", False)`。
- **`^C` 路由**：`bot.py` 现在读 `associated_windows`，改读 `wake["windows"]`。
- **删掉只写不读的**：`turn.loop_stopped`、`requested_reads`。

### 3.8 工具模块

`tools/__init__.py` 开头已经写明可以整体重写。

**保留：**
- 文件开头列的三条期望：tool 与 skill 二合一；改了能立刻重载并拿到错误栈；meta 是给她的说明书。
- 有 WHY 解释过的取舍：空闲回收放在开局做；`bot_op_only` 控制可见性；已卸载的工具留一个桩，稳住前缀缓存。

**改：**
- `ToolRegistry`、`SessionBinding` 两个类改成模块级的 last-good 字典加几个函数。
- 激活集合和 schema 顺序存进视图的 `tools` 字段，随视图走，交接时重置。现在的 `tool_schema_modules` 是全局持久化的，只增不减。

**删：**
- 按 registry 地址拼包名（注释已经说不必保留）；
- `_deferred`（权限改了本来就要重启）；
- 子会话那条 `_child_provider` 分支（交给 `run` 统一处理）。

Codex 动手前要逐条读这个文件里的 WHY，确认每一项要删的东西都没有被观察到的问题依赖它。

### 3.9 上下文压力：一个函数

`pressure(view, stage)` 返回五种结果之一：
1. 什么都不做；
2. 提醒收一收（增长 5 万 token 时，原“潜意识”）；
3. 提醒交接（还在宽限期内）；
4. 交接；
5. 不交接，直接归档。

“该不该交接”仍由 `data/notes/policy/tidy.py` 判断（D17），其余门槛都集中在这一个函数里。`subconscious` 这个名字、它的开关和 `#subconscious` 删掉，报告并进 `#status`。

### 3.10 给“事后验证”留的口子（最后做，可以不做）

日志记下了每个上下文带了哪些条目、做了什么。讨论里说的“组装得好不好，在下一个上下文用它时会暴露”，于是变成几条查询，而不是一套新系统：
- 收起后又展开了哪些；
- 交接后的头几步回去找了旧视图里的什么；
- 开头带的引用里，哪些从来没被提到过；
- 分身报过缺什么。

可以先做成反思材料里的一段列表，供她夜里看。

## 4. 删除清单

| 现在 | 去向 |
|---|---|
| `mods/oplog/`（到达、激活、通知、输出与结果、正式号计数、`PersistenceError`、`replay.py`）；`llm` 里的 `raise_if_failed` | chatlog、`read`、注意力队列、日志 |
| `segment.marks`、`numbers()`、`last_number`、`MARKED`、`_restore` | `wake` 条目，加上从视图推导 |
| `_fold_since`、`_fold_between`、`_fold_slice`、`_detect_fold_level`、`get_folded_blocks`，以及 `finish_decompress` 拼记号 | `fold` 和 `unfold` 各一个函数 |
| `data/context/current.json`、`archive/*.json`（含 folded 和 reflect） | 日志、`view.json`、`views/` |
| storage 的 `seen`、段里的 `last_shown` | `read` |
| awaker 的 `on`、`wait`、`follow`、`mute` 行，以及 `_since`、`_custom` | `attention`（内存） |
| `agent._loop`、`_next_loop`、`_loop_round`、`wait_loop`、`_start_quietly`、`loop.jsonl`，以及 `loop_note_history`、`clear_loop_note` 两个工具 | `attention` 里的循环；备忘历史改查日志 |
| `chat/debounce.py` | 并入 `attention` |
| `agent.pick_up_pending`、`_backfill._register_calls` 里的 oplog 部分 | `attention.rescan()` |
| 7 处 `turn_done = lambda: True` | `wake["end"]` |
| `turn` 上的十几个属性、`session` 上的口袋属性 | `current_wake()` |
| `_run_session` 之外手搭的 4 处会话 | `run()` |
| `ToolRegistry`、`SessionBinding` 两个类，全局的 `tool_schema_modules` | 几个函数，加上视图里的 `tools` 字段 |
| `subconscious` 的名字、开关和 `#subconscious` | `pressure()`、`#status` |
| 旧公开名 `chat.cond()` | 删除；运行期代码里的引用由转换脚本找出来（第 5 节） |
| `#loop` 总开关 | 删除 |

文件层面：
- **新增**：`mods/chat/journal.py`、`mods/chat/attention.py`。
- **重写**：`segment.py` 名字保留，“一段上下文”是文档里已经用开的说法，改成视图的读写、渲染和操作；读位置并进 `screen.py`。
- **大幅收缩**：`awaker.py`、`agent.py`、`chat/__init__.py`、`tools/meta.py`、`tools/__init__.py`、`tools/agents.py`。

## 5. 数据与一次性转换

转换脚本放在 `scripts/once/`，停掉 Bot 后跑一次，跑完在下一个提交里删掉。

1. **日志**：
   - oplog 里的输出和结果 → `act` 条目，保留原编号。
   - `archive/*.json` → 每份一条 `legacy` 条目，原样存消息，再记一张“归档名 → 编号”的映射表，`read_context` 和 `search` 用它找。
   - `current.json` → 拆成条目并写出 `view.json`：
     - 唤醒头（以“这次唤醒你的是”开头的 system，序号取自 marks）→ `wake`；
     - role 为 user 的画面块 → `screen`；
     - assistant 加上紧随的 tool 消息 → `act`；
     - 带 `-folded` 归档名的收起行 → `fold`，`covers` 指向那份归档的 `legacy` 条目；
     - 其余 system → `system`。
   - **硬检查**：`render(view)` 必须和旧 `current.json` 的 messages 逐条相等（按 JSON 比较），不等就中止。
2. **读位置**：由 storage 的 `seen`、`current.json` 的 `last_shown` 和 chatlog 合出来。时间取 `seen`；那一秒里已看过的消息号，按 `last_shown` 在 chatlog 里定位后得出。
3. **awaker.md**：删掉 `on`、`wait`、`follow`、`mute` 行，删掉的打印出来。
4. **storage**：删掉 `seen`、`tool_schema_modules`、`subconscious`、`loop` 几个键。
5. **旧数据挪走**：oplog 目录、`data/context/` 挪进 `data/legacy/`，代码不再读。
6. **找运行期引用**：在 `data/pyload.py`、link 存储和 `data/hint/*.py` 里搜被删的公开名（`chat.cond`、`oplog.`、`chat.segment.`、`agent.start_loop` 等），把命中的位置打印出来，由维护者改。

## 6. 实施切片

每片做完都能单独部署：

- **A. 日志、视图、读位置**：dismiss、compress、decompress、交接、反思、`read_context`、`search`、`read_actions` 搬到新模型上，转换脚本第 1、2 步跟着做。这一片里 oplog 只剩激活和通知。
- **B. 注意力与唤醒状态**：`attention.py`、`current_wake()`、`wake["end"]`，awaker.md 只留长期规则；删掉 oplog，补回改成调 `rescan()`。转换脚本第 3 到 6 步跟着做。
- **C. `run`、`fork`、开头策略**：各处会话改走 `run`，加 `opening` 文件和目的那一行。
- **D. 工具模块**。
- **E. 文档**：
  - 重写 `llm.md` 里上下文、压缩、旁观、闹钟几节，`architecture.md` 的 chat 部分，`interaction-model.md` 的中心聊天一节；
  - 退休已被取代的提案。候选：`stateless-agent-loop`、`reader-cleanup-and-split`、`agent-simplify-round2`、`chat-client-for-an-agent`、`chat-client-reading`、`mail-and-activation`、`unified-event-stream`、`chat-condense`、`chat-condense-history`、`condense-and-unify-handoff`、`offline-message-backfill`。Codex 逐篇确认现行合同没有只写在那里，再处理。讨论记录类的保留。

## 7. 验证

不加永久测试，脚本都放在仓库外，每片都跑 `run.py --check` 和 `--smoke`，再对照下面几条：

- **A**：
  - 转换的硬检查（第 5 节第 1 步）。
  - 用合成的段，对比新旧 dismiss、compress、decompress 的渲染结果。有意的差异要列出来：收子步骤现在也会退回区间里给过的笔记，旧的 `_fold_slice` 不退。
  - 连续两次唤醒，第二次的渲染结果要以第一次为前缀。
- **B**：
  - 用合成事件流驱动 `on_record`，覆盖这些情况：at、名字开头、私聊、戳一戳、mute 压住、follow、wait 被认领、防抖合并。结果和旧 `capture_chat` 对比。
  - 重启后 `rescan()` 只叫醒读位置之后的点名。
  - 到点时 `wait` 的理由出现在“这次唤醒你的是”里。
- **C**：smoke 里用假模型检查每种 `run` 的请求末尾都有目的那一条；`fork` 的 refs 渲染正确。
- **D**：smoke 里覆盖加载、重载、坏模块报错栈、空闲回收、卸载后留桩。

## 8. 需要维护者拍板

1. **日志格式**：D8 说行动记录要“和 chatlog 同类的可读日志”。可原样回传需要精确的 JSON（工具调用编号、思考内容），所以本文选了 jsonl，可读性交给 `read_actions` 和 `read_context` 渲染。要坚持 D8 的话，得另存一份原样数据。
2. **`opening` 文件**：同不同意让她决定新视图的开头带什么。不同意的话，3.4 的这部分删掉，`_head` 照旧写死。
3. **短期规则全进内存**：维护者只说了 loop 和 wait；本文把 `on`、`follow`、`mute` 也放进了内存，重启后都会丢。
4. **退休的提案**：直接删，还是挪进一个归档目录。

## 9. 评审问题对照

对照[设计原则](../../design-principles.md#评审新方案时必须回答)里的十个提问：

- **解决了什么问题**：第 1 节列的都是已经发生过的，有提交和审查记录。
- **新增了什么概念**：日志条目、视图里的编号列表、`run`、内存里的注意力。被它们取代的概念见第 4 节，删掉的比加进来的多。
- **直接组合能力**：`.py`、link、命令都不动；`chat.wake()`、`chat.push()` 签名不变；日志和视图都是能 grep、能手改的文件。
- **写入权威**：
  - 内容：日志；
  - 当前上下文：`view.json`；
  - 读到哪：chatlog 加 `read`；
  - 长期闹钟：awaker.md；
  - 短期注意力：内存，有意不持久。
- **坏了怎么修**：`view.json` 就是一串编号，可以手改；删掉它就从新视图开始。日志只追加。注意力重启就清空，`rescan()` 会把漏掉的点名补回来。
- **能否先验证一个切片**：可以，见第 6、7 节，A 片单独就能验证核心假设。
- **删掉新抽象会失去什么**：没有日志和视图，所有改上下文的操作又要回到原地改列表、维护下标；没有 `run`，每种上下文又要各自手搭。
