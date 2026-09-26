# 大文件拆分：先搬家，再简化

> **状态：计划，未实施。** 本文不是现行合同；当前行为以[交互模型](../../interaction-model.md)、
> [运行架构](../../architecture.md)和代码为准。它和[中心 reader 与事件流的低熵收束](reader-low-entropy-refactor.md)
> 处理同一批文件，是另一份供对照审阅的方案，差异写在第 6 节。

## 1. 一句话

先**只搬家、不改行为**：把 `chat.py` 拆成 `mods/chat/` 文件夹里的五个文件，`oplog.py`、`chatlog.py`
各切一刀，`meta.py` 里的阅读逻辑挪回 chat。等文件好读了，再**单独**讨论两处真正的逻辑简化
（未读存了两份；oplog 有的检查在写盘之后才做），每处由维护者单独拍板。

## 2. 现状：哪些文件变大了

| 文件 | 09-20 仓库起点 | 09-25 中心 reader 前 | 现在 | 新增的主要是 |
|---|---:|---:|---:|---|
| `mods/chat.py` | 1350 | 1604 | 2800 | 未读、`take`／`mark_read`、补回信源、来源桥；从 oplog 重建历史 |
| `mods/oplog.py` | 346 | 570 | 1308 | 到达、未读、信源、覆盖等记录，以及它们的重放规则 |
| `mods/chatlog.py` | 994 | 1084 | 1566 | 按日档案的范围查询与 SQLite 索引、补回 sidecar |
| `mods/tools/meta.py` | 377 | 501 | 824 | `take`／`status`／`fetch`／`mark_read` 等阅读工具 |
| `mods/context.py` | 254 | 380 | 466 | `Mailbox`：内存里的一份未读 |

`browser.py`（1532）、`tools/__init__.py`（1134）、`llm/__init__.py`（1011）也大，但这段时间没有长，见 4.5。

还有一个比行数更说明问题的数字：不算仓库起点那次整体导入，改过 `meta.py` 的 15 次提交里，14 次同时改了
`chat.py`，12 次三者（加上 `oplog.py`）一起改；09-24 以来改 `context.py` 的 8 次提交，每次都同时改了
`chat.py` 和 `oplog.py`。所以这里其实有两个问题：

1. **文件太大，难找。** 拆文件能解决。
2. **“读消息”这一件事散在四个文件里，难改。** 拆文件解决不了，要靠 4.3 节和第 5 节。

## 3. 我对旧代码设计哲学的理解

依据是 [设计原则](../../design-principles.md)、[运行架构](../../architecture.md)、
[`AGENTS.md`](../../../AGENTS.md) 和几个老模块（`cave.py`、`later.py`、`link/`）的写法。

- **一个功能一个文件，文件名就是入口。** 打开 `cave.py` 就能看到回声洞的全部：存储、抽取、命令。
  人是按“功能叫什么”去找代码的，不是按“这是哪一层”。
- **普通 dict 加普通函数。** 函数能被命令、`.py`、link、LLM 工具直接调用，所以模块上的公开名字就是 API，
  哪怕仓库里 grep 不到调用者（`chat.cond` 的 `WHY:` 正是这么说的）。
- **状态住在用它的模块里**，存在 storage；没有统一的状态系统。
- **复杂度要由已经发生的问题来买单。** “这个概念很重要”不等于“要给它建一层”。
- **能现场看、现场改、现场修。** 文件、日志、终端输出都是维护界面。
- **理由写在代码旁边。** `WHY:` 保护的形状不清理；`WHY?:` 不猜。
- **import 没有副作用，加载顺序是声明出来的**（`PHASE`／`LOAD_AFTER`），不靠 import 语句的先后。

落到拆分上，就是下面这些规则：

1. 先只搬家。每个函数原样挪到新文件。要用同一文件夹里别的子文件的函数，就在文件头
   `from .view import 名字`，函数体不用改；要用 `__init__.py` 里的东西，函数体里写成 `chat.名字`，
   这是函数体唯一允许的改动。后者即使是从不重新赋值的函数也这样写：子文件被 import 时
   `__init__.py` 还没执行完，提前取名字就会依赖 `__init__.py` 里语句的先后。
