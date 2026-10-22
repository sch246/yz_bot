# Hindsight、Cortico 与 meow-memory：持续主体的参考补证

日期：2026-10-20。补充 [D17](decisions/D17-重审主体持续性与自主选择的目标和实现方向.md) 及[首个原型路线](subject-loop-reorientation-20261020.md)。三个只读代理定向查公开源码，主代理亲读相关设计、核查关键实现并整合。以下区分代码事实、作者主张与工程推断；没有安装或运行这些项目，没有读取本机插件配置、会话、生产材料或凭据，也没有调用模型。

## 先改变什么判断

这三个项目都超出了“把对话总结后存起来”。它们分别提供可修订知识、持续事件循环、原代理自写记忆的具体机制。我们应先复用这些已有机制的经验，再决定柚子缺哪一小段；没有依据把整个循环从头设计一遍。

维护者确定的目标仍是“有自我的主体”“让她自己决定”。研究没有发现任何一个项目已证明这个目标成立。这里的参考重点是主代理的工程判断：**Cortico 首先参考跨环境统一事件输入、共享认知循环和模型输出流分发；Hindsight 参考来源与知识修订；meow-memory 参考代理写入怎样真正进入未来上下文。** 不据此决定安装某个产品或搬用整套架构。同日维护者指出“统一事件流和输出流”后，主代理重核契约与执行路径，校正此前只突出持续运行／再醒的片面概括；先前表述及校正历史保存在 D17。

