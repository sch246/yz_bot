# 中心 agent：经历日志、视图与上下文运行

2026-10-31。实施基线为 `79ec37b400f18972a388c2469f146cf8d0c8562b`。本规格吸收 Claude 提案及源码审查，作为本次实施合同；票据记录进度与实际验收证据。

## 问题与交付

当前 `current.json.messages` 同时承担经历记录和工作上下文；dismiss、compress、decompress 修改列表后还要维护 marks、scopes、told 和 last_shown。已读位置又分别存在 screen、segment 和 oplog。多种模型会话分别组装、绑定工具与结算，分身引用主上下文只能重写背景。

本次把经历保存为只追加的日志，把眼前上下文保存为编号视图；画面的展示和模型确认读过分别表达，由一个读状态所有者处理确认与撤回。统一模型执行入口，使每次运行带明确目的、可复用材料引用，开局材料可以由前一段修改。

这是上下文组装的工程基础。目的是否恰当、材料怎样互相作用、怎样从历史中形成新的关注方向，仍是研究问题。本次合成场景验收只证明明确列出的机制。

## 行为合同

- 一个中心主体串行处理主唤醒；同一段在交接前保留追加式经历和前缀。`chat.wake()`、`chat.push()` 的公开签名保留。
- 默认唤醒、命令优先级、`^C` 和子进程取消、私聊正在输入、输入框持久化、先打字后发送与发送等待规则保留。自己的回声只记录、不派发。
- dismiss 保留已发言、新草稿、follow 积压超过 60 条三个拦截；退回被清除画面的阅读确认，保留期间无关的新到达及实际外部动作。
- compress 支持 `#n` 与 `n.m`；不跨唤醒折叠子步骤。原始条目编号和唤醒/步骤地址稳定，折叠不让剩余步骤改号。展开还原原引用，支持嵌套；当次工具调用仍在执行的步骤不能收走。白醒自动 dismiss 保留。
- 交接仍由看过本段的她完成；完成判据、宽限、失败重试、强制归档和每次唤醒最多三次交接保留，落实原 WHY 保护的行为。
- 笔记、self 写入时机、夜间反思、月年汇总、旁观助手、面包屑、普通文件策略和 Bot 固定权限保留。
- 普通离线补回只进入历史，不因此增加未读或唤醒；离线期间叫她的消息必须可见、可唤醒，即使其时间早于已确认位置。
- Bot 全局工具激活跨交接与重启保留。每个运行的工具绑定、schema 顺序和显式授权独立，fork 不改变主运行的工具或唤醒状态。

## 日志与视图

`data/chat/journal/YYYYMMDD.jsonl` 是内容权威；条目有稳定 `YYYYMMDD-N` 编号、时间、上下文归属、种类、原样模型消息及来源元数据。JSONL 保留精确协议数据，可读查询由 `read_actions`、`read_context` 提供。旧正式编号保留，新分配避开迁移的编号。

`data/chat/view.json` 保存当前条目编号列表及不能推导的运行统计；归档 `data/chat/views/<ctx>.json` 保存冻结视图。笔记版本/名称、展示记录、工具状态、唤醒和步骤从条目推导，不另存可写镜像。归档名和折叠名可用于已有读取与搜索接口。

条目至少表达 wake、screen、note、system、assistant 行动、逐个 tool 结果、fold、drop，以及只用于迁移的旧归档。逻辑 act 是 assistant 与按调用 ID 关联的结果，不要求它们同一时刻落成一行。

输出记录先于工具执行；每个工具完成后立即追加结果。当前轮写入失败停止继续派发工具并报告，下次独立唤醒可重试。取消或崩溃后，已完成结果保留，未执行与结果未知可辨识；缺失响应在模型投影中补中断说明，恢复不自动重跑外部动作。取消 oplog 的全局熔断，不承诺跨外部服务的恰好一次执行。

日志写成功后才把引用加入视图；视图采用原子保存及恢复保护。`#reasoning drop`、compress 参数缩短只改变渲染，不改原始内容。普通追加维持旧消息为新请求前缀。

跨切片接口（普通 dict 和函数；实现可补充明确参数，但不能另造状态所有者）：

```python
journal.append(kind, *, ctx, messages=(), **metadata) -> str
journal.get(item_id) -> dict
journal.begin_act(ctx, assistant, **metadata) -> str
journal.record_results(act_id, results, *, complete=False) -> None
journal.resolve_act(act_id) -> list[dict]

segment.render(view, *, reasoning="keep") -> list[dict]
segment.derive(view) -> dict  # notes, screens/shown, tool state, wake/step addresses
segment.resolve_refs(view, refs) -> list[str]
segment.fold(view, first, last, summary) -> str
segment.unfold(view, fold_id) -> None
segment.dismiss(view, wake_id) -> dict  # 返回读状态恢复材料，不调用 screen
```

