# 方向索引：区分当前主线与长期计划

> **路线索引，不是新的实施顺序。** 当前实施方向及下一决策检查点见[中心化阅读计划](chat-client-reading.md)；[压缩、遗忘与消息模型交接](condense-and-unify-handoff.md)保存已完成项及旧路线来路。下面各项不因列在同一页就互为前置；当前运行事实以[运行架构](../../architecture.md)和[交互模型](../../interaction-model.md)为准。

| 方向 | 当前状态 | 计划与进入条件 |
|---|---|---|
| **中心化 agent，给柚子做聊天软件** | 中心 reader、全局 hint、明确发送目标与五动作信源接口已接线 | [通知、信源与顺序阅读计划](chat-client-reading.md)保留设计理由和真实停机空窗等验收项；[聊天软件的主观时间轴](chat-client-for-an-agent.md)保留理由。自我经历如何遗忘仍未解，不在第一阶段引入退休或物理删除。 |
| **恢复停机期间的消息** | NapCat 自动补回与主动 fetch 已接线，真实停机空窗未验收 | [离线消息恢复](offline-message-backfill.md)记录上游锚点、缺口和顺序约束；控制台展示日志不作权威来源，模型不管理内部请求页。 |
| **放弃 CQ 字符串，改用数组消息格式** | 维护者选择的长期方向，未迁移 | [数组消息迁移](array-message-format.md)承接[消息表示调查](message-representation.md)的 B 路线；生产设备配置和现有字符串消费者不能直接切换。 |
| **LLF 纯文本工具承载** | 可选长期格式方案，非必要前置，未迁移 | [LLF 工具调用迁移](llf-tool-protocol.md)保留文本格式探索；[统一信息流计划](unified-event-stream.md)只依赖输出/输入身份和因果关系，不依赖 LLF，也不以供应商调用／结果配对形状为本体。 |
| **自我实践与记忆反馈** | 回放、检查点、分叉和诊断实验已接线；运行中产生候选改动并由后续分支实际加载的闭环尚未实现 | [可自维护的同相上下文](self-maintaining-context.md#两个必须分开的研究问题)定义“系统上限”与“实践反馈”两问；[长期记忆离线回放实验](memory-replay-evaluation.md)承接首个真实失败重放；[记忆估值与索引演进](memory-valuation-and-index-evolution.md)只提供 MDL、压缩进步与路径观察，不定义理想记忆或阻塞实践。 |
| **让柚子用 Python 查询和审查自己的经历** | 现有 `exec_code` 已提供通用 Python，但事件查询依赖专用工具且 oplog 仍全量恢复 | [可编程经历数据](programmable-experience-data.md)先验证磁盘惰性 `iter_events` 与模型自由筛选；不新增 world/snapshot/manager，查询成立后才逐个退役专用工具，全量缓存迁移另行按消费者推进。 |

**其它已有独立入口，不是这次从正文里新挖出的同类计划：**[浏览器、计算机使用与远程操作](browser-computer-remote.md)是未来支线；[检索索引](retrieval-index.md)、[多项目共存](multicore.md)、[op 工具集](op-toolbox.md)各有独立提案。稳定记录 id、`say` 绑定和覆盖反查已部分实现，其中心化目标见[压缩与反查现状索引](chat-condense.md)，旧决策来路仍在[交接索引](condense-and-unify-handoff.md)。

**其它状态：**分片追加存储与事件式折叠原先服务于全量重建，后者已降级，须先证实迁移仍有必要；mail 的跨窗口读取与主观顺序现在由聊天软件实施计划接管。高度退休和物理删除因 H 未定且尚无存储压力而暂缓；它们的旧来路仍见[交接索引](condense-and-unify-handoff.md#三推进规则与任务关系)，不伪装成当前前置。
