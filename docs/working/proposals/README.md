# 设计提案与迁移记录

本目录同时保存仍未实现的提案和已经完成的架构迁移记录。当前事实只能由 `docs/architecture.md`、`docs/interaction-model.md` 与 `docs/runtime.md` 断言；完成项用于解释取舍和旧能力去向。

**先找方向：**[中心化聊天软件阅读计划](chat-client-reading.md)是 2026-09-24 新选定的下一实施方向；[压缩与消息模型交接](condense-and-unify-handoff.md)保存此前主线与已落地部分；[统一信息流实施记录](unified-event-stream.md)区分现行接线和未完成的压缩树；[方向索引](long-term-roadmap.md)单列其它长期计划。当前运行行为仍以正式文档和代码为准。

- [新代码架构与一次性切换](code-organization.md)（已完成；调查摘要见[旧代码组织现状](../code-organization-analysis.md)）
- [Mods 两阶段加载](mods-loading.md)（已实现）
- [Mods 模块手册](module-manual.md)与[源码覆盖索引](source-coverage.md)（迁移记录）
- [Storage 加载校验与历史恢复](storage-and-history.md)（热同步已实施，剩余部分仍是提案）
- [统一消息模型：窗口、正文与双向转换](message-model.md)（已实施；行格式协议已移入[chatlog 记录格式](../../chatlog-format.md)，运行事实见[运行架构](../../architecture.md)，本篇保留取舍与被排除的做法）
- [重启后的离线消息恢复](offline-message-backfill.md)（未实现；NapCat 历史可作为主动旧档阅读及停机缺口补齐来源，真实接口的身份和分页语义尚待验证，不默认重放入站行为）
- [数组消息格式迁移](array-message-format.md)（长期方向，尚未切换 NapCat 或迁移 CQ 字符串消费者）
- [LLF 纯文本工具调用迁移](llf-tool-protocol.md)（长期方向，尚未替换原生工具调用与结果协议）
- [可自维护的同相上下文](self-maintaining-context.md)（未实现；记录长期记忆、外显状态转移与自修改的北极星语义及证伪条件；[讨论来源与演化索引](self-maintaining-context-discussion.md)保留原话与修正链）
- [检索索引：把子代理找过的路存下来](retrieval-index.md)（未实现；缓存子代理的检索路径而不是结论，失效判据由模型自写的校验命令给出）
- [统一信息流：读时编号与完整输出事件](unified-event-stream.md)（已提交读时编号、受限首层覆盖和反查，最新代码尚未部署；长期退休暂缓，不要求 LLF）
- [压缩、遗忘与消息模型：交接与待办](condense-and-unify-handoff.md)（索引，不是提案；跨 chat-condense / message-model / self-message-echo 三份，回答「走到哪、下一步、谁在等谁」，未决项各自标明卡住谁）
- [压缩与反查：现状、目标与历史](chat-condense.md)（当前机制与已定中心化方向；早期 `condense_ops`、`floor` 和退休推导另见[历史归档](chat-condense-history.md)）
- [中心化聊天软件：最近预览、顺序拉取与逐页积累](chat-client-reading.md)（当前实施计划，尚未实现；首阶段直接单一 agent、全局 hint、明确发送目标与费用归属）
- [给 agent 做一个聊天软件：主观时间轴，以及它推翻的东西](chat-client-for-an-agent.md)（保留两条时间轴的论点和需复核的推论；新的实施顺序见阅读计划，经历如何遗忘仍未解）
- [mail 与激活：把「谁进上下文」和「什么让它跑」拆开](mail-and-activation.md)（待办 3b 的第 3 步主动跳过，第 6 步已提交，第 7 步等异步回调消费者；现行整段排空将改为有界 FIFO 拉取，最近页由不消费未读的 `peek` 预览）
- [私聊窗口的身份](window-identity.md)（已实现；`user_id` 是作者、`target_id` 是那条私聊，「谁发的」与「发到哪」不再共用一个字段）
- [自发消息回流](self-message-echo.md)（已实现；「Bot 说过的话」的写入权威从 `get_msg` 回查换成 `message_sent` 回声，`bot._route` 的自发消息关卡随之改成「记录、不派发」，`record_sent` 删除；留一条未决：同账号别的客户端发的话算不算模型说过的）
- [异常、日志与 link 报错节流](errors-and-logging.md)（按严重程度分）与[日志分流：流的身份与出口](log-streams.md)（按流的身份分；两者正交）
- [消息表示与 at/reply 解析](message-representation.md)（调查；§2 列出的五个问题已按其中 A 方案的前半修掉四个半，本篇按节标注了现状，未做的是模型侧的结构化渲染）
- [权限演进](permissions.md)（部分已实现；**模型工具已按 Bot 自身固定权限运行**，不因阅读消息改变；「代行」仍按被代行者的权限，其余演进方案尚未实施）
- [多项目共存](multicore.md)
- [模型选择的宽松解析与在线模型列表](model-selection.md)（已实现；未登记的模型照常调用，`#models` 先问对端、取不到才用本地配置）
- [op 工具集与事件注入](op-toolbox.md)（未实现；op 专属工具集：把事件交给唯一事件循环的注入原语，以及「开一轮聊天」的续话原语——`.reboot chat` 是后者的第一个消费者）
- [窗口级 `#hint`](chat-hint.md)（已实现；给聊天窗口存一段可配置代码（本质是文本管理），聊天循环停止时求值并发送——用于显示已用上下文这类运行状态，也让用户看到循环停没停）
- [浏览器、计算机使用与远程操作](browser-computer-remote.md)（未来支线备忘，不是当前提案；保存已有家底、外部调查入口和以后进入实现前需要验证的问题）

剩余提案实现后，应把已验证事实移入现行文档，并把这里改成简洁的决策记录；不要让提案与现行文档长期维护两份运行契约。