2. 按“人会去哪里找”切，不按层切；不引入 Manager、Session、DTO、Repository。
3. **对外名字一个不少**：今天 `chat.X`（X 不以下划线开头）能用的，拆完仍能用。
4. **不新增公开模块**，一个模块拆成多个文件就用文件夹（`llm/`、`image/`、`tools/` 已经是这样）。
5. **会被重新赋值的全局量只住一处。** `settings`、`prompts`、`chat_groups`、`description_cache`、`llm_config`
   在 `on_load` 里重新赋值，`experiments/memory_replay.py` 还会直接改写它们来做隔离回放
   （第 500–559 行、第 707 行）。如果子文件用 `from mods.chat import prompts` 把它们绑到自己名下，
   回放时子文件拿到的仍是真实提示词和配置，**隔离会悄悄失效**。所以它们留在 `__init__.py`，
   子文件一律在调用时写 `chat.prompts`。`chatlog.rootfile` 也被同一个实验改写（第 511 行），同样处理。
   从不重新赋值的对象（如 `_offline_scope` 这个 ContextVar）放哪都行。
6. `WHY:` 跟着它说明的代码走，条数不变（`chat.py` 现有 64 条）。
7. 不改 import 顺序、`LOAD_AFTER`、`PHASE`、`@capture`／`@command` 所在的文件；`chat/__init__.py`
   里原来那行 `from mods import ...` 原样保留，哪怕其中几个名字已不在 `__init__` 里用到——它决定了
   其他模块首次被 import 的先后。
8. 不碰磁盘上的任何数据格式。

## 4. 第一阶段：只搬家

### 4.1 `chat.py` → `mods/chat/` 五个文件

| 文件 | 回答什么问题 | 放什么 | 约行数 |
|---|---|---|---:|
| `__init__.py` | 什么时候叫醒 agent；设置存在哪；`.chat` 单句怎么跑 | `capture_chat`、`activation_signal`、`cond`／`call`、`record_event`；窗口与全局设置的读取、计费；`#hint` 配置合并、工具激活名单存取；`.chat` 的 `run`、`chat()`、`on_load` | 650 |
| `view.py` | 一条消息、一次输出、一批工具结果、一条通知在模型眼里长什么样；按预算能看到哪些 | `msg2chat`／`event2chat`／`_model_event`、O/R/通知的文字形式、`_stream_rows`／`get_msgs`／`build_context`、token 计数、“这条消息对应哪个事件号”的对照 | 570 |
| `reader.py` | 现在有哪些未读；`take`／`mark_read`／`read_messages` 怎样把消息正式读进来；补回信源 | `unread_details`、`_pending_hint`、成员遍历、来源桥、`_take_members`／`_take_archive`、`mark_*`、`prepare_recovery_sources`／`fetch_remote_source` | 750 |
| `agent.py` | 中心 agent 的一次激活怎么跑 | `init_chat`／`_activate_chat`、`_drive_agent`／`_run_agent`／`_agent_provider`、O/R 登记回调、自言自语与计费回调、结束时的 `#hint` 求值 | 490 |
| `subcommands.py` | 群里的 `#model`、`#limit`、`#hint` 等设置命令 | `_SUBCOMMAND_HELP`、`_subcommand` 和它调用的各个子命令函数 | 470 |

行数取自 4.7 的试拆产物，含空行和文件头。总数比原来多约 130 行，是四个新文件头和对外名字清单；
正式提交时删掉用不到的 import 会少一些。

调用方向（已用脚本核对，没有环）：`view` 不调用另外三个文件；`reader` 只调 `view`；`agent` 调 `view` 和 `reader`；
`subcommands` 只调 `view`；设置和全局量都在调用时经 `chat.名字` 读取。子文件在 import 时不读 `chat` 的任何属性。

**为什么用文件夹，而不是新建一个公开的 `mods/reader.py`：** 公开模块会进入 `.py`／link 的共享环境，成为一个新名字
（可能和 `data/pyload.py` 里的名字冲突，而 pyload 不在版本库里，查不到）；它还有自己的加载阶段和失败隔离，
而“reader 加载失败、chat 加载成功”是一个没有意义的状态。文件夹内部的文件没有这些问题。
以后 reader 真的有了 chat 以外的使用者，再把它升格成模块，那只是一次 `git mv`。

**`__init__.py` 对外名字清单：** 把子文件里所有不带下划线的名字在 `__init__.py` 重新导出（`count_tokens`、
`bounded_excerpt`、`msg2chat`、`event2chat`、`parse_target`、`get_msgs`、`build_context`、`context_usage`、
`unread_details`、`unread_members`、`mark_window_read`、`mark_source_read`、`fetch_remote_source`、
`prepare_recovery_sources`、`init_chat`、`get_handler`、`MAX_PULL_EVENTS`、`MAIL_PULL_TOKENS`、`NOTICE_TOKENS` 等）。
这份清单本身就是“chat 对外提供什么”的说明。带下划线的名字不重新导出，仓库里的调用方改成新位置：

