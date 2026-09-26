# 中心 reader 与事件流的低熵收束

> **状态：计划，未实施。** 本文不是当前运行合同。现行行为仍以
> [交互模型](../../interaction-model.md)、[运行架构](../../architecture.md)与
> [LLM 文档](../../llm.md)为准。

本计划处理的不是“文件太长”，而是中心 reader 落地后逐渐出现的协调负担：

- 选择一次未读需要同时理解 `tools/meta`、`chat`、`context.Mailbox`、`oplog` 与 chatlog；
- `Mailbox` 与 `oplog._pending` 同时表达未读，每条消费路径都要写入一边、修补另一边；
- `read_by` / `read_via` 既是 oplog 字段，又被烘入冻结 projection，所有超长消息降级路径都要重复补标记；
- `meta` 直接调用多个 `chat._private` 并构造 `requested_reads`，工具适配层已开始拥有 reader 内部协议；
- `oplog._append` 与 `_apply` 各做一部分校验，某些非法事实可能在 fsync 之后才被 reducer 拒绝；
- 持久投影已成为需要兼容的数据格式。`814cb2f` 修复的旧 notification 快照缺少
  `ordinal` 导致重建失败，就是已经发生的格式漂移。

2026-09-26 基线：

| 文件 | 当前行数 | 中心 reader 前参考 |
|---|---:|---:|
| `mods/chat.py` | 约 2800 | 约 1604 |
| `mods/oplog.py` | 约 1308 | 约 570 |
| `mods/tools/meta.py` | 约 824 | 约 501 |

行数只说明审查范围，不是验收指标。

## 1. 哲学闸门

### 1.1 优化目标

柚子优化的是：用少量透明、可组合、可现场修改的机制获得大表达面；出错时人仍能沿源码、
文件、日志和聊天入口理解与恢复系统。“简单”衡量的是必须同时记住的规则、状态权威和失败路径，
不是文件数、class 数或总行数。