`fold.covers` 保存原条目引用；drop 记录移出的编号。只有一个折叠区间操作；整轮和子步骤只是地址解析。折叠后不可见笔记、工具状态按推导结果补齐。

## 注意力与读位置

已确认阅读只有 storage 的 `read` 一个所有者。它可以包含连续位置、同秒身份和未确认的例外记录，不限制为标量时间。计数、已展示边界由该状态与视图推导或缓存。

屏幕准备只返回画面、记录身份与待确认 patch；首次模型输出才提交该次请求实际展示的记录。请求失败释放待唤醒认领，不吞消息；生成期间新到的记录不会被旧 patch 清掉。展示身份从视图读取，防止同一次失败重试重复追加。dismiss 只退回对应范围，不能把无关的新到达恢复成旧快照。

```python
screen.prepare(window, *, shown, count, pending=()) -> dict
screen.commit_read(patches) -> None
screen.restore_read(snapshot, windows) -> None
screen.read_snapshot() -> dict

attention.on_record(window, event, *, recovered=False) -> bool
attention.enqueue(window, *, kind, reason="", record_ids=()) -> bool
attention.claim() -> list[dict]
attention.ack(claimed) -> None
attention.release(claimed) -> None
attention.schedule(kind, due, *, window=None, reason="", **state) -> dict
attention.rescan() -> None
```

attention 收拢覆盖层、等待、循环、防抖和待处理队列。一个计时线程等待最近期限，状态变化可唤醒重算；它通知中心有工作，不自行另起模型会话。队列 claim 不是删除，模型输出确认后 ack，失败 release。消息身份、窗口、作者和理由保留。

loop、wait 只在内存，重启丢弃；wait 在循环外也保留原理由，在循环内推迟下一轮。follow、mute、on **继续持久化**，由 awaker 文件提供权威，内存只作执行投影；已有 other 覆盖函数仍只在内存。every、daily、at、when 保留每分钟检查。

启动和补回后恢复未确认的有效召唤。补回必须交付新增记录身份，不能只扫描时间水位之后。普通离线记录排除、旧时间召唤与同秒新消息都要有可恢复的解释。去掉 `#loop` 总开关、旧独立 timer 和 debounce 文件；循环历史改查日志，旧历史查询能力可以由该入口提供。

## 运行、分身与开局

```python
run(items, goal, *, tools=(), model=None, kind="fork", view=None,
    stop=None, timeout=None) -> tuple[str, str]
```

统一主唤醒、交接、反思、旁观、汇总与分身的 LLM 构造、记录、计费、工具绑定、超时和取消。goal 非空并出现在请求末尾；它描述本次要处理的问题，来源可为唤醒理由或调用方明确任务。各角色保留必要的工具、提示和限制。

主唤醒状态为普通 dict，以执行上下文隔离。`chat.current_wake()` 仅在该主运行及其工具执行中返回 dict；并行 fork、observer、summary 不继承。结束通过 `wake["end"]`，删除替换 `turn_done` 回调以及 session 动态口袋属性。`^C` 从该状态取关联窗口。

`fork(goal, refs="", tasks="", tools="", model="")` 替代 assign_tasks。refs 逐行支持唤醒/步骤地址、笔记名、ctx 和日志编号；引用内容复用，分身结果可追溯至独立 ctx，最多五个并发任务。默认不可调用 send/type_word；显式授权可以提供，授权只属于本次运行，也不能经 load_tools 重新默认取得被排除能力。这是工具暴露规则，保留原宿主 op 信任边界。

开局策略在 `data/notes/policy/opening`，逐行引用。未创建时默认 global、recent、最近七篇日记、年表、目录；self 和系统说明仍由系统提示提供。前一段可在交接时改文件，下一段采用；坏引用给出可定位错误，不静默丢失。人群首次出现笔记、当天日记新增/跨天刷新保留。目录 60 份和人物 20 份截断都明确报告遗漏数量与检索入口。

## 工具与压力

工具 registry 使用模块级 last-good 字典和函数，绑定用每次运行自己的 dict；ContextVar 保留工具调用期间的归属和后台线程传播。删除 ToolRegistry/SessionBinding 类、地址拼接包名、_deferred 与单独 child provider。保留 Python/Markdown 二合一、顶层目录、显式 reload、错误栈、last-good、磁盘变动提示、Bot 权限过滤、开局空闲回收、卸载留桩、恢复入口。