- `mods/tools/meta.py`：`chat._iter_unread_metadata` → `chat.reader._iter_unread_metadata` 等约 10 处；
- `mods/tools/agents.py`：`chat._stream_results` → `chat.agent._stream_results`；
- `experiments/memory_replay.py`：`chat._drive_agent` → `chat.agent._drive_agent`
  （`chat._offline_scope` 仍可用，是同一个对象）；
- 文档和注释里写着 `mods/chat.py` 或 `chat._xxx` 的地方同步改路径。

**分两次提交：** 先 `git mv mods/chat.py mods/chat/__init__.py`，什么都不改；再把函数搬出去。
这样 `git log --follow` 能接上历史，第二次提交的 diff 也只剩“搬走了什么”。

### 4.2 `oplog.py` → `mods/oplog/` 两个文件

只切一刀：把**重放规则**搬进 `replay.py`。它们是纯函数，本来就把所有索引作为参数传入，
除了两个常量（`AGENT_WINDOW`、`_REFERENCE`）不读任何模块状态，可以一字不改地挪走：`_apply`、`_validate_*`、
`_reference_candidates`、`_message_identity`、`_input_message_identity`、`_source_positions`、`_accessible`，
共约 330 行。两个常量随之搬过去，`AGENT_WINDOW` 在 `__init__.py` 重新导出（别处都写 `oplog.AGENT_WINDOW`）。

`__init__.py` 留下文件读写、锁、崩溃残尾恢复和全部对外函数，约 980 行。
拆完以后，“日志里每种记录重放时怎样改内存”在一个文件里从头读到尾；“对外怎么查、怎么写”在另一个文件里。

`_apply` 的 17 个参数不在这一步动。

### 4.3 `meta.py` 瘦身：工具只留“参数检查 + 给模型看的说明”

这一步仍然不改行为，但不是纯搬运，要单独一个提交。

现在 `meta.py` 里的 `_take` 自己遍历未读成员、按来源顺序排序，并亲手拼出 `requested_reads` 里那种请求 dict；
`status` 自己汇总窗口状态；`cover_events` 调 chat 的私有函数去删会话里的消息。这些都是 chat 内部的事，
所以每改一次阅读逻辑都要同时改 `meta.py`（第 2 节的数字）。

改法：

- 这些逻辑整段挪进 `chat/reader.py`（`cover_events` 那几行挪进 `chat/agent.py`），各自成为一个普通函数，
  返回给模型看的字符串；
- 请求 dict 只由 `reader.py` 创建，也只由 `reader.py` 消费；
- `meta.py` 的每个阅读工具只剩：函数签名、docstring、“只有中心 reader 可以……”那条检查、一次调用。

工具名、参数、返回文字都不变，所以模型看到的完全一样，oplog 里的 `read_via` 也不变。
完成的标志很好检查：`meta.py` 里不再出现 `chat._` 开头的调用。预计 `meta.py` 从 824 行降到约 650 行，
剩下的大部分是给模型看的说明书。

### 4.4 `chatlog.py` → `mods/chatlog/` 两个文件（优先级最低）

写入、格式化、`parse_log` 和目录布局（`window_path` 等）是[记录格式协议](../../chatlog-format.md)，留在
`__init__.py`；按日档案的查询（`read_range`、`read_origin`、`read_around`）、它们背后的 SQLite 索引，
以及启动时从档案重建近期窗口的 `_restore_history`，搬进 `archive.py`，约 500 行。
这样“改协议”和“改查询加速”落在不同文件里。

这一刀比 chat 乱：档案查询要调用协议函数去解析（`parse_log`、`recall_key`、窗口锁），这个方向是自然的；
但 `on_load` 和 `freeze_boot_anchors` 又要反过来调用档案这一半。共享的模块状态跟着写它的代码走：
`_append_lock`、`_boot_anchors`、`_recalls` 以及碰它们的函数（含 `recalled_ids`）留在 `__init__.py`；
`rootfile` 按规则 5 留在 `__init__.py`，`archive.py` 调用时读 `chatlog.rootfile`。
收益也最小，所以排最后；维护者觉得 1566 行的 chatlog 读起来还行，就不切。

### 4.5 不拆的大文件