参见[设计原则](../../design-principles.md#优化目标)。

### 1.2 必须保留的行为

1. 主聊天只有一个中心 agent，窗口是消息来源、通知范围和明确发送目标。
2. 通知、未读和正式阅读是三种事实；工具调用只安排阅读，下一 provider 边界才产生 input。
3. `mark_read` 只跳过，不伪造 input。
4. 新 input 保留自己的正式号，`read_by` 指向发起读取的输出，`read_via` 记录实际公开工具名。
5. 稀疏阅读的来源桥、五条以内展开、更多时首尾折叠，以及预算不足时明确报告部分进度，
   是现行契约，不在本重构中暗中删除。
6. O 在派发行动前落盘，同步工具完成后整批登记 R；取消、失败或 `final_call` 不为 R 强制续轮。
7. chatlog 是 QQ 原文权威，oplog 是正式经历、未读与覆盖事实的权威；原号不因重启或视图裁剪改写。
8. 明确命令、取消、权限和完整重启仍是动态能力失败后的恢复入口。

### 1.3 值得保留的小机制

- OneBot 事件、工具请求与索引都继续用普通 dict 和函数表达，不为拆分创建 Reader class、DTO、
  Repository、服务容器或事件总线。
- 一个事实只有一个写入权威，但允许明确的派生索引和冻结投影。
- provider 追加发生过的经历，hint 重算当前状态；两者不合并成“上下文更新器”。
- 窗口锁保护 chatlog 写入与 arrival 的顺序，turn 锁保护唯一 reader 及其收尾交接。这两个事实
  需保留，当前 `Mailbox` 对象不是必须形状。

### 1.4 不在本计划中默认采用的惯例

不默认引入统一 Session、Manager、Repository、DTO、依赖注入、数据库迁移、框架化权限层或
长期新旧双路径。新模块必须通过“删除它后，哪些协调知识会重新散回多个调用者”证明自己有深度。

## 2. 目标权威与责任

| 事实或行为 | 目标权威／负责人 |
|---|---|
| QQ 原文 | chatlog |
| 不可变补回成员顺序 | source page |
| arrival、input、skip、notification、O/R、cover 事实 | oplog JSONL |
| 未读、覆盖、来源成员等快速查询 | 从 oplog 重放得到的内存索引 |
| 一次阅读的选择、提交顺序和预算处理 | reader 普通函数 |
| LLM 装配、O/R 生命周期、provider 顺序、命令与计费 | `chat` |
| 工具签名、说明、权限和适用性检查 | `tools/meta` |

reader 不存另一份持久未读表，也不接管 source 的写入权威。

## 3. 目标 reader 边界

建议建立 `mods/reader.py`，只回答一个完整问题：

> 一个信源有哪些成员，这次选了哪些，在实际请求模型时能提交哪些，提交后留下什么阅读或跳过事实？

它用普通函数提供下列能力；函数名可在实施中调整，责任不得再散回调用者：

- 入站：记录 arrival、关联当前原事件、激活未读；
- 查询：返回信源状态和可选未读成员；
- 安排：选择 `take`、`mentions`、`pull` 或 archive 成员，登记当前 turn 的普通请求 dict；
- 兑现：provider 边界逐条提交 input，返回投影、已兑现数量、剩余成员与明确失败；
- 跳过：以 `mark_read` 事实推进未读，不产生 input；
- 补回：启动前准备、启动后恢复和主动 `fetch`。

reader 不拥有模型 client、工具 registry、QQ 子命令、计费、通用 continuation 或另一个
ReaderSession。当前 `chat.unread_members` 保留为文档化的 Python 入口，但只转发到 reader，不保留第二份实现。

验收 reader 真正“变深”的条件是：

- `meta` 只需知道“安排阅读”；
- provider 只需知道“兑现下一份计划”；
- 两者都不知道 arrival 与 `page/offset` 怎样合并、怎样吸收重复、怎样构造来源桥。

## 4. 拆分与切换阶段

### 阶段 A：先修正 oplog 持久写入边界

**当前：** `_append` 只做部分校验，落盘后 `_apply` 仍可因正常语义条件拒绝事件。

**目标：**

1. 在同一把 oplog 锁中完整解码和校验当前事件；
2. 校验成功后才序列化、追加和 fsync；
3. 落盘后只应用已校验事实；
4. 写入、fsync 或落盘后应用异常均闭锁后续写入与行动，留给重启恢复。

可用一份普通 state dict 代替 `_apply` 的长参数列表，但必须先减掉无用索引和重复校验；不能只把
十七个容器塞进一个 dict 就宣称复杂度消失。可选私有文件 `_oplog_state.py` 只负责重放规则与派生状态，
不成为新存储权威。

**此阶段不改磁盘格式。**

### 阶段 B：reader 接管完整阅读路径，同时删除 Mailbox 未读镜像

**当前：**

```text
router -> chatlog/history -> oplog arrival -> Mailbox entry
take   -> oplog input -> Mailbox absorb/trim
source/archive -> 同时校对 Mailbox 与 oplog -> 写 oplog -> 修 Mailbox
```

**目标：**

```text
router -> 同窗口锁内写 chatlog/history -> oplog arrival
take/source/archive/mark_read -> 同锁内校验并提交 oplog
所有未读查询 -> oplog 派生状态
```

同一个可启动提交同时切换全部消费者，删除：

- `MailEntry`、`Mailbox`及 `_entries/_base/_read/_absorbed`；
- watermark、修剪与邮箱状态对象表；
- `pull(project)`、`absorb(commit)`、`commit_recovered(project)` 这类“写权威后修镜像”协议；
- `WindowTurn.mail` 以及默认用 Mailbox 红点判断续轮的分支；
- 从 Mailbox 和 oplog 各取一遍再合并的查询。

保留或改成更小的机制：

- 普通窗口锁表，且持锁时不请求网络或模型；
- 原事件对象到 arrival 的有界关联，只用于 capture 识别刚记录的事件，不另存“已读”结论；
- 一份 message identity 归一化逻辑；
- “读过再 capture 不重新激活”和“补回命中 live arrival 时一并消费”的现行行为。

Mailbox 本就在重启时从 oplog 重建，因此不需要磁盘迁移。不部署 Mailbox 与 reader 都拥有消费权的
中间版本。

### 阶段 C：收束投影与 meta 边界

1. `meta` 不再解析 source 坐标、排序成员、访问 `chat._private` 或修改 reader 请求队列；
2. `cover_events` 的可见性检查与当前会话投影删除由 `chat` 的一个普通操作完成；
3. reader 与 chat 共用必要的私有投影函数，但不预建通用 renderer 协议；
4. 保留 native O/R、`keep/drop`、结果回调、say 回声闭包与现有消息块顺序。

若只把同样数量的私有调用换个文件名，本阶段不算完成。

### 阶段 D：有版本地去掉 provenance 文本重复

依赖第 6 节的长消息桥裁决。

目标新格式只写一份 `read_by/read_via` 事实；input 自身冻结的正文投影不再烘入同一组标记，
显示时由唯一入口加入正式号、provenance 和来源桥。新 input 的主体投影与桥快照分开保存，不再从已烘好的
XML 样式文本中剥嵌套桥。

兼容规则：

- 旧 input 无 provenance 时留空，不从位置猜测；
- 旧 projection 保持旧内容，不重复添加标记；
- 旧 mark-read 无发起号时显示 `unknown`；
- 旧 notification 无 `ordinal` 仍可重建；
- 不重写旧日志、不重编号、不改 source page；
- `_reference_candidates`、`recall_events`、来源桥和上下文重建必须一起支持新旧格式。

先落地一个能同时读新旧格式的提交，再切换写入新格式；不使用长期 feature flag 或双写。

### 阶段 E：清理与文档收口

- 删除确认无消费者的 helper、索引和旧导入；
- 修正 `meta` 中与当前 R 登记、续轮规则冲突的过期说明；
- 修正本次触及的 runtime/architecture 旧描述；
- 为离线回放保留一个明确的隔离入口，不再继续增加 `_offline_scope/_drive_agent` 私有钩子；
- 旧 `_stream_results` arrival 恢复桥只在聚合数量为零且已无 writer 时删除。不读聊天正文即可核实这个条件。

## 5. 明确不随本重构删除的契约

| 项目 | 本计划的处理 |
|---|---|
| 独立 `.chat` | 现行文档仍将其列为产品入口；本计划保留。维护者已质疑过其实际应用场景，删除应作为单独产品裁决，不夹带在内部拆分中。 |
| native O/R | 保留 `keep/drop` 和 DeepSeek 重建契约，不统一为纯文本。 |
| `pull` | 保留为当前公开别名，并保留 `read_via="pull"`。 |
| 自动 source bridge | 保留；它是最近明确选择，不是可以为净删除行数随手撤销的细节。 |
| `cond` / `call` | 保留动态 `.py` / link / hint 兼容，尊重现有 `WHY:`。 |
| 旧 input / notification / cover / condensed / clear 解码 | 已有持久数据读取义务；旧数据未退休前保留。 |

`_stream_results` 函数是当前正常 R 登记和工具 touch 回调，不能因与旧 arrival 字段同名而误删。

## 6. 阻断阶段 D 的语义裁决

当一条长消息首次只以片段形式进入 input，之后又成为来源桥成员时，当前文档与代码不一致：

- 文档的冻结投影规则意味着：桥应继续展示当时真正读到的片段；
- 当前代码会识别中文“正式阅读片段”/“补回消息过长”提示，从原事件重建全文，却仍沿用旧 input 号。

本计划倾向严格冻结：**旧 input 号只表示当时实际读到的片段；若需全文，应产生新的正式重读 input。**
改变现行代码前须由维护者裁决。此问题不阻塞阶段 A–C。

## 7. 提交、验收与恢复

### 7.1 提交切片

1. oplog 完整写前校验与失败闭锁，不改磁盘格式；
2. reader 纵向切换，同时删除 Mailbox 未读镜像；
3. meta 与投影边界收束；
4. 新旧 projection 同读；
5. 只写新 projection 格式；
6. 无消费者职责、旧说明与条件性恢复桥清理。

第 2 个切片需要同一实现者完整拥有 `context/chat/reader/meta` 的阅读路径；不将两个未读权威分给两个 worker 并行修改。

### 7.2 最小但有价值的验收

- 无效 source reopen、重复 input、非法 skip/provenance 在落盘前失败，日志字节和内存状态不变；
- 乱序 ids 按来源排序，稀疏已读洞、`mark_read` 后新 arrival、补回/live 重合、archive 重读完全一致；
- 普通消息不唤醒，旧唤醒不反复开轮，取消后剩余未读不丢；
- 同批多工具、空 assistant 正文、取消半批、cover 已确认 say、recall 可见性的 provider 消息块顺序不变；
- 新旧混合日志可重放：旧 notification 无 ordinal、旧 input 无 provenance、旧 skip unknown、新 input、跨格式 bridge、
  recall 与 event links 的原号和关系都不漂移。

使用仓库外临时脚本和隔离 runtime，不默认新增常驻测试框架。每个 Python 提交运行
`uv run --frozen python run.py --check`；重要纵向切片和最终版本运行隔离 `--smoke`，与同机改前的
available/optional failures 比较。

### 7.3 部署与回退

- 阶段 A–C 不改磁盘格式，可通过代码回退恢复；
- 首次写入新 projection 格式后，只能回退到已支持读取新格式的提交；
- 不通过删除新日志“恢复”，不在生产数据上运行未授权迁移；
- 部署、重启、读取生产 pending 或删除条件性 legacy 恢复桥前另行确认授权。

## 8. 完成定义

本重构完成时：

1. 未读事实只由 oplog 写入与重放，不再有 Mailbox 可变镜像；
2. live、source、archive 只有一条阅读安排与提交路径；
3. `meta` 不知道 arrival/page/offset 合并、来源桥和预算降级规则；
4. provenance 事实只写一次，新投影不从中文文案推测结构；
5. oplog 不会把正常可预见的语义错误 fsync 后再拒绝；
6. 不靠长期双写、双 reader、Manager/DTO 或删除现行产品契约获得整齐。

粗略估算：`chat.py` 可迁出 1100–1500 行 reader/投影职责，`context.py` 可删除 230–290 行 Mailbox 实现，
`meta.py` 可移出或删除 180–280 行内部编排。考虑旧格式兼容后，仓库总净删除预期约 250–600 行。
真正的验收仍是权威和路径的减少，不是这个数字。