| 项目与本次固定版本 | 已实现的持久改变 | 无新来信时怎样继续 | 当前最值得借的部分 |
|---|---|---|---|
| [Hindsight](https://github.com/vectorize-io/hindsight/tree/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2)，`c5f585f` | 抽取归属明确的事实／经历；后台修订 observation；刷新持久 mental model | 按配置整理知识、刷新指定问题；本次未查到 bank 自身的开放行动循环 | 经历归属、修订来源、失效与刷新水位、失败保留旧内容 |
| [Cortico](https://github.com/Pal-AI-Lab/Cortico/tree/469f496af8ed9c113d2743db229589b16315ad34)，v0.2.4，`469f496` | Persona 读写文件工作区；部分实现另有后台 Dream 改写 | 核心接受多环境事件；Soulmate 另有背景节律和模型自定再醒 | 统一事件／输出契约与共享主循环；保留来源、工具行动与实际结果；再醒是其中一种事件来源 |
| [meow-memory](https://github.com/Phant0Meow/dsh-meow-memory/tree/033df8589467bbe17a2c40dc864283b960795ec5)，v0.31.0，`033df85` | DSH 模型直接写改 SQLite，含自身条目；以后注入、检索及压缩恢复消费 | 插件在满足条件时给原会话派发 reflect／dream 整理轮 | 可拒绝的维护轮、自写自改、明确的后续消费路径 |

表中结论的具体读写、调度和限制见下文。它们不是三种互斥的“记忆算法”：Cortico 是 agent harness，Hindsight 是可接入 agent 的记忆系统，meow-memory 是 DSH 插件，替换范围不同。

## Hindsight：知识会改变，但当前 reflect 不落库

**事实。** 当前 retain 将代理实际做、试、学、决定过的内容映射为 `experience`；用户偏好、用户规则等属于 `world`。抽取提示还按 narrator 与转录上下文判断第一人称归属。因此它可以记“她曾尝试什么”，但输入归属不清也会污染自传；抽取出的 world 仍是有来源的主张，不能自动当作外部真相。[事实抽取源码](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py#L1097)、[视角判定](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/retain/fact_extraction.py#L1855)。

持久学习主要发生在 consolidation：事务内新建、更新或删除 observation，关联 source facts，更新检索索引；可记录修改前历史。没有合法来源的创建／更新会被拒绝。但同一事件怎样合并、何时被反证，仍依赖模型及提示，并非形式化真值维护。`proof_count` 只是去重来源数，不是独立证据数量、可信度或偏好强度。[提交事务](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py#L2850)、[更新指引](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/consolidation/prompts.py#L39)、[来源计数](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py#L3861)。

mental model 是调用方选定问题后保存的答案，可按配置随新证据刷新，供以后 reflect 优先检索。刷新失败保留旧正文及旧水位；它不等于“文件已经存在就永远正确”。指定问题也可以来自外部 agent，但这个选择不是 bank 的后台刷新自己产生的。[官方 mental-model 文档](https://hindsight.vectorize.io/developer/mental-models)、[刷新落库与失败处理](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L18770)。

**版本纠正。** [论文 v1 §3、§5](https://arxiv.org/html/2512.12818v1)描述四类记忆、带置信度的 opinion 及反思更新观点；当前迁移已删除 opinion 和 `confidence_score`。当前 `reflect_async` 明确只读取并综合答案，不保存改变，工具也以检索为主。不能把旧论文的观点更新机制倒写成当前 reflect 的能力。[删除 opinion 的迁移](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/alembic/versions/g2h3i4j5k6l7_remove_opinion_fact_type.py#L31)、[当前 reflect 实现](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L15613)。

mission、skepticism／literalism／empathy 由调用方设置并影响提示；本次核查路径没有“新经历自动改这些参数”的实现。知识更新确实存在，不能因此推成她已自行决定价值或目的；外部主体可以另行调用这些 API，但那是需要明确的主体机制。[disposition 更新入口](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/memory_engine.py#L15324)、[反思提示装配](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/engine/reflect/prompts.py#L137)。

**对我们的推断。** 优先借“实际经历→有来源、能改写的理解→后续选择使用”及失败保留机制。不要把用户偏好写成她的偏好，也不要把抽取、反思答案、持久修改混成一个动作。这里没有核验全部 integrations，不能据局部 bank 源码否定外部 agent 能形成行动闭环。

## Cortico：统一事件输入与输出通道，各 Persona 的认知安排不同

**核心结构补证。** 多个 World 用共同 `EventEnvelope` 与 `pushEvent` 送入事件库、总线及主认知循环，保留 source、type、时间和全局 cursor。外部事件可共同装进 `external_event_frame`，也允许配置为 user 消息；统一的是跨来源的存储／入队与上下文承载，不保证现实发生时间严格排序。[事件写入](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/core/core.ts#L481)、[共同事件帧](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/core/loop.ts#L839)。

输出侧有统一 `StreamEvent`／`OutputTap` 契约，包含增量、轮末与中断；Core 不解释其语义。Cormini 合并挂载 World 的接收器，将同一模型输出流扇出；这是具体 Persona 的装配，框架提供可选 tap 接口。另有 World 工具执行行动、回执进 session：例如 QQ 发消息调用平台后显式保存 `qq.self`、返回发送回执。这不意味着所有环境自动发出模型正文，也不把事件档案、输出增量、工具日志混成一个耐久双向流。[输出接口](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/core/types.ts#L446)、[多 World 扇出](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/cormini/persona/persona.ts#L707)、[QQ 发送与回录](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/worlds/qq/world.ts#L1500)。

**事实。** 核心按事件批次驱动模型和工具，把 assistant 输出与工具回执写入 session。没有新来信仍可继续工具循环；无工具调用、结束工具或轮数上限可以结束。Cormini 的主 session 持久；它的 Memory 是实际文件工作区及 Git 支持，并非只有一个空接口。不能把“每次再醒”误解成“每次重新创建上下文”。[核心循环](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/core/loop.ts#L1343)、[文件 Memory](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/cormini/persona/memory.ts#L194)、[持久主 session](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/cormini/persona/persona.ts#L666)。

Cormini 背景 tick 默认关闭；Soulmate 的背景昼夜节律来自代码配置。与此不同，Soulmate 暴露 `schedule_wake`，模型能自己选择时间并写下次醒来的 note，也能暂时阻断实时事件投递。定时器落盘，重启恢复；但到期先删除条目再执行回调，不保证崩溃时恰好一次送达。存在这些能力不等于已观察到稳定兴趣。[模型再醒工具](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/rhythm.ts#L43)、[定时器持久化](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/src/core/timers.ts#L20)。

Soulmate 把 worldview、常驻 memo 等装进前缀，其余可按文件索引主动读取。文件写入立即落地，前缀快照到下次交接才刷新。其 main 可改 memo／note，但不能改 constitution／worldview，people 只能追加；后台 Dream 才有更广写权。这是具体 Persona 强制的认知分工，不是整个 Cortico 的必要条件。[装配内容](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/memory.ts#L40)、[写权限](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/permissions.ts#L41)、[前缀刷新说明](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/tools.ts#L163)。

Soulmate 交接后给后台 Dream 一个上下文 fork；Dream 可依据轨迹修文件、向主线程浮现内容，提示包含承诺结果与行为反馈。它是真正的可写过程，但独立证据判断主要靠提示。Dream 队列只在内存，fork 不持久；中途已写文件会留下，未完成任务不自动耐久接续。串行 Dream 队列也不能推出它与 main 共享文件之间有版本冲突保护。CortiV 另有实现，不能混用其重试保证。[Dream 队列与执行](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/subconscious/index.ts#L79)、[整理指引](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/corti-soulmate/persona/subconscious/prompts.ts#L15)。

**作者主张。** [PHILOSOPHY.md](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/PHILOSOPHY.md)强调最少干预、可不行动，并提出 Memory 与人格的关系。它提供了很接近本项目的问题意识；“换模型后仍保持身份”等主张没有在本轮取得验证，不能作为已经成立的身份理论。

**对我们的推断。** 更基础的借鉴是让多个环境成为同一主体的经历来源与行动／表达通道，共享认知循环；再醒给这条循环增加一种事件来源。先核柚子现有多窗口、行动回执及定时机制已统一了什么、还缺什么，再判断原型切片。持续主体不必每轮先做总结，应具备能行动、静默、留下再醒线索的实际通路；不预先照搬四层类结构，也不把“清醒时不能改核心自我、只能交给 Dream”当成维护者已选边界。维持一个 session 或改了自我文件，均不足以证明持续主体。

## meow-memory：原代理真能改自己，但 dream 不是冻结事务

**事实。** `soul/user/project/fact/lesson/topic/rules` 是七类存储，不是七个代理或固定认知阶段。工具允许模型写入所有类别，并更新正文、状态、重要度等；包括 soul，没有查到它独有的写入批准门槛。因此不能把它描述成“只有维护者预写的人设”。本次没有查看任何实际安装的 soul。[分类与用法](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/prompts/zh/system-guide.md#L4)、[写工具](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/tools.ts#L191)。

首轮自动注入 active soul／user 及满足条件的全局规则，附检索说明与全部项目名，不列 topic 标题；后续对部分类型按关键词匹配，全局加当前项目默认取 top-2，也可主动 search／read。压缩后重建快照、查过的项目、本会话写改过的条目。这条写入→未来消费的接线是实物；不是向量召回，也不意味着每一条已写记忆一定会进入以后每次请求。[首轮注入](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/inject.ts#L252)、[实际导引](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/inject.ts#L278)、[后续匹配](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/inject.ts#L522)、[设计说明 §5](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/docs/%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E.md)。

2026-10-21 更正：上段原先写“附项目和 topic 导引”，沿文件开头旧注释作了错误概括；同版本执行代码仅列项目名，原表述撤回。[长期选材补证](long-term-material-selection-20261021.md)另指出常驻全文／目录数量和候选扫描并非天然有界，不把工具结果 top-k 当作总资源保证。

reflect 默认在连续工具步骤达到门槛、轮次停止时安排；它允许新增、纠错、归档，也明确可以决定无需记忆。dream 可在无新来信的空闲期恢复原会话，分原子条目、topic、项目总结几组整理。两者通过同一写工具直接改变持久库；它们与 Hindsight 当前只读的 reflect 含义不同。[停止钩子](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/index.ts#L1581)、[reflect 指引](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/prompts/zh/reflect.md)、[梦的组别](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/dream.ts#L112)。

**执行与版本纠正。** 当前不 fork 另一个代理：reflect／dream 首组优先在原 agent 上 `followup()` 新 turn，缺能力才 `steer()`，后续组用 steer。设计说明笼统描述为 steer，早于这段源码。它和 Cortico Soulmate 的后台 fork 不是同一种“梦”；回原会话也只是接入机制，不能据此判定身份连续已成立。[派发实现](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/dream.ts#L308)。

**并发与回退边界。** dream 带持久租约与组进度，CAS 防重复启动／推进；但启动和每组推进会重新读取最新条目，范围包括本窗口创建、读到／注入过、写过的条目。`T` 通常取窗口最后正常活动时间，缺失时取当前时间；它不限制读取版本，`snapshot()` 也只读当前列表。收尾仅给本窗口创建的条目取 `MAX(updated_at,T)`，保留较新时间戳，但不能阻止跨窗口正文覆盖。[时间戳实现](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/db.ts#L720)。条目 update 没有 expected-version 条件；源码明确把工具乐观锁留待后续。任务租约不能替代 T12 的笔记版本冲突保护。[分组读取](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/dream.ts#L112)、[推进重读](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/dream.ts#L372)、[条目更新](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/db.ts#L380)。

各次工具写入直接落库，没有查到整梦候选／原子提交或条目多版本回退。dream_log 的收尾数据不能构成完整 before／after 撤销记录；可选备份默认关，开启也在收尾生成单个轮转快照，非开始前保护。进程中断后，已完成部分组的任务会封存，不保证继续剩余组；停止也不撤销已经写入的内容。[中断封存](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/dream.ts#L590)、[备份默认值](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/defaults.ts#L44)、[备份实现](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/db.ts#L788)。

**对我们的推断。** 很值得借“代理可选不记、纠错、自改，并把实际改变接回未来上下文”。但插件调度给的是预定记忆维护任务；本次没有查到它自行选择探索外界、休息或推进任意目的的通用行动循环。`todo/goal` 字段不等于行动调度。这个结论限于插件，不否定 DSH 宿主的其它能力。七分类、空闲分钟数及作者的峰时抑制不是柚子必须采用的认知结构或当前价格依据。

## 对首个原型的具体补充

以下是工程建议，不是三项目已经替我们验证的结论，也不改写现有生产契约。

| 原型问题 | 先复用的经验 | 必须留下的证据 |
|---|---|---|
| 不同环境是否成为同一主体的经历与行动通道 | Cortico 的统一事件契约、共同上下文、输出流接收器及工具回执 | 标清来源与顺序、输出送往哪里、实际生效或中断了什么；先核现有小机制，不预建 World 类层次 |
| 无新来信还能否自行继续 | Cortico 的自定再醒时间与自己的 note；复用现有事件、时钟和终态小机制 | 模型实际选择是否再醒、为何留下线索；到期可见什么；允许不安排或静默 |
| 经历改变了什么 | Hindsight 的归属／来源；meow 的自写与无需记忆选项 | 区分外部事实、用户偏好、她当时的理解、实际行动及回执；不能由脚本预写改变内容 |
| 改变以后有没有用 | meow 的注入／压缩消费，Cortico 的前缀刷新时点 | 后一醒实际请求加载了哪个版本、还主动读了什么；写入成功不代替消费成功 |
| 何时修正或放下 | 来源与失效、真实环境反馈、已有笔记版本链 | 后续反馈独立于模型自评；保留、改写、搁置都可能合理；保留旧版及失败证据 |
| 主线程和整理怎样相处 | 对比同 agent 整理与后台 fork 两种接法 | 不要求每醒必跑三角色；若有并发，核对版本冲突、部分写入和中断恢复，不能拿任务租约冒充笔记 CAS |

不新增一套人格审批器来判断“这是不是她自己”。原型先让整个持续系统产生内容、使用内容并承受后果，再用局部干预诊断一条联系；仍不能从单次行为或自述推出主观意识。也不因这些参考有名为 soul／constitution 的文件，就先把兴趣和价值清单写给她。

当前取舍是补强 D17 已定路线，不推翻它。T10／T11 的机械成果保留；T12 的混合 role／note alias checkpoint `5b82cd2` 仍未验证、未集成，已有来源／版本／回退边界不能因照搬插件而丢失。T13 仍是草案，先对照上述差异核实实际接口，再划最小原型切片。本研究不把旧三角色草案直接置 ready，也不重开旧新四臂收益对照。

## 当前选型建议

维护者询问“选什么持久记忆方案和统一事件流好”。主代理的明确推荐是：**第一版保留现有 Python 宿主，补齐 Cortico 式统一事件／输出语义，采用代理可直接维护、带来源和版本记录的文件记忆工作区。** 随后[D18访谈](decisions/D18-确定统一事件与文件记忆的首版职责边界.md)中维护者实际选择“保留 Python，补齐机制（推荐）”及“主循环可主动修订，后台也可整理（推荐）”。两类内部过程共用来源、版本和提交校验，不强制主循环等轮末，也不把后台身份等同terminal前置。宿主和修订发起职责已定；具体非终态截止、工具接线和实际消费仍待验证，不扩大为整套权限或生产切换许可。只读架构顾问复核支持上述推荐，并指出下述代价与改变推荐的条件。

统一应先接上现有 reader、阅读事实、工具结果、say 和 later 缺失的连接，标清来源、顺序、行动关联与实际回执；不把每个 Python 调用都改造成总线事件，不再造一套 session／调度器／状态库。统一输出的目标与结果，也不等于把正文广播给所有设备。理由来自[设计原则](../../design-principles.md)：普通函数、文件、现场修改及完整重启都是产品能力；现有全局 reader 已经提供跨窗口主体入口。若实际缺口要求整体重建认知循环，就须重新比较直接采用 Cortico 与自行维护的总代价。

记忆应允许她主动留下经历、疑问、理解、承诺与自己选择的后续事项，也能不写、修订或放下；用短索引和主动读取让这些内容进入以后选择。原始经历与现在的解释分别保留清楚来源。文件易读改和追溯是本阶段的优点，不能把文件格式当作主体性保证，也不能退回每轮固定总结后全量塞回。T12 联合返工未验证，版本保护仍是待验的具体机制。

第一版不叠加 Hindsight 与 meow-memory 引擎：前者保留为检索／来源修订候选，后者复用自写与后续消费经验，DSH 插件本身不硬接现有 Python 宿主。不是断言文件检索已经最好，也不先仿制 Hindsight 的抽取／图谱／consolidation。若出现“材料确已保存、可发现且读取工具正常，仍因中文转述或跨经历关联反复漏召回”，且同预算的定向验证显示 Hindsight 能修复，应提前接入；不必等数据量变大。材料已读到却没用于选择，则不能据此认定需要更换检索引擎。

这条建议的最大代价是我们仍要承担时间与恢复语义：重启后什么待处理、行动是否生效、修订何时进入下一次请求。隔离原型应验证一条实际经历→自主记录／重访→重启后使用对应版本→继续／修正／放下的闭环；不能用一套更大的框架或模型自述代替证据。

## 费用依据与仍缺的证据

本轮没有模型支出，也没有足够证据给出三者同口径金额或中文效果排名。已知成本环节如下；具体模型、长度、触发频率与工具往返才决定 token 与费用。

- Hindsight：抽取、embedding／索引、后台 consolidation、多步 reflect、mental-model 刷新和可选结构化输出；另有数据库、本地 embedding／reranker 资源。无需生成 LLM 的 recall 也不等于无算力或远端费用。[配置与后端](https://github.com/vectorize-io/hindsight/blob/c5f585f8f8568ed0677eb2e70fe830c4c483e2a2/hindsight-api-slim/hindsight_api/config.py#L1190)、[reflect 文档](https://hindsight.vectorize.io/developer/reflect)。
- Cortico：正常决策与工具回合、背景唤醒、上下文前缀及具体 Persona 的 Dream fork。机械交接文件本身不是额外模型总结；周期与分工会改变支出。[Cormini 交接](https://github.com/Pal-AI-Lab/Cortico/blob/469f496af8ed9c113d2743db229589b16315ad34/bots/cormini/persona/persona.ts#L611)。
- meow-memory：宿主主上下文加 reflect／dream 各组的模型与工具回合，可能受缓存影响。插件打包无需额外向量服务，不意味着没有宿主和模型成本；整理默认沿主模型，也可按请求覆盖。[整理请求模型](https://github.com/Phant0Meow/dsh-meow-memory/blob/033df8589467bbe17a2c40dc864283b960795ec5/src/index.ts#L1615)。

仍缺：中文抽取和冲突修订质量、长时自我漂移、实际选择与独立结果、并发写入及崩溃恢复实测、同口径成本。未复现论文分数，未核验全部集成，更未比较真实生产行为。这些是可验证的研究缺口；当前没有必须让维护者替她选择兴趣或再授权本次公开研究的问题。

公开设计全文和定向源码缓存、原始 URL／hash 清单位于 `/root/work/agent-memory-new-references-20261020/`；代理源码缓存分别为 `/root/work/hindsight-research/`、`/root/work/cortico-read/`、`/root/work/meow-public-033df858/`。它们只有公开文本，不构成另一份票据状态。