- `browser.py`：单一能力，从上往下读就是“起浏览器 → 开页面 → 操作 → 截图 → cookie”，没人因为它难找而出错。
- `tools/__init__.py`：文件开头已经写明它是 GPT 迁移来的、形状未经维护者裁决。它需要的是
  “这层机制解决了哪个问题”的讨论，而不是拆文件；拆了反而让那场讨论更难做。
- `llm/__init__.py`：主要是 `LLMClient` 一个类。

行数本身不是理由（设计原则：简单不等于文件少或代码短）。等哪天真的因为它们找不到东西、改错东西，再说。

### 4.6 怎样证明“只搬了家”

都在仓库外用一次性脚本做，不新增测试：

1. `uv run --frozen python run.py --check` 通过，公开模块数仍是 84（拆成文件夹不改变模块数）。
2. `uv run --frozen python run.py --smoke` 仍是 82/84，可选失败仍只有 `mcf`、`minecraft`
   （这是本机今天的基线；在设备上请以同机改前结果为准）。
3. AST 对比：旧文件里每个顶层函数、类和赋值，在新文件里恰好出现一次，AST 相同；
   唯一允许的差异是规则 1 说的 `chat.` 前缀，脚本把它们逐条列出来给人看。
4. 旧 `chat` 上每个不带下划线的名字，新 `chat` 上都还在，而且是同一个对象。
5. 仓库里所有 `chat._xxx`、`chat.reader._xxx` 这类引用都能解析到函数（纯 import 检查，不启动 Bot）。
6. `WHY:` 条数不变。

第一阶段每一步都能单独 `git revert`，不涉及任何运行数据。部署仍需完整重启，由维护者决定时机。

### 4.7 试拆结果（2026-09-26，仓库外，已丢弃）

为了确认 4.1 行得通，我按第 3 节的规则写了一次性脚本，在仓库外的副本里把 `chat.py` 自动拆成五个文件，
并改了 `meta.py`、`tools/agents.py`、`experiments/memory_replay.py` 里的私有引用。结果：

- `--check` 通过：123 个 Python 文件（多出四个），公开模块仍是 84；
- `--smoke` 与改前相同：82/84，可选失败只有 `mcf`、`minecraft`；
- 旧文件 163 个顶层定义在新文件里各出现一次，AST 全部相同，其中 26 个函数只多了 `chat.` 前缀；
- `WHY:` 64 条 → 64 条；
- 旧 `chat` 上 93 个公开名字全部还在；
- 仓库里 82 处 `chat.xxx` 引用都能解析，只有两处**注释**里的旧路径没跟着改
  （`llm/__init__.py`、`tools/__init__.py`），正式提交时一并改。

没有发现第 3 节规则以外的新隐患。这只证明“搬得动、没搬坏”，不代替正式提交时重新跑一遍 4.6。

## 5. 第二阶段：两处真正的逻辑简化（各自单独拍板）

这两处会改变代码的行为路径，不属于“拆文件”。放在搬家之后，是因为到那时每处改动只落在一两个小文件里，
审查的人读得完。

### 5.1 oplog：写盘之前把检查做完

**现在：** 写一条记录分两步，`_append` 先检查、写盘、fsync，再调 `_apply` 更新内存。检查写了两遍，而且两遍不一样。
例如 `source_reopen` 的“正在拉取或已经读过的信源不能再扩展”只在 `_apply` 里，也就是**写盘之后**才检查。

**后果：** 一旦这类检查失败，磁盘上已经有了这一行，内存却没更新；下次启动重放到这一行会同样失败，
而按现有 `WHY:`，完整的坏行不允许跳过——于是 oplog 起不来，chat 也跟着起不来，只能人工修文件。

**目前没有出过事**：我读到的触发路径都被调用方事先挡住了（比如 `fetch_remote_source` 先看过信源状态）。
但挡在调用方，而不是在 oplog 自己手里。

**改法：** 把 `_apply` 里所有会失败的检查收进一个 `_check(记录, 当前索引)`；`_append` 在写盘前调它，
启动重放时也调它；`_apply` 只剩“改索引”，不再失败。不改磁盘格式。拆完 4.2 之后，这两个函数就在同一个小文件里。

### 5.2 删掉 `Mailbox`：未读只记一份