工具 worker 交付 `bind_session(...) -> dict`、`current_binding() -> dict` 与显式接收 binding 的模块函数；保持原方法职责的函数名（load/reload/unload/restore/reconcile/state_text 等，以其交付 API 清单为接线依据）。共享 catalog，绑定 active/schema 不共享。主运行 active 写回 Bot 全局设置，schema 顺序在视图内；fork 使用独立状态。

`pressure(view, stage)` 集中原有长度、增长、宽限、失败等判断，返回无动作、折叠提醒、交接提醒、交接或强制归档。tidy.py 仍可现场编辑。去掉 subconscious 命名与开关，其增长提醒和状态合入 pressure 与 `#status`。

## 迁移与切换

提供显式指定离线 runtime root 的一次性转换脚本，默认只读报告，显式 apply 才写入。先检查源和目标、备份并校验完整转换，再切换；失败不覆盖原数据，重复运行不产生重复条目。运行时只走新路径，不保留旧存储回退。

转换 oplog 行动、current 和归档、seen/last_shown/未确认召唤、工具设置以及规则。保留正式编号、原消息、note 来源、wake/草稿快照、screen 身份、fold 关系。无法可靠恢复的元数据明确报告并阻止有损应用，不凭文本猜造确信。follow/mute/on 保留，wait 删除，loop 运行态清空。旧目录归档到 legacy，旧 runtime 公开名只做定位报告，维护者处理现场代码。

迁移必须同时验证消息 JSON 相等、派生元数据对应，以及转换后首次追加/折叠/展开/dismiss/再唤醒。工具原始输出与结果不重复写多份；旧档案不能可靠去重时保留其来源并报告。

在集成分支完成原子切换，中间票据不承诺独立部署。现有主工作树未提交的 CLI 等改动保留，不纳入本基线。真实停机、数据迁移和启动属于后续部署，当前只完成可审核代码与合成数据验收。

## 验收

基线 `--check`：146 Python files、88 public modules；基线 `--smoke`：86/88，optional load failures 为 mcf、minecraft。每个 Python 交付跑 check；集成运行 smoke 并比较。片段因接线未完成导致 smoke 缺项，记录为待集成，不能当作通过。

仓库外脚本验证：

1. 稳定编号、并发追加、原子视图保存、已有引用不悬空；两次普通追加保持消息前缀。
2. dismiss 三个拦截与阅读回退；整轮/步骤/嵌套折叠展开、来源推导、参数/思考仅投影。
3. assistant 落盘先于工具；第一个工具完成而第二个取消/写失败时保留结果，不重放动作。
4. 同秒后到、旧时间点名、普通离线补回、生成期间新消息、请求失败与重试、dismiss 后未读恢复。
5. 默认触发器、mute/master、follow、on/wait 认领、防抖、秒级 wait 理由、loop 停止和重启语义。
6. 各类 run 的目的、计费/归档和取消；fork refs、默认禁发言、显式授权、并发隔离。
7. opening 编辑生效、坏引用报告、日记刷新和两个截断提示。
8. 工具加载/重载/失败保留/空闲回收/卸载留桩/权限和全局激活跨交接。
9. 合成旧数据迁移：显示与下一步行为，dry-run 不写，失败保护与重复运行。
10. 全库旧路径引用检查；文档 diff check 与相对链接检查；最终行为文档与新实现一致。

不增加永久测试框架。实际验证结果记录在票据及整体验收文档，计划不是通过证据。旧提案保留历史位置并标明被替代，避免丢失 WHY 来源；可选的组装效果统计不在本次必交范围。

## 来源

- 用户本轮明确要求 `$to-spec`、`$to-tickets` 后子代理实现，未决项用 `$ask-matt`、`$grilling`。
- [Claude 提案](../../proposals/chat-core-rewrite.md)，提交 4e0f657、79ec37b；本规格覆盖其中读位置、行动持久化、工具激活及短期规则的差异。
- [设计原则](../../../design-principles.md)、[交互模型](../../../interaction-model.md)、[运行架构](../../../architecture.md)、[运行边界](../../../runtime.md)、[LLM](../../../llm.md)。
- [上下文与通透讨论](../../proposals/context-prediction-and-permeability.md)，以及用户关于“组装上下文的也是上下文”的后续澄清。
- [D01：短期规则持久化](decisions/D01-裁定-follow-mute-on-的重启语义.md)。
- 源码审查基线 79ec37b：screen.py 的离线调用与输出确认 WHY、agent.py 的先记输出后执行、llm 中取消结果保存、segment._repair 的中断投影。