**现在：** “哪些消息到了还没读”记了两份：oplog（磁盘，重启以它为准）和 `context.Mailbox`（内存，按窗口）。
每种读法都要“先写 oplog，再同步 Mailbox”，而且有三种不同的同步方法（`absorb`、`pull`、`commit_recovered`），
外加跳过已读洞、修剪、别名表等辅助。第 2 节里那 8 次同时改 `context.py` 的提交，有 6 次是在增改这几种同步方法。
这份镜像是先后顺序造成的：Mailbox 先有，oplog 的持久未读是 09-24 以后才加的。
维护者在 [mail 与激活](mail-and-activation.md) 里裁决过同类问题：“内存本身是不稳定的”，不配当权威。

**Mailbox 真正提供、oplog 不提供的只有两样：**

1. 每个窗口一把锁，让“写 chatlog + 登记到达”对读取的一方来说是一步；
2. 让 `capture_chat` 找到“路由刚登记的这条事件对应哪个到达号”（路由不许往事件 dict 里塞键）。

这两样各用十几行就能保留：一个“窗口 → 锁”的字典，一个有上限的“事件对象 → 到达号”对照表。
其余所有未读查询都改问 oplog。

**收益：** 删掉约 230 行和三种同步方法；`context.py` 回到“当前事件、等待输入、轮”三件事；
以后改阅读逻辑只动 `chat/reader.py` 和 `oplog`。

**风险与验证：** 它碰的是并发和重启恢复路径，而且要一次性切换所有消费者，不能留两份并存的中间版本。
验证用仓库外的差分脚本：同一串合成事件（普通消息、@、补回与实时重合、`mark_read` 后再到达、取消）
分别喂给旧代码和新代码，比对 oplog 写出的行和模型收到的消息，全程在临时运行目录里，不碰真实数据。

## 6. 和《低熵收束》计划的异同

**同意的：** 未读应当只由 oplog 记；oplog 应当写盘前检查完；`meta` 不该知道 reader 的内部；
不引入 Session、Manager、DTO；独立 `.chat`、`pull`、来源桥、原生 O/R 都不在这次删除。

**不同的：**

| | 《低熵收束》 | 本计划 |
|---|---|---|
| 顺序 | 先改语义（写入校验、删 Mailbox），文件结构随之形成 | 先纯搬家，能机械证明没改行为；语义改动排在后面、逐项拍板 |
| reader 放哪 | 新的公开模块 `mods/reader.py` | `mods/chat/` 文件夹里的 `reader.py`，不新增公开名字和加载单元 |
| 阶段 D（provenance 改新格式、新旧同读） | 做 | **不做。** 旧日志不重写，来源桥和 `recall_events` 又能引用任意久远的旧 input，所以“标记烘进投影”的旧格式和识别它的代码要永远留着；改新格式不会让代码少认一种格式，只会多认一种。要减少的是代码重复：三条读取路径（补回、实时、档案）各自“正常投影一次、超长降级再补一次标记”，这用一个普通函数就能合并，不必动磁盘格式 |
| 长消息片段与来源桥的语义问题 | 作为阶段 D 的前置裁决 | 这是产品问题，和拆文件无关；建议记进[当前维护队列](../current-issues.md)单独讨论 |

## 7. 提交顺序

| # | 内容 | 改行为吗 | 依赖 |
|---|---|---|---|
| 1 | `git mv mods/chat.py mods/chat/__init__.py` | 否 | — |
| 2 | 拆出 `view`／`reader`／`agent`／`subcommands`，更新仓库内调用方与文档路径 | 否 | 1 |
| 3 | `oplog` 拆出 `replay.py` | 否 | — |
| 4 | `meta.py` 阅读逻辑挪进 `chat/reader.py`、`chat/agent.py` | 否（模型所见不变） | 2 |
| 5 | `chatlog` 拆出 `archive.py` | 否 | — |
| 6 | oplog 写盘前检查（5.1） | 是：非法记录改为不落盘 | 3，需拍板 |
| 7 | 删除 Mailbox（5.2） | 是：未读只由 oplog 回答 | 2、4，需拍板 |

1–5 每步都跑第 4.6 节的检查；6、7 另加 5.1／5.2 里说的验证。

## 8. 需要维护者回答的问题

1. `chat` 改成文件夹、公开名字全部保留，可以吗？
2. 五个文件的名字（`view`、`reader`、`agent`、`subcommands`）顺手吗？换成什么更好找？
3. `oplog`、`chatlog` 各切一刀，要不要做？
4. 第二阶段的 5.1、5.2 做不做，先做哪个？
5. 《低熵收束》的阶段 D（provenance 新格式）是否放弃？
